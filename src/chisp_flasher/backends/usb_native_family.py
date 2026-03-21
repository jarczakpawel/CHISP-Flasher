from __future__ import annotations

import os
import time

from chisp_flasher.backends.base import BackendBase, LogFn, ProgressFn
from chisp_flasher.core.errors import BackendError
from chisp_flasher.protocol.commands import CMD_ERASE, CMD_IDENTIFY, CMD_ISP_END, CMD_ISP_KEY, CMD_PROGRAM, CMD_READ_CFG, CMD_VERIFY, CMD_WRITE_CFG
from chisp_flasher.protocol.config_codec import apply_config_fields, decode_config_fields
from chisp_flasher.protocol.crypto import calc_xor_key_seed, calc_xor_key_uid, xor_crypt
from chisp_flasher.protocol.native_usb import build_erase, build_identify, build_isp_end, build_isp_key, build_program, build_read_cfg, build_verify, build_write_cfg
from chisp_flasher.protocol.option_bytes import parse_read_cfg_response
from chisp_flasher.protocol.variants import USB_NATIVE_PLAIN
from chisp_flasher.transport.usb_native import UsbNativeLink

CHUNK_SIZE = 56
USB_TIMEOUTS_MS = {
    'identify': 1000,
    'read_cfg': 1200,
    'write_cfg': 2000,
    'isp_key': 1200,
    'erase': 20000,
    'program': 2500,
    'verify': 2500,
    'isp_end': 1200,
}


