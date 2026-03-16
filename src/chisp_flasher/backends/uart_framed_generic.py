from __future__ import annotations

import os
import time

from chisp_flasher.backends.base import BackendBase, LogFn, ProgressFn
from chisp_flasher.core.errors import BackendError
from chisp_flasher.protocol.commands import (
    CFG_MASK_FULL,
    CFG_MASK_RDPR_USER_DATA_WPR,
    CMD_ERASE,
    CMD_IDENTIFY,
    CMD_ISP_END,
    CMD_ISP_KEY,
    CMD_PROGRAM,
    CMD_READ_CFG,
    CMD_SET_BAUD,
    CMD_VERIFY,
    CMD_WRITE_CFG,
    build_erase,
    build_identify,
    build_isp_end,
    build_isp_key,
    build_program,
    build_read_cfg,
    build_set_baud,
    build_verify,
    build_write_cfg,
)
from chisp_flasher.protocol.crypto import calc_xor_key_seed, calc_xor_key_uid, xor_crypt
from chisp_flasher.protocol.config_codec import apply_config_fields, decode_config_fields
from chisp_flasher.protocol.option_bytes import parse_read_cfg_response
from chisp_flasher.transport.auto_di import AutoDIProfile, candidate_profiles, pulse_reset, set_lines
from chisp_flasher.transport.autodetect import auto_pick_port
from chisp_flasher.transport.serial_link import SerialLink