class NativeUsbFamilyBackend(BackendBase):
    chip_native_defaults: dict[str, dict] = {}

    def _normalize_identify_candidates(self, chip_cfg: dict, identify_candidates=None) -> list[dict]:
        raw = identify_candidates if isinstance(identify_candidates, (list, tuple)) else None
        if raw is None:
            fallback = chip_cfg.get('identify_candidates')
            raw = fallback if isinstance(fallback, (list, tuple)) else []
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
            if 'max_flash_size' in cand:
                cand['max_flash_size'] = int(cand['max_flash_size'])
            if 'option_profile' in cand:
                cand['option_profile'] = str(cand['option_profile'])
            out.append(cand)
        if out:
            return out
        return [{
            'identify_device_id': int(chip_cfg['identify_device_id']) & 0xFF,
            'device_type': int(chip_cfg['device_type']) & 0xFF,
            'erase_sectors': int(chip_cfg.get('erase_sectors') or 0),
            'max_flash_size': int(chip_cfg.get('max_flash_size') or 0),
            'option_profile': str(chip_cfg.get('option_profile') or ''),
        }]

    def _build_identify_pkt(self, candidate: dict) -> bytes:
        return build_identify(int(candidate['identify_device_id']), int(candidate['device_type']))

    def _match_identify_candidate(self, chip_id: int, chip_type: int, identify_candidates: list[dict]) -> dict | None:
        chip_id = int(chip_id) & 0xFF
        chip_type = int(chip_type) & 0xFF
        for cand in identify_candidates:
            if chip_id == int(cand['identify_device_id']) and chip_type == int(cand['device_type']):
                return cand
        return None

    def _probe_identify_native(self, link: UsbNativeLink, identify_candidates: list[dict], timeout_ms: int):
        last_err = None
        for cand in identify_candidates:
            try:
                parsed = link.txrx_frame(self._build_identify_pkt(cand), CMD_IDENTIFY, timeout_ms)
                if parsed.code != 0x00 or len(parsed.data) < 2:
                    last_err = BackendError('identify bad response')
                    continue
                chip_id = parsed.data[0]
                chip_type = parsed.data[1]
                matched = self._match_identify_candidate(chip_id, chip_type, identify_candidates)
                if matched is not None:
                    return parsed, matched
                expected = ', '.join(
                    f'0x{int(x["identify_device_id"]):02x}/0x{int(x["device_type"]):02x}'
                    for x in identify_candidates
                )
                last_err = BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}; expected: {expected}')
            except Exception as e:
                last_err = e
        if last_err is None:
            raise BackendError('identify failed')
        raise last_err


    def _make_link(self, selector: str, *, trace: bool, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None) -> UsbNativeLink:
        return UsbNativeLink(
            UsbNativeLink.parse_selector(
                selector,
                interface_number=usb_interface_number,
                endpoint_out=usb_endpoint_out,
                endpoint_in=usb_endpoint_in,
            ),
            trace=trace,
        )

    def supported_protocol_variants(self) -> list[str]:
        return [USB_NATIVE_PLAIN.key]

    def _detected_link(self, detected: dict, *, trace: bool) -> UsbNativeLink:
        return self._make_link(
            str(detected['usb_selector']),
            trace=trace,
            usb_interface_number=detected.get('interface_number'),
            usb_endpoint_out=detected.get('endpoint_out'),
            usb_endpoint_in=detected.get('endpoint_in'),
        )

    def detect_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, identify_candidates=None, **_kwargs) -> dict:
        chip_cfg = dict(self.chip_native_defaults[chip_name])
        identify_candidates = self._normalize_identify_candidates(chip_cfg, identify_candidates)
        selector = (usb_selector or '').strip()
        if not selector:
            raise BackendError('usb device selector is empty')
        expected = ', '.join(f'0x{int(c["identify_device_id"]):02x}/0x{int(c["device_type"]):02x}' for c in identify_candidates)
        self.log(log_cb, 'INFO', f'native usb open selector={selector} chip={chip_name}')
        self.log(log_cb, 'INFO', f'native usb expected identify pairs: {expected}')
        link = self._make_link(selector, trace=trace, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in)
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates, USB_TIMEOUTS_MS['identify'])
            chip_id = parsed.data[0]
            chip_type = parsed.data[1]
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'usb',
                'protocol_variant': USB_NATIVE_PLAIN.key,
                'usb_selector': selector,
                'chip_id': chip_id,
                'chip_type': chip_type,
                'interface_number': link.info.interface_number,
                'endpoint_out': link.info.endpoint_out,
                'endpoint_in': link.info.endpoint_in,
            }
        finally:
            link.close()

    def read_config_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, identify_candidates=None, **_kwargs) -> dict:
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb, identify_candidates=identify_candidates)
        chip_cfg = dict(self.chip_native_defaults[chip_name])
        identify_candidates = self._normalize_identify_candidates(chip_cfg, identify_candidates)
        option_profile = str(chip_cfg.get('option_profile') or '')
        link = self._detected_link(detected, trace=trace)
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates, USB_TIMEOUTS_MS['identify'])
            option_profile = str(matched_ident.get('option_profile') or option_profile)
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            opt = parse_read_cfg_response(cfg_raw)
            self._log_cfg(log_cb, 'cfg_initial', opt)
            result = {
                **detected,
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
            }
            if option_profile:
                result.update(decode_config_fields(option_profile, opt.cfg12))
            return result
        finally:
            link.close()

    def write_config_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, config, trace: bool = False, log_cb: LogFn = None, identify_candidates=None, **_kwargs) -> dict:
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb, identify_candidates=identify_candidates)
        chip_cfg = dict(self.chip_native_defaults[chip_name])
        identify_candidates = self._normalize_identify_candidates(chip_cfg, identify_candidates)
        option_profile = str(chip_cfg.get('option_profile') or '')
        if not option_profile:
            raise BackendError(f'option profile is missing for {chip_name}')
        link = self._detected_link(detected, trace=trace)
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates, USB_TIMEOUTS_MS['identify'])
            chip_id = parsed.data[0]
            chip_type = parsed.data[1]
            option_profile = str(matched_ident.get('option_profile') or option_profile)
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            opt_before = parse_read_cfg_response(cfg_raw)
            self._log_cfg(log_cb, 'cfg_initial', opt_before)
            prepared = apply_config_fields(option_profile, opt_before.cfg12, config)
            self.log(log_cb, 'INFO', f'write config fields={",".join(prepared.applied_fields)}')
            if prepared.preserved_fields:
                self.log(log_cb, 'INFO', f'preserved fields={",".join(prepared.preserved_fields)}')
            code, _ = link.txrx(build_write_cfg(data=prepared.cfg12), CMD_WRITE_CFG, USB_TIMEOUTS_MS['write_cfg'])
            if code != 0x00:
                raise BackendError('write_config native usb failed')
            self.log(log_cb, 'INFO', f'write_cfg single cfg12={prepared.cfg12.hex()}')
            code, cfg_raw_after = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg after write failed')
            opt_after = parse_read_cfg_response(cfg_raw_after)
            self._log_cfg(log_cb, 'cfg_after_write', opt_after)
            result = {
                **detected,
                'chip_id': chip_id,
                'chip_type': chip_type,
                'uid_hex': opt_after.uid.hex(),
                'cfg12_hex': opt_after.cfg12.hex(),
                'rdpr_user_hex': opt_after.rdpr_user.hex(),
                'data_hex': opt_after.data_bytes.hex(),
                'wpr_hex': opt_after.wpr.hex(),
                'raw_hex': opt_after.raw_response.hex(),
                'data0_hex': f'0x{opt_after.data_bytes[0]:02X}' if len(opt_after.data_bytes) >= 1 else '',
                'data1_hex': f'0x{opt_after.data_bytes[1]:02X}' if len(opt_after.data_bytes) >= 2 else '',
                'wrp0_hex': f'0x{opt_after.wpr[0]:02X}' if len(opt_after.wpr) >= 1 else '',
                'wrp1_hex': f'0x{opt_after.wpr[1]:02X}' if len(opt_after.wpr) >= 2 else '',
                'wrp2_hex': f'0x{opt_after.wpr[2]:02X}' if len(opt_after.wpr) >= 3 else '',
                'wrp3_hex': f'0x{opt_after.wpr[3]:02X}' if len(opt_after.wpr) >= 4 else '',
                'applied_fields': prepared.applied_fields,
                'preserved_fields': prepared.preserved_fields,
                'immediate_cfg12_hex': opt_after.cfg12.hex(),
                'native_usb_write_style': 'single_write_then_readback',
            }
            result.update(decode_config_fields(option_profile, opt_after.cfg12))
            return result
        finally:
            link.close()

    def erase_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, progress_cb: ProgressFn = None, seed_len: int = 49, seed_random: bool = False, identify_candidates=None, **_kwargs) -> dict:
        chip_cfg = dict(self.chip_native_defaults[chip_name])
        identify_candidates = self._normalize_identify_candidates(chip_cfg, identify_candidates)
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb, identify_candidates=identify_candidates)
        flash_bytes = int(chip_cfg['erase_sectors']) * 1024
        seed = os.urandom(int(seed_len)) if seed_random else (b'\x00' * int(seed_len))
        t0 = time.monotonic()
        link = self._detected_link(detected, trace=trace)
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates, USB_TIMEOUTS_MS['identify'])
            chip_id = parsed.data[0]
            erase_sectors = int(matched_ident.get('erase_sectors') or chip_cfg['erase_sectors'])
            flash_bytes = int(matched_ident.get('max_flash_size') or erase_sectors * 1024)
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            opt = parse_read_cfg_response(cfg_raw)
            self._log_cfg(log_cb, 'cfg_initial', opt)
            xor_key, boot_sum, key_src = self._unlock_with_seed(link, opt, chip_id, seed, label='erase', timeout_ms=USB_TIMEOUTS_MS['isp_key'])
            self.log(log_cb, 'INFO', f'isp_key erase ok (src={key_src} key_sum=0x{boot_sum:02x})')
            self.log(log_cb, 'INFO', f'stage erase sectors={erase_sectors}')
            code, _ = link.txrx(build_erase(erase_sectors), CMD_ERASE, USB_TIMEOUTS_MS['erase'])
            if code != 0x00:
                raise BackendError('erase failed')
            tail_addr = ((flash_bytes - CHUNK_SIZE) // CHUNK_SIZE) * CHUNK_SIZE
            ff_enc = xor_crypt(b'\xff' * CHUNK_SIZE, xor_key)
            code, _ = link.txrx(build_verify(tail_addr, 0x00, ff_enc), CMD_VERIFY, USB_TIMEOUTS_MS['verify'])
            if code != 0x00:
                raise BackendError(f'erase incomplete (tail not erased) addr=0x{tail_addr:08x}')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, USB_TIMEOUTS_MS['isp_end'])
            except Exception:
                pass
            self.progress(progress_cb, 100, 1, 1)
            total = time.monotonic() - t0
            self.log(log_cb, 'INFO', f'erase-only ok total={total:.3f}s')
            return {**detected, 'duration_s': total, 'erase_sectors': int(erase_sectors), 'interface_number': link.info.interface_number, 'endpoint_out': link.info.endpoint_out, 'endpoint_in': link.info.endpoint_in}
        finally:
            link.close()

    def verify_native_usb(self, chip_name: str, firmware_path: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, progress_cb: ProgressFn = None, seed_len: int = 52, seed_random: bool = False, identify_candidates=None, **_kwargs) -> dict:
        chip_cfg = dict(self.chip_native_defaults[chip_name])
        identify_candidates = self._normalize_identify_candidates(chip_cfg, identify_candidates)
        max_flash_size = max(int(cand.get('max_flash_size') or chip_cfg.get('max_flash_size') or int(chip_cfg['erase_sectors']) * 1024) for cand in identify_candidates)
        firmware = self.require_file(firmware_path, chip_name=chip_name, max_size=max_flash_size)
        blocks = (len(firmware) + CHUNK_SIZE - 1) // CHUNK_SIZE
        firmware_padded = firmware + (b'\xff' * (blocks * CHUNK_SIZE - len(firmware)))
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb, identify_candidates=identify_candidates)
        seed = os.urandom(int(seed_len)) if seed_random else (b'\x00' * int(seed_len))
        t0 = time.monotonic()
        link = self._detected_link(detected, trace=trace)
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates, USB_TIMEOUTS_MS['identify'])
            chip_id = parsed.data[0]
            chip_type = parsed.data[1]
            chip_limit = int(matched_ident.get('max_flash_size') or chip_cfg.get('max_flash_size') or int(chip_cfg['erase_sectors']) * 1024)
            if len(firmware) > chip_limit:
                raise BackendError(f'firmware too large for {chip_name}: {len(firmware)} > {chip_limit}')
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            opt = parse_read_cfg_response(cfg_raw)
            self._log_cfg(log_cb, 'cfg_initial', opt)
            xor_key, boot_sum, key_src = self._unlock_with_seed(link, opt, chip_id, seed, label='verify', timeout_ms=USB_TIMEOUTS_MS['isp_key'])
            self.log(log_cb, 'INFO', f'isp_key verify ok (src={key_src} key_sum=0x{boot_sum:02x})')
            self.log(log_cb, 'INFO', f'stage verify-only blocks={blocks}/{blocks}')
            last_ui_pct = -1
            last_log_pct = -1
            for i in range(blocks):
                addr = i * CHUNK_SIZE
                plain = firmware_padded[addr:addr + CHUNK_SIZE]
                enc = xor_crypt(plain, xor_key)
                code, _ = link.txrx(build_verify(addr, 0x00, enc), CMD_VERIFY, USB_TIMEOUTS_MS['verify'])
                if code != 0x00:
                    raise BackendError(f'verify failed at 0x{addr:08x}')
                stage_pct = (i + 1) * 100 // blocks
                if stage_pct != last_ui_pct:
                    last_ui_pct = stage_pct
                    self.progress(progress_cb, stage_pct, i + 1, blocks)
                if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                    last_log_pct = stage_pct
                    self.log(log_cb, 'INFO', f'verify-only {stage_pct}% addr=0x{addr:08x}')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, USB_TIMEOUTS_MS['isp_end'])
            except Exception:
                pass
            total = time.monotonic() - t0
            self.progress(progress_cb, 100, blocks, blocks)
            self.log(log_cb, 'INFO', f'verify-only ok total={total:.3f}s')
            return {**detected, 'bytes': len(firmware), 'blocks': blocks, 'duration_s': total, 'interface_number': link.info.interface_number, 'endpoint_out': link.info.endpoint_out, 'endpoint_in': link.info.endpoint_in}
        finally:
            link.close()

    def flash_native_usb(self, chip_name: str, firmware_path: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, verify: bool = True, seed_lens: tuple[int, int] = (49, 52), seed_random: bool = False, trace: bool = False, log_cb: LogFn = None, progress_cb: ProgressFn = None, identify_candidates=None) -> dict:
        chip_cfg = dict(self.chip_native_defaults[chip_name])
        identify_candidates = self._normalize_identify_candidates(chip_cfg, identify_candidates)
        erase_sectors = int(chip_cfg['erase_sectors'])
        max_flash_size = max(int(cand.get('max_flash_size') or chip_cfg.get('max_flash_size') or erase_sectors * 1024) for cand in identify_candidates)
        firmware = self.require_file(firmware_path, chip_name=chip_name, max_size=max_flash_size)
        blocks = (len(firmware) + CHUNK_SIZE - 1) // CHUNK_SIZE
        firmware_padded = firmware + (b'\xff' * (blocks * CHUNK_SIZE - len(firmware)))
        selector = (usb_selector or '').strip()
        if not selector:
            raise BackendError('usb device selector is empty')
        seed_program = os.urandom(int(seed_lens[0])) if seed_random else (b'\x00' * int(seed_lens[0]))
        seed_verify = os.urandom(int(seed_lens[1])) if seed_random else (b'\x00' * int(seed_lens[1]))
        link = self._make_link(selector, trace=trace, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in)
        t0 = time.monotonic()
        self.log(log_cb, 'INFO', f'native usb open selector={selector} chip={chip_name}')
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates, USB_TIMEOUTS_MS['identify'])
            chip_id = parsed.data[0]
            chip_type = parsed.data[1]
            erase_sectors = int(matched_ident.get('erase_sectors') or erase_sectors)
            chip_limit = int(matched_ident.get('max_flash_size') or chip_cfg.get('max_flash_size') or erase_sectors * 1024)
            if len(firmware) > chip_limit:
                raise BackendError(f'firmware too large for {chip_name}: {len(firmware)} > {chip_limit}')
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            opt = parse_read_cfg_response(cfg_raw)
            self._log_cfg(log_cb, 'cfg_initial', opt)
            cfg12_write = bytearray(opt.cfg12)
            cfg12_write[0:2] = b'\xff\xff'
            cfg12_write[4:8] = b'\x00\x00\x00\x00'
            cfg12_write[8:12] = b'\xff\xff\xff\xff'
            code, _ = link.txrx(build_write_cfg(data=bytes(cfg12_write)), CMD_WRITE_CFG, USB_TIMEOUTS_MS['write_cfg'])
            if code != 0x00:
                raise BackendError('write_cfg single failed')
            self.log(log_cb, 'INFO', f'write_cfg single cfg12={bytes(cfg12_write).hex()}')
            code, cfg_raw_after = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg after write failed')
            opt_after = parse_read_cfg_response(cfg_raw_after)
            self._log_cfg(log_cb, 'cfg_after_write', opt_after)
            xor_key, boot_sum_program, key_src = self._unlock_with_seed(link, opt_after, chip_id, seed_program, label='program', timeout_ms=USB_TIMEOUTS_MS['isp_key'])
            self.log(log_cb, 'INFO', f'isp_key ok (src={key_src} key_sum=0x{boot_sum_program:02x})')
            self.log(log_cb, 'INFO', f'stage erase sectors={erase_sectors}')
            code, _ = link.txrx(build_erase(erase_sectors), CMD_ERASE, USB_TIMEOUTS_MS['erase'])
            if code != 0x00:
                raise BackendError('erase failed')
            self.log(log_cb, 'INFO', 'erase ok')
            self.progress(progress_cb, 0, 0, blocks)
            last_ui_pct = -1
            last_log_pct = -1
            self.log(log_cb, 'INFO', f'stage program addr=0x00000000..0x{blocks * CHUNK_SIZE:08x} blocks={blocks} chunk={CHUNK_SIZE}')
            for i in range(blocks):
                addr = i * CHUNK_SIZE
                plain = firmware_padded[addr:addr + CHUNK_SIZE]
                enc = xor_crypt(plain, xor_key)
                code, _ = link.txrx(build_program(addr, 0x00, enc), CMD_PROGRAM, USB_TIMEOUTS_MS['program'])
                if code != 0x00:
                    raise BackendError(f'program failed at 0x{addr:08x}')
                stage_pct = (i + 1) * 100 // blocks
                ui_pct = (i + 1) * 50 // blocks
                if ui_pct != last_ui_pct:
                    last_ui_pct = ui_pct
                    self.progress(progress_cb, ui_pct, i + 1, blocks)
                if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                    last_log_pct = stage_pct
                    self.log(log_cb, 'INFO', f'program {stage_pct}% addr=0x{(i + 1) * CHUNK_SIZE:08x}')
            flush_addr = blocks * CHUNK_SIZE
            code, _ = link.txrx(build_program(flush_addr, 0x00, b''), CMD_PROGRAM, USB_TIMEOUTS_MS['program'])
            if code != 0x00:
                raise BackendError('program_flush failed')
            self.log(log_cb, 'INFO', f'stage program_flush addr=0x{flush_addr:08x}')
            self.progress(progress_cb, 50, blocks, blocks)
            if verify:
                _, boot_sum_verify, key_src_verify = self._unlock_with_seed(link, opt_after, chip_id, seed_verify, label='verify', timeout_ms=USB_TIMEOUTS_MS['isp_key'])
                self.log(log_cb, 'INFO', f'isp_key verify ok (src={key_src_verify} key_sum=0x{boot_sum_verify:02x})')
                self.log(log_cb, 'INFO', f'stage verify blocks={blocks}/{blocks}')
                last_ui_pct = -1
                last_log_pct = -1
                for i in range(blocks):
                    addr = i * CHUNK_SIZE
                    plain = firmware_padded[addr:addr + CHUNK_SIZE]
                    enc = xor_crypt(plain, xor_key)
                    code, _ = link.txrx(build_verify(addr, 0x00, enc), CMD_VERIFY, USB_TIMEOUTS_MS['verify'])
                    if code != 0x00:
                        raise BackendError(f'verify failed at 0x{addr:08x}')
                    stage_pct = (i + 1) * 100 // blocks
                    ui_pct = 50 + ((i + 1) * 50 // blocks)
                    if ui_pct != last_ui_pct:
                        last_ui_pct = ui_pct
                        self.progress(progress_cb, ui_pct, i + 1, blocks)
                    if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                        last_log_pct = stage_pct
                        self.log(log_cb, 'INFO', f'verify {stage_pct}% addr=0x{addr:08x}')
                self.log(log_cb, 'INFO', 'verify ok')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, USB_TIMEOUTS_MS['isp_end'])
            except Exception:
                pass
            self.progress(progress_cb, 100, blocks, blocks)
            total = time.monotonic() - t0
            self.log(log_cb, 'INFO', f'OK total={total:.3f}s')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'usb',
                'protocol_variant': USB_NATIVE_PLAIN.key,
                'usb_selector': selector,
                'bytes': len(firmware),
                'blocks': blocks,
                'duration_s': total,
                'interface_number': link.info.interface_number,
                'endpoint_out': link.info.endpoint_out,
                'endpoint_in': link.info.endpoint_in,
                'seed_program_len': len(seed_program),
                'seed_verify_len': len(seed_verify),
            }
        finally:
            link.close()

    def _unlock_with_seed(self, link: UsbNativeLink, opt, chip_id: int, seed: bytes, *, label: str, timeout_ms: int) -> tuple[bytes, int, str]:
        code, kresp = link.txrx(build_isp_key(seed), CMD_ISP_KEY, timeout_ms)
        if code != 0x00 or len(kresp) < 1:
            raise BackendError(f'isp_key failed ({label})')
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
            msg = f'isp_key checksum mismatch ({label}): boot=0x{boot_sum:02x}'
            for name, key in candidates:
                msg += f' {name}=0x{(sum(key) & 0xFF):02x}'
            raise BackendError(msg)
        picked.sort(key=lambda item: 0 if item[0] == 'uid' else 1)
        key_src, xor_key = picked[0]
        return xor_key, boot_sum, key_src

    def _log_cfg(self, log_cb: LogFn, tag: str, opt) -> None:
        if log_cb is None:
            return
        if len(opt.uid) == 8:
            self.log(log_cb, 'INFO', f'uid={opt.uid.hex("-")}')
        self.log(log_cb, 'INFO', f'{tag} rdpr_user={opt.rdpr_user.hex()} cfg_data={opt.data_bytes.hex()} cfg_wpr={opt.wpr.hex()}')