class UartFramedGenericBackend(BackendBase):
    family_name = 'generic_uart_framed'

    def supported_protocol_variants(self) -> list[str]:
        return ['uart_framed']

    def _normalize_identify_candidates(self, identify_device_id: int, device_type: int, identify_candidates=None) -> list[dict]:
        raw = identify_candidates if isinstance(identify_candidates, (list, tuple)) else []
        out: list[dict] = []
        seen: set[tuple[int, int]] = set()
        for item in raw:
            if isinstance(item, dict):
                cand = dict(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                cand = {'identify_device_id': item[0], 'device_type': item[1]}
            else:
                continue
            if 'identify_device_id' not in cand and 'chip_id' in cand:
                cand['identify_device_id'] = cand['chip_id']
            if 'device_type' not in cand and 'chip_type' in cand:
                cand['device_type'] = cand['chip_type']
            try:
                chip_id = int(cand['identify_device_id']) & 0xFF
                chip_type = int(cand['device_type']) & 0xFF
            except Exception:
                continue
            pair = (chip_id, chip_type)
            if pair in seen:
                continue
            seen.add(pair)
            cand['identify_device_id'] = chip_id
            cand['device_type'] = chip_type
            if 'erase_sectors' in cand:
                cand['erase_sectors'] = int(cand['erase_sectors'])
            if 'chunk_size' in cand:
                cand['chunk_size'] = int(cand['chunk_size'])
            if 'max_flash_size' in cand:
                cand['max_flash_size'] = int(cand['max_flash_size'])
            if 'preserve_user_word' in cand:
                cand['preserve_user_word'] = bool(cand['preserve_user_word'])
            out.append(cand)
        if out:
            return out
        return [{'identify_device_id': int(identify_device_id) & 0xFF, 'device_type': int(device_type) & 0xFF}]

    def _build_identify_pkt(self, candidate: dict) -> bytes:
        return build_identify(device_id=int(candidate['identify_device_id']), device_type=int(candidate['device_type']))

    def _match_identify_candidate(self, chip_id: int, chip_type: int, identify_candidates: list[dict]) -> dict | None:
        chip_id = int(chip_id) & 0xFF
        chip_type = int(chip_type) & 0xFF
        for cand in identify_candidates:
            if chip_id == int(cand['identify_device_id']) and chip_type == int(cand['device_type']):
                return cand
        return None

    def _try_auto_di_candidates(self, link: SerialLink, identify_candidates: list[dict], timeout_s: float = 0.6) -> AutoDIProfile | None:
        packets = [self._build_identify_pkt(cand) for cand in identify_candidates]
        for profile in candidate_profiles():
            try:
                link.flush()
                set_lines(link, profile)
                time.sleep(0.02)
                pulse_reset(link, profile)
                time.sleep(0.06)
                link.flush()
                for pkt in packets:
                    code, data = link.txrx(pkt, CMD_IDENTIFY, timeout_s)
                    if code == 0x00 and len(data) >= 2:
                        return profile
            except Exception:
                continue
        return None

    def detect_chip(
        self,
        *,
        chip_name: str,
        identify_device_id: int,
        device_type: int,
        mode: str = 'usb',
        port: str = '',
        vid: int = 0x1A86,
        pid: int = 0x7523,
        baud: int = 115200,
        parity: str = 'N',
        trace: bool = False,
        log_cb: LogFn = None,
        identify_candidates=None,
        **_ignored,
    ) -> dict:
        if mode not in {'usb', 'ttl'}:
            raise BackendError(f'bad mode: {mode}')
        if not port:
            if mode == 'usb':
                port = auto_pick_port(vid=vid, pid=pid) or ''
            if not port:
                raise BackendError('no port selected')

        link = SerialLink(port=port, baud=baud, parity=parity, trace=trace)
        identify_candidates = self._normalize_identify_candidates(identify_device_id, device_type, identify_candidates)
        identify_pkt = self._build_identify_pkt(identify_candidates[0])
        auto_di_profile: AutoDIProfile | None = None
        self.log(log_cb, 'INFO', f'open port={port} mode={mode} chip={chip_name} host_baud={link.baud}')
        link.open()
        try:
            if mode == 'usb':
                self.log(log_cb, 'INFO', 'auto-di via DTR/RTS...')
                auto_di_profile = self._try_auto_di_candidates(link, identify_candidates)
                if auto_di_profile is None:
                    raise BackendError('auto-di failed (serial usb-uart mode)')
                self.log(log_cb, 'INFO', f'autodi ok boot_is_dtr={int(auto_di_profile.boot_is_dtr)} boot_assert={int(auto_di_profile.boot_assert)} reset_assert={int(auto_di_profile.reset_assert)}')
            else:
                self.log(log_cb, 'ACTION', 'serial manual mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')
            ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
            chip_id = ident['chip_id']
            chip_type = ident['chip_type']
            matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
            if matched_ident is None:
                raise BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': mode,
                'protocol_variant': 'uart_framed',
                'port': port,
                'chip_id': chip_id,
                'chip_type': chip_type,
                'auto_di_profile': auto_di_profile,
            }
        finally:
            link.close()

    def read_config_chip(
        self,
        *,
        chip_name: str,
        identify_device_id: int,
        device_type: int,
        mode: str = 'usb',
        port: str = '',
        vid: int = 0x1A86,
        pid: int = 0x7523,
        baud: int = 115200,
        parity: str = 'N',
        trace: bool = False,
        log_cb: LogFn = None,
        identify_candidates=None,
        **_ignored,
    ) -> dict:
        if mode not in {'usb', 'ttl'}:
            raise BackendError(f'bad mode: {mode}')
        if not port:
            if mode == 'usb':
                port = auto_pick_port(vid=vid, pid=pid) or ''
            if not port:
                raise BackendError('no port selected')

        link = SerialLink(port=port, baud=baud, parity=parity, trace=trace)
        identify_candidates = self._normalize_identify_candidates(identify_device_id, device_type, identify_candidates)
        identify_pkt = self._build_identify_pkt(identify_candidates[0])
        auto_di_profile: AutoDIProfile | None = None
        self.log(log_cb, 'INFO', f'open port={port} mode={mode} chip={chip_name} host_baud={link.baud}')
        link.open()
        try:
            if mode == 'usb':
                self.log(log_cb, 'INFO', 'auto-di via DTR/RTS...')
                auto_di_profile = self._try_auto_di_candidates(link, identify_candidates)
                if auto_di_profile is None:
                    raise BackendError('auto-di failed (serial usb-uart mode)')
                self.log(log_cb, 'INFO', f'autodi ok boot_is_dtr={int(auto_di_profile.boot_is_dtr)} boot_assert={int(auto_di_profile.boot_assert)} reset_assert={int(auto_di_profile.reset_assert)}')
            else:
                self.log(log_cb, 'ACTION', 'serial manual mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')
            ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
            chip_id = ident['chip_id']
            chip_type = ident['chip_type']
            matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
            if matched_ident is None:
                raise BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            opt = self._read_cfg(link, log_cb, label='stage read_cfg (A7)', error_label='read_cfg failed')
            decoded = decode_config_fields('generic', opt.cfg12)
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': mode,
                'protocol_variant': 'uart_framed',
                'port': port,
                'chip_id': chip_id,
                'chip_type': chip_type,
                'uid_hex': opt.uid.hex(),
                'cfg12_hex': opt.cfg12.hex(),
                'rdpr_user_hex': opt.rdpr_user.hex(),
                'data_hex': opt.data_bytes.hex(),
                'wpr_hex': opt.wpr.hex(),
                'raw_hex': opt.raw_response.hex(),
                'data0_hex': f'0x{opt.data_bytes[0]:02X}' if len(opt.data_bytes) >= 1 else '',
                'data1_hex': f'0x{opt.data_bytes[1]:02X}' if len(opt.data_bytes) >= 2 else '',
                'wrp0_hex': f'0x{opt.wpr[0]:02X}' if len(opt.wpr) >= 1 else '',
                'wrp1_hex': f'0x{opt.wpr[1]:02X}' if len(opt.wpr) >= 2 else '',
                'wrp2_hex': f'0x{opt.wpr[2]:02X}' if len(opt.wpr) >= 3 else '',
                'wrp3_hex': f'0x{opt.wpr[3]:02X}' if len(opt.wpr) >= 4 else '',
                'auto_di_profile': auto_di_profile,
                **decoded,
            }
        finally:
            link.close()


    def write_config_chip(
        self,
        *,
        chip_name: str,
        identify_device_id: int,
        device_type: int,
        config,
        option_profile: str,
        mode: str = 'usb',
        port: str = '',
        vid: int = 0x1A86,
        pid: int = 0x7523,
        baud: int = 115200,
        parity: str = 'N',
        trace: bool = False,
        log_cb: LogFn = None,
        identify_candidates=None,
        **_ignored,
    ) -> dict:
        if mode not in {'usb', 'ttl'}:
            raise BackendError(f'bad mode: {mode}')
        if not port:
            if mode == 'usb':
                port = auto_pick_port(vid=vid, pid=pid) or ''
            if not port:
                raise BackendError('no port selected')

        link = SerialLink(port=port, baud=baud, parity=parity, trace=trace)
        identify_candidates = self._normalize_identify_candidates(identify_device_id, device_type, identify_candidates)
        identify_pkt = self._build_identify_pkt(identify_candidates[0])
        auto_di_profile: AutoDIProfile | None = None
        self.log(log_cb, 'INFO', f'open port={port} mode={mode} chip={chip_name} host_baud={link.baud}')
        link.open()
        try:
            if mode == 'usb':
                self.log(log_cb, 'INFO', 'auto-di via DTR/RTS...')
                auto_di_profile = self._try_auto_di_candidates(link, identify_candidates)
                if auto_di_profile is None:
                    raise BackendError('auto-di failed (serial usb-uart mode)')
                self.log(log_cb, 'INFO', f'autodi ok boot_is_dtr={int(auto_di_profile.boot_is_dtr)} boot_assert={int(auto_di_profile.boot_assert)} reset_assert={int(auto_di_profile.reset_assert)}')
            else:
                self.log(log_cb, 'ACTION', 'serial manual mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')
            ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
            chip_id = ident['chip_id']
            chip_type = ident['chip_type']
            matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
            if matched_ident is None:
                raise BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')

            before = self._read_cfg(link, log_cb, label='stage read_cfg (A7)', error_label='read_cfg failed')
            prepared = apply_config_fields(option_profile, before.cfg12, config)
            self.log(log_cb, 'INFO', f'write config fields={",".join(prepared.applied_fields)}')
            if prepared.preserved_fields:
                self.log(log_cb, 'INFO', f'preserved fields={",".join(prepared.preserved_fields)}')
            code, _ = link.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, prepared.cfg12), CMD_WRITE_CFG, 2.0)
            if code != 0x00:
                raise BackendError('write_config failed')
            immediate = self._read_cfg(link, log_cb, label='read_cfg after write_config', error_label='read_cfg after write_config failed')

            self.log(log_cb, 'INFO', 'stage isp_end reason=0x01 (apply option bytes)')
            try:
                link.txrx(build_isp_end(1), CMD_ISP_END, 1.2)
            except Exception:
                pass
            self._reenter_isp(link, identify_candidates, mode, auto_di_profile, log_cb)
            final = self._read_cfg(link, log_cb, label='read_cfg after apply/re-enter', error_label='read_cfg after apply/re-enter failed')
            decoded = decode_config_fields(option_profile, final.cfg12)
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': mode,
                'protocol_variant': 'uart_framed',
                'port': port,
                'chip_id': chip_id,
                'chip_type': chip_type,
                'uid_hex': final.uid.hex(),
                'cfg12_hex': final.cfg12.hex(),
                'rdpr_user_hex': final.rdpr_user.hex(),
                'data_hex': final.data_bytes.hex(),
                'wpr_hex': final.wpr.hex(),
                'raw_hex': final.raw_response.hex(),
                'data0_hex': f'0x{final.data_bytes[0]:02X}' if len(final.data_bytes) >= 1 else '',
                'data1_hex': f'0x{final.data_bytes[1]:02X}' if len(final.data_bytes) >= 2 else '',
                'wrp0_hex': f'0x{final.wpr[0]:02X}' if len(final.wpr) >= 1 else '',
                'wrp1_hex': f'0x{final.wpr[1]:02X}' if len(final.wpr) >= 2 else '',
                'wrp2_hex': f'0x{final.wpr[2]:02X}' if len(final.wpr) >= 3 else '',
                'wrp3_hex': f'0x{final.wpr[3]:02X}' if len(final.wpr) >= 4 else '',
                'applied_fields': prepared.applied_fields,
                'preserved_fields': prepared.preserved_fields,
                'immediate_cfg12_hex': immediate.cfg12.hex(),
                'auto_di_profile': auto_di_profile,
                **decoded,
            }
        finally:
            link.close()

    def erase_chip(
        self,
        *,
        chip_name: str,
        identify_device_id: int,
        device_type: int,
        erase_sectors: int,
        chunk_size: int = 56,
        mode: str = 'usb',
        port: str = '',
        vid: int = 0x1A86,
        pid: int = 0x7523,
        baud: int = 115200,
        parity: str = 'N',
        trace: bool = False,
        log_cb: LogFn = None,
        progress_cb: ProgressFn = None,
        seed_len: int = 0x1E,
        seed_random: bool = False,
        identify_candidates=None,
        **_ignored,
    ) -> dict:
        if mode not in {'usb', 'ttl'}:
            raise BackendError(f'bad mode: {mode}')
        if not port:
            if mode == 'usb':
                port = auto_pick_port(vid=vid, pid=pid) or ''
            if not port:
                raise BackendError('no port selected')

        link = SerialLink(port=port, baud=baud, parity=parity, trace=trace)
        identify_candidates = self._normalize_identify_candidates(identify_device_id, device_type, identify_candidates)
        identify_pkt = self._build_identify_pkt(identify_candidates[0])
        auto_di_profile: AutoDIProfile | None = None
        flash_bytes = int(erase_sectors) * 1024
        t0 = time.monotonic()
        self.log(log_cb, 'INFO', f'open port={port} mode={mode} chip={chip_name} host_baud={link.baud}')
        link.open()
        try:
            if mode == 'usb':
                self.log(log_cb, 'INFO', 'auto-di via DTR/RTS...')
                auto_di_profile = self._try_auto_di_candidates(link, identify_candidates)
                if auto_di_profile is None:
                    raise BackendError('auto-di failed (serial usb-uart mode)')
                self.log(log_cb, 'INFO', f'autodi ok boot_is_dtr={int(auto_di_profile.boot_is_dtr)} boot_assert={int(auto_di_profile.boot_assert)} reset_assert={int(auto_di_profile.reset_assert)}')
            else:
                self.log(log_cb, 'ACTION', 'serial manual mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')
            self.log(log_cb, 'INFO', f'stage identify @ host_baud={link.baud}')
            ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
            chip_id = ident['chip_id']
            chip_type = ident['chip_type']
            matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
            if matched_ident is None:
                raise BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            erase_sectors = int(matched_ident.get('erase_sectors', erase_sectors))
            chunk_size = int(matched_ident.get('chunk_size', chunk_size))
            flash_bytes = int(erase_sectors) * 1024

            opt = self._read_cfg(link, log_cb, label='stage read_cfg (A7)', error_label='read_cfg failed')
            seed = os.urandom(int(seed_len)) if seed_random else (b'\x00' * int(seed_len))
            xor_key, boot_sum = self._unlock_uart(link, chip_id, opt, seed, log_cb)
            self.log(log_cb, 'INFO', f'stage erase sectors={erase_sectors} (full erase)')
            code, _ = link.txrx(build_erase(int(erase_sectors)), CMD_ERASE, 12.0)
            if code != 0x00:
                raise BackendError('erase failed')
            self.log(log_cb, 'INFO', 'erase ok')
            tail_addr = ((flash_bytes - chunk_size) // chunk_size) * chunk_size
            ff_enc = xor_crypt(b'\xFF' * chunk_size, xor_key)
            code, _ = link.txrx(build_verify(tail_addr, 0x00, ff_enc), CMD_VERIFY, 1.8)
            if code != 0x00:
                raise BackendError(f'erase incomplete (tail not erased) addr=0x{tail_addr:08x}')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, 1.2)
            except Exception:
                pass
            if mode == 'usb' and auto_di_profile is not None:
                exit_profile = AutoDIProfile(boot_is_dtr=auto_di_profile.boot_is_dtr, boot_assert=not auto_di_profile.boot_assert, reset_assert=auto_di_profile.reset_assert)
                set_lines(link, exit_profile)
                time.sleep(0.02)
                pulse_reset(link, auto_di_profile)
            self.progress(progress_cb, 100, 1, 1)
            total = time.monotonic() - t0
            self.log(log_cb, 'INFO', f'erase-only ok total={total:.3f}s key_sum=0x{boot_sum:02x}')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': mode,
                'protocol_variant': 'uart_framed',
                'port': port,
                'duration_s': total,
                'erase_sectors': int(erase_sectors),
                'auto_di_profile': auto_di_profile,
            }
        finally:
            link.close()

    def verify_chip(
        self,
        firmware_path: str,
        *,
        chip_name: str,
        identify_device_id: int,
        device_type: int,
        chunk_size: int = 56,
        mode: str = 'usb',
        port: str = '',
        vid: int = 0x1A86,
        pid: int = 0x7523,
        baud: int = 115200,
        parity: str = 'N',
        trace: bool = False,
        log_cb: LogFn = None,
        progress_cb: ProgressFn = None,
        seed_len: int = 0x1E,
        seed_random: bool = False,
        verify_every: int = 1,
        verify_last: bool = True,
        **_ignored,
    ) -> dict:
        if mode not in {'usb', 'ttl'}:
            raise BackendError(f'bad mode: {mode}')
        firmware = self.require_file(firmware_path)
        if not port:
            if mode == 'usb':
                port = auto_pick_port(vid=vid, pid=pid) or ''
            if not port:
                raise BackendError('no port selected')
        verify_every = max(1, int(verify_every))
        blocks = (len(firmware) + chunk_size - 1) // chunk_size
        firmware_padded = firmware + (b'\xFF' * (blocks * chunk_size - len(firmware)))
        link = SerialLink(port=port, baud=baud, parity=parity, trace=trace)
        identify_candidates = self._normalize_identify_candidates(identify_device_id, device_type, identify_candidates)
        identify_pkt = self._build_identify_pkt(identify_candidates[0])
        auto_di_profile: AutoDIProfile | None = None
        t0 = time.monotonic()
        self.log(log_cb, 'INFO', f'open port={port} mode={mode} chip={chip_name} host_baud={link.baud}')
        link.open()
        try:
            if mode == 'usb':
                self.log(log_cb, 'INFO', 'auto-di via DTR/RTS...')
                auto_di_profile = self._try_auto_di_candidates(link, identify_candidates)
                if auto_di_profile is None:
                    raise BackendError('auto-di failed (serial usb-uart mode)')
                self.log(log_cb, 'INFO', f'autodi ok boot_is_dtr={int(auto_di_profile.boot_is_dtr)} boot_assert={int(auto_di_profile.boot_assert)} reset_assert={int(auto_di_profile.reset_assert)}')
            else:
                self.log(log_cb, 'ACTION', 'serial manual mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')
            self.log(log_cb, 'INFO', f'stage identify @ host_baud={link.baud}')
            ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
            chip_id = ident['chip_id']
            chip_type = ident['chip_type']
            matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
            if matched_ident is None:
                raise BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            opt = self._read_cfg(link, log_cb, label='stage read_cfg (A7)', error_label='read_cfg failed')
            seed = os.urandom(int(seed_len)) if seed_random else (b'\x00' * int(seed_len))
            xor_key, boot_sum = self._unlock_uart(link, chip_id, opt, seed, log_cb)
            verify_indices = []
            for i in range(blocks):
                is_last = i == blocks - 1
                if ((i % verify_every) == 0 and not is_last) or (verify_last and is_last):
                    verify_indices.append(i)
            self.progress(progress_cb, 0, 0, len(verify_indices) or blocks)
            last_ui_pct = -1
            last_log_pct = -1
            self.log(log_cb, 'INFO', f'stage verify-only blocks={len(verify_indices)}/{blocks}')
            vtotal = max(1, len(verify_indices))
            for j, i in enumerate(verify_indices):
                addr = i * chunk_size
                plain = firmware_padded[addr:addr + chunk_size]
                enc = xor_crypt(plain, xor_key)
                code, _ = link.txrx(build_verify(addr, 0x00, enc), CMD_VERIFY, 1.8)
                if code != 0x00:
                    raise BackendError(f'verify failed at 0x{addr:08x}')
                stage_pct = (j + 1) * 100 // vtotal
                if stage_pct != last_ui_pct:
                    last_ui_pct = stage_pct
                    self.progress(progress_cb, stage_pct, j + 1, vtotal)
                if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                    last_log_pct = stage_pct
                    self.log(log_cb, 'INFO', f'verify-only {stage_pct}% addr=0x{addr:08x}')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, 1.2)
            except Exception:
                pass
            if mode == 'usb' and auto_di_profile is not None:
                exit_profile = AutoDIProfile(boot_is_dtr=auto_di_profile.boot_is_dtr, boot_assert=not auto_di_profile.boot_assert, reset_assert=auto_di_profile.reset_assert)
                set_lines(link, exit_profile)
                time.sleep(0.02)
                pulse_reset(link, auto_di_profile)
            total = time.monotonic() - t0
            self.progress(progress_cb, 100, len(verify_indices), len(verify_indices))
            self.log(log_cb, 'INFO', f'verify-only ok total={total:.3f}s key_sum=0x{boot_sum:02x}')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': mode,
                'protocol_variant': 'uart_framed',
                'port': port,
                'bytes': len(firmware),
                'blocks': blocks,
                'verified_blocks': len(verify_indices),
                'duration_s': total,
                'auto_di_profile': auto_di_profile,
            }
        finally:
            link.close()

    def flash_chip(
        self,
        firmware_path: str,
        *,
        chip_name: str,
        identify_device_id: int,
        device_type: int,
        erase_sectors: int,
        chunk_size: int = 56,
        mode: str = 'usb',
        port: str = '',
        vid: int = 0x1A86,
        pid: int = 0x7523,
        baud: int = 115200,
        fast_baud: int = 1000000,
        no_fast: bool = False,
        verify: bool = True,
        verify_every: int = 1,
        verify_last: bool = True,
        seed_len: int = 0x1E,
        seed_random: bool = False,
        parity: str = 'N',
        trace: bool = False,
        log_cb: LogFn = None,
        progress_cb: ProgressFn = None,
        preserve_user_word: bool = True,
        identify_candidates=None,
        **_ignored,
    ) -> dict:
        if mode not in {'usb', 'ttl'}:
            raise BackendError(f'bad mode: {mode}')

        firmware = self.require_file(firmware_path)
        if not port:
            if mode == 'usb':
                port = auto_pick_port(vid=vid, pid=pid) or ''
            if not port:
                raise BackendError('no port selected')

        verify_every = max(1, int(verify_every))
        blocks = (len(firmware) + chunk_size - 1) // chunk_size
        firmware_padded = firmware + (b'\xFF' * (blocks * chunk_size - len(firmware)))
        flash_bytes = int(erase_sectors) * 1024

        link = SerialLink(port=port, baud=baud, parity=parity, trace=trace)
        t0 = time.monotonic()
        identify_candidates = self._normalize_identify_candidates(identify_device_id, device_type, identify_candidates)
        identify_pkt = self._build_identify_pkt(identify_candidates[0])
        auto_di_profile: AutoDIProfile | None = None

        self.log(log_cb, 'INFO', f'open port={port} mode={mode} chip={chip_name} host_baud={link.baud}')
        link.open()
        try:
            if mode == 'usb':
                self.log(log_cb, 'INFO', 'auto-di via DTR/RTS...')
                auto_di_profile = self._try_auto_di_candidates(link, identify_candidates)
                if auto_di_profile is None:
                    raise BackendError('auto-di failed (serial usb-uart mode)')
                self.log(log_cb, 'INFO', f'autodi ok boot_is_dtr={int(auto_di_profile.boot_is_dtr)} boot_assert={int(auto_di_profile.boot_assert)} reset_assert={int(auto_di_profile.reset_assert)}')
            else:
                self.log(log_cb, 'ACTION', 'serial manual mode: enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')

            self.log(log_cb, 'INFO', f'stage identify @ host_baud={link.baud}')
            ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
            chip_id = ident['chip_id']
            chip_type = ident['chip_type']
            matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
            if matched_ident is None:
                raise BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            erase_sectors = int(matched_ident.get('erase_sectors', erase_sectors))
            chunk_size = int(matched_ident.get('chunk_size', chunk_size))
            preserve_user_word = bool(matched_ident.get('preserve_user_word', preserve_user_word))
            flash_bytes = int(erase_sectors) * 1024
            max_flash_size = int(matched_ident.get('max_flash_size') or flash_bytes)
            if len(firmware) > max_flash_size:
                raise BackendError(f'firmware too large for {chip_name}: {len(firmware)} > {max_flash_size}')
            blocks = (len(firmware) + chunk_size - 1) // chunk_size
            firmware_padded = firmware + (b'\xFF' * (blocks * chunk_size - len(firmware)))

            opt = self._read_cfg(link, log_cb, label='stage read_cfg (A7)', error_label='read_cfg failed')
            cfg12 = bytearray(opt.cfg12)
            wpr = bytes(opt.wpr)
            uid = bytes(opt.uid)

            cfg12_a = bytearray(cfg12)
            cfg12_a[0:2] = b'\xA5\x5A'
            if not preserve_user_word:
                cfg12_a[2:4] = b'\x3F\xC0'
            cfg12_a[4:8] = b'\x00\xFF\x00\xFF'
            cfg12_a[8:12] = b'\xFF\xFF\xFF\xFF'
            self.log(log_cb, 'INFO', 'wchtool: stage write_cfg step1 (A8)')
            code, _ = link.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, bytes(cfg12_a)), CMD_WRITE_CFG, 2.0)
            if code != 0x00:
                raise BackendError('write_cfg (wchtool step1) failed')
            _ = self._read_cfg(link, log_cb, label='read_cfg after write_cfg (wchtool step1)', error_label='read_cfg after write_cfg (wchtool step1) failed')

            self.log(log_cb, 'INFO', 'wchtool: stage isp_end reason=0x01 (apply option bytes)')
            try:
                link.txrx(build_isp_end(1), CMD_ISP_END, 1.2)
            except Exception:
                pass

            self._reenter_isp(link, identify_candidates, mode, auto_di_profile, log_cb)
            self.log(log_cb, 'INFO', 'bootloader detected again (after isp_end(01))')

            cfg_data = b''
            for _ in range(2):
                ident = self._wait_identify_candidates(link, identify_candidates, mode, log_cb)
                chip_id = ident['chip_id']
                chip_type = ident['chip_type']
                matched_ident = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
                if matched_ident is None:
                    raise BackendError(f'unexpected chip_id/type after re-enter: 0x{chip_id:02x}/0x{chip_type:02x}')
                code, cfg_data = link.txrx(build_read_cfg(CFG_MASK_FULL), CMD_READ_CFG, 1.2)
                if code != 0x00 or len(cfg_data) < 14:
                    raise BackendError('read_cfg failed after re-enter (wchtool)')
            opt = parse_read_cfg_response(cfg_data)
            self._log_cfg(log_cb, 'cfg_after_reenter', opt)

            cfg12_b = bytearray(opt.cfg12)
            cfg12_b[0:2] = b'\xFF\xFF'
            if not preserve_user_word:
                cfg12_b[2:4] = b'\x3F\xC0'
            cfg12_b[4:8] = b'\x00\x00\x00\x00'
            cfg12_b[8:12] = b'\xFF\xFF\xFF\xFF'
            self.log(log_cb, 'INFO', 'wchtool: stage write_cfg step2 (A8)')
            code, _ = link.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, bytes(cfg12_b)), CMD_WRITE_CFG, 2.0)
            if code != 0x00:
                raise BackendError('write_cfg (wchtool step2) failed')
            opt = self._read_cfg(link, log_cb, label='read_cfg after write_cfg (wchtool step2)', error_label='read_cfg after write_cfg (wchtool step2) failed')
            cfg12 = bytearray(opt.cfg12)
            wpr = bytes(opt.wpr)
            uid = bytes(opt.uid)

            self.log(log_cb, 'INFO', 'stage isp_key (A3)')
            seed = os.urandom(int(seed_len)) if seed_random else (b'\x00' * int(seed_len))
            code, kresp = link.txrx(build_isp_key(seed), CMD_ISP_KEY, 1.2)
            if code != 0x00 or len(kresp) < 1:
                raise BackendError('isp_key failed')
            boot_sum = kresp[0] & 0xFF
            uid_chk = opt.raw_response[2]
            candidates = []
            if len(uid) == 8:
                candidates.append(('uid', calc_xor_key_uid(uid, chip_id)))
            try:
                candidates.append(('seed', calc_xor_key_seed(seed, uid_chk, chip_id)))
            except Exception:
                pass
            picked = [item for item in candidates if (sum(item[1]) & 0xFF) == boot_sum]
            if not picked:
                msg = f'isp_key checksum mismatch: boot=0x{boot_sum:02x}'
                for name, key in candidates:
                    msg += f' {name}=0x{(sum(key) & 0xFF):02x}'
                raise BackendError(msg)
            picked.sort(key=lambda item: 0 if item[0] == 'uid' else 1)
            key_src, xor_key = picked[0]
            self.log(log_cb, 'INFO', f'isp_key ok (src={key_src} key_sum=0x{boot_sum:02x})')
            self.log(log_cb, 'INFO', 'unlock ok')

            if wpr != b'\xFF\xFF\xFF\xFF':
                self.log(log_cb, 'WARN', f'code flash protected (WPR={wpr.hex()}) -> clearing WPR + RDPR')
                cfg12[0] = 0xA5
                cfg12[1] = 0x5A
                cfg12[8:12] = b'\xFF\xFF\xFF\xFF'
                code, _ = link.txrx(build_write_cfg(CFG_MASK_RDPR_USER_DATA_WPR, bytes(cfg12)), CMD_WRITE_CFG, 2.0)
                if code != 0x00:
                    raise BackendError('write_cfg (unprotect) failed')
                time.sleep(0.08)
                opt2 = self._read_cfg(link, log_cb, label='read_cfg after unprotect', error_label='read_cfg after unprotect failed')
                self.log(log_cb, 'INFO', f'cfg_wpr(after)={opt2.wpr.hex()}')
                if opt2.wpr != b'\xFF\xFF\xFF\xFF':
                    raise BackendError('WPR still not cleared (needs reset/power-cycle to apply option bytes). Re-enter bootloader and retry.')
                self.log(log_cb, 'INFO', 'unprotect ok')

            self.log(log_cb, 'INFO', f'stage erase sectors={erase_sectors} (full erase)')
            code, _ = link.txrx(build_erase(int(erase_sectors)), CMD_ERASE, 12.0)
            if code != 0x00:
                raise BackendError('erase failed')
            self.log(log_cb, 'INFO', 'erase ok')

            tail_addr = ((flash_bytes - chunk_size) // chunk_size) * chunk_size
            ff_enc = xor_crypt(b'\xFF' * chunk_size, xor_key)
            code, _ = link.txrx(build_verify(tail_addr, 0x00, ff_enc), CMD_VERIFY, 1.8)
            if code != 0x00:
                raise BackendError(f'erase incomplete (tail not erased) addr=0x{tail_addr:08x}')

            if not no_fast:
                self.log(log_cb, 'INFO', f'stage set_baud mcu={fast_baud}')
                code, _ = link.txrx(build_set_baud(int(fast_baud)), CMD_SET_BAUD, 1.2)
                if code != 0x00:
                    raise BackendError('set_baud failed')
                time.sleep(0.03)
                link.set_baud(int(fast_baud))
                link.flush()
                self.log(log_cb, 'INFO', f'set_baud ok host_baud={link.baud}')

            pad_byte = 0x00
            verify_tag = 'on' if verify else 'off'
            verify_tail = ' +last' if verify_last else ''
            self.log(log_cb, 'INFO', f'stage program addr=0x00000000..0x{blocks * chunk_size:08x} blocks={blocks} chunk={chunk_size} verify={verify_tag} every={verify_every}{verify_tail}')
            tprog = time.monotonic()
            self.progress(progress_cb, 0, 0, blocks)
            last_ui_pct = -1
            last_log_pct = -1
            for i in range(blocks):
                addr = i * chunk_size
                plain = firmware_padded[addr:addr + chunk_size]
                enc = xor_crypt(plain, xor_key)
                code, _ = link.txrx(build_program(addr, pad_byte, enc), CMD_PROGRAM, 1.8)
                if code != 0x00:
                    raise BackendError(f'program failed at 0x{addr:08x}')
                stage_pct = (i + 1) * 100 // blocks
                ui_pct = (i + 1) * 50 // blocks
                if ui_pct != last_ui_pct:
                    last_ui_pct = ui_pct
                    self.progress(progress_cb, ui_pct, i + 1, blocks)
                if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                    last_log_pct = stage_pct
                    self.log(log_cb, 'INFO', f'program {stage_pct}% addr=0x{(i + 1) * chunk_size:08x}')

            flush_addr = blocks * chunk_size
            self.log(log_cb, 'INFO', f'stage program_flush addr=0x{flush_addr:08x} (A5 empty)')
            code, _ = link.txrx(build_program(flush_addr, pad_byte, b''), CMD_PROGRAM, 2.2)
            if code != 0x00:
                raise BackendError('program_flush failed')
            self.progress(progress_cb, 50, blocks, blocks)
            dt = time.monotonic() - tprog
            kb = len(firmware) / 1024.0
            self.log(log_cb, 'INFO', f'program done in {dt:.3f}s ({kb / dt:.1f} KiB/s)')

            if verify:
                self.log(log_cb, 'INFO', 'stage isp_key (A3) before verify')
                code, kresp2 = link.txrx(build_isp_key(seed), CMD_ISP_KEY, 1.2)
                if code != 0x00 or len(kresp2) < 1 or (kresp2[0] & 0xFF) != boot_sum:
                    raise BackendError('isp_key before verify failed')
                verify_indices = []
                for i in range(blocks):
                    is_last = i == blocks - 1
                    if ((i % verify_every) == 0 and not is_last) or (verify_last and is_last):
                        verify_indices.append(i)
                if not verify_indices:
                    self.log(log_cb, 'INFO', 'stage verify skipped (no blocks selected)')
                    self.progress(progress_cb, 100, blocks, blocks)
                else:
                    self.log(log_cb, 'INFO', f'stage verify blocks={len(verify_indices)}/{blocks}')
                    last_ui_pct = -1
                    last_log_pct = -1
                    vtotal = len(verify_indices)
                    for j, i in enumerate(verify_indices):
                        addr = i * chunk_size
                        plain = firmware_padded[addr:addr + chunk_size]
                        enc = xor_crypt(plain, xor_key)
                        code, _ = link.txrx(build_verify(addr, pad_byte, enc), CMD_VERIFY, 1.8)
                        if code != 0x00:
                            raise BackendError(f'verify failed at 0x{addr:08x}')
                        stage_pct = (j + 1) * 100 // vtotal
                        ui_pct = 50 + ((j + 1) * 50 // vtotal)
                        if ui_pct != last_ui_pct:
                            last_ui_pct = ui_pct
                            self.progress(progress_cb, ui_pct, j + 1, vtotal)
                        if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                            last_log_pct = stage_pct
                            self.log(log_cb, 'INFO', f'verify {stage_pct}% addr=0x{addr:08x}')
                    self.log(log_cb, 'INFO', 'verify ok')

            self.log(log_cb, 'INFO', 'stage isp_end')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, 1.2)
            except Exception:
                pass

            if mode == 'usb' and auto_di_profile is not None:
                exit_profile = AutoDIProfile(
                    boot_is_dtr=auto_di_profile.boot_is_dtr,
                    boot_assert=not auto_di_profile.boot_assert,
                    reset_assert=auto_di_profile.reset_assert,
                )
                set_lines(link, exit_profile)
                time.sleep(0.02)
                pulse_reset(link, auto_di_profile)

            self.progress(progress_cb, 100, blocks, blocks)
            total = time.monotonic() - t0
            self.log(log_cb, 'INFO', f'OK total={total:.3f}s')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': mode,
                'protocol_variant': 'uart_framed',
                'port': port,
                'bytes': len(firmware),
                'blocks': blocks,
                'duration_s': total,
                'auto_di_profile': auto_di_profile,
                'inferred_path': chip_name != 'CH32V203',
            }
        finally:
            link.close()

    def _wait_identify_candidates(self, link: SerialLink, identify_candidates: list[dict], mode: str, log_cb: LogFn):
        packets = [self._build_identify_pkt(cand) for cand in identify_candidates]
        if mode == 'ttl':
            end = time.monotonic() + 12.0
            last_err = None
            while True:
                try:
                    link.flush()
                    for pkt in packets:
                        code, data = link.txrx(pkt, CMD_IDENTIFY, 0.8)
                        if code == 0x00 and len(data) >= 2:
                            self.log(log_cb, 'INFO', 'bootloader detected (ISP active)')
                            return {'chip_id': data[0], 'chip_type': data[1]}
                    last_err = BackendError('identify bad response')
                except Exception as e:
                    last_err = e
                if time.monotonic() >= end:
                    raise BackendError(f'identify failed (serial manual mode). enter bootloader first. last={last_err}')
                time.sleep(0.15)
        last_err = None
        for pkt in packets:
            try:
                code, data = link.txrx(pkt, CMD_IDENTIFY, 1.0)
                if code == 0x00 and len(data) >= 2:
                    return {'chip_id': data[0], 'chip_type': data[1]}
                last_err = BackendError('identify bad response')
            except Exception as e:
                last_err = e
        raise BackendError(f'identify failed: {last_err}')

    def _reenter_isp(self, link: SerialLink, identify_candidates: list[dict], mode: str, auto_di_profile: AutoDIProfile | None, log_cb: LogFn) -> None:
        packets = [self._build_identify_pkt(cand) for cand in identify_candidates]
        if mode == 'usb':
            if auto_di_profile is None:
                raise BackendError('autodi missing (usb)')
            self.log(log_cb, 'INFO', 'usb: re-enter bootloader (autodi)')
            end = time.monotonic() + 2.5
            last_err = None
            while True:
                try:
                    link.flush()
                    set_lines(link, auto_di_profile)
                    time.sleep(0.02)
                    pulse_reset(link, auto_di_profile)
                    time.sleep(0.08)
                    link.flush()
                    for pkt in packets:
                        code, data = link.txrx(pkt, CMD_IDENTIFY, 0.8)
                        if code == 0x00 and len(data) >= 2:
                            return
                    last_err = BackendError('identify bad response')
                except Exception as e:
                    last_err = e
                if time.monotonic() >= end:
                    raise BackendError(f'identify failed (serial usb-uart mode) after isp_end(01). last={last_err}')
                time.sleep(0.10)
            return
        self.log(log_cb, 'ACTION', 'ttl: re-enter bootloader now (hold BOOT, tap RESET). waiting for ISP...')
        end = time.monotonic() + 12.0
        last_err = None
        while True:
            try:
                link.flush()
                for pkt in packets:
                    code, data = link.txrx(pkt, CMD_IDENTIFY, 0.8)
                    if code == 0x00 and len(data) >= 2:
                        return
                last_err = BackendError('identify bad response')
            except Exception as e:
                last_err = e
            if time.monotonic() >= end:
                raise BackendError(f'identify failed (serial manual mode) after isp_end(01). last={last_err}')
            time.sleep(0.15)

    def _unlock_uart(self, link: SerialLink, chip_id: int, opt, seed: bytes, log_cb: LogFn) -> tuple[bytes, int]:
        self.log(log_cb, 'INFO', 'stage isp_key (A3)')
        code, kresp = link.txrx(build_isp_key(seed), CMD_ISP_KEY, 1.2)
        if code != 0x00 or len(kresp) < 1:
            raise BackendError('isp_key failed')
        boot_sum = kresp[0] & 0xFF
        uid_chk = opt.raw_response[2]
        candidates = []
        if len(opt.uid) == 8:
            candidates.append(('uid', calc_xor_key_uid(opt.uid, chip_id)))
        try:
            candidates.append(('seed', calc_xor_key_seed(seed, uid_chk, chip_id)))
        except Exception:
            pass
        picked = [item for item in candidates if (sum(item[1]) & 0xFF) == boot_sum]
        if not picked:
            msg = f'isp_key checksum mismatch: boot=0x{boot_sum:02x}'
            for name, key in candidates:
                msg += f' {name}=0x{(sum(key) & 0xFF):02x}'
            raise BackendError(msg)
        picked.sort(key=lambda item: 0 if item[0] == 'uid' else 1)
        key_src, xor_key = picked[0]
        self.log(log_cb, 'INFO', f'isp_key ok (src={key_src} key_sum=0x{boot_sum:02x})')
        self.log(log_cb, 'INFO', 'unlock ok')
        return xor_key, boot_sum

    def _read_cfg(self, link: SerialLink, log_cb: LogFn, *, label: str, error_label: str):
        self.log(log_cb, 'INFO', label)
        code, cfg = link.txrx(build_read_cfg(CFG_MASK_FULL), CMD_READ_CFG, 1.2)
        if code != 0x00 or len(cfg) < 14:
            raise BackendError(error_label)
        opt = parse_read_cfg_response(cfg)
        self._log_cfg(log_cb, label.split('(')[0].strip().replace(' ', '_'), opt)
        return opt

    def _log_cfg(self, log_cb: LogFn, tag: str, opt) -> None:
        if len(opt.uid) == 8:
            self.log(log_cb, 'INFO', f'uid={opt.uid.hex("-")}')
        self.log(log_cb, 'INFO', f'{tag} rdpr_user={opt.rdpr_user.hex()} cfg_data={opt.data_bytes.hex()} cfg_wpr={opt.wpr.hex()}')
