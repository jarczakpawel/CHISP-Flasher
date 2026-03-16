from __future__ import annotations

import time

from chisp_flasher.core.errors import BackendError
from chisp_flasher.protocol.commands import CMD_ERASE, CMD_IDENTIFY, CMD_ISP_END, CMD_ISP_KEY, CMD_PROGRAM, CMD_READ_CFG, CMD_SET_BAUD, CMD_VERIFY
from chisp_flasher.protocol.crypto import calc_xor_key_uid, xor_crypt
from chisp_flasher.protocol.framing import pack_request
from chisp_flasher.protocol.native_usb import build_erase, build_identify, build_isp_end, build_program, build_read_cfg, build_set_baud, build_verify
from chisp_flasher.protocol.variants import LEGACY_UART_NATIVE_WRAPPED, USB_NATIVE_PLAIN
from chisp_flasher.transport.serial_link import SerialLink
from chisp_flasher.backends.wch_legacy_usb import Backend as LegacyUsbBackend, CHUNK_SIZE



def _fmt_pin_value(value) -> str:
    if isinstance(value, str):
        return value.replace('_', '/')
    if isinstance(value, (list, tuple)):
        return ', '.join(_fmt_pin_value(x) for x in value if x)
    return ''


def _fmt_entry_mode(value: str) -> str:
    mapping = {
        'no_key_serial': 'no-key serial window after power-on',
        'no_key_serial_only': 'no-key serial only',
        'forced_wait_40ms_only': 'forced 40ms serial wait after power-on',
    }
    return mapping.get(str(value or '').strip(), str(value or '').strip().replace('_', ' '))


def _manual_entry_hint(chip_name: str, chip_meta: dict | None) -> str:
    chip_meta = dict(chip_meta or {})
    cross = dict(chip_meta.get('public_cross_check') or {})
    parts: list[str] = []
    serial_uart = cross.get('serial_uart')
    serial_uart_options = cross.get('serial_uart_options')
    boot_pin = cross.get('boot_pin_default')
    entry_mode = cross.get('serial_entry_mode')
    isp_level = cross.get('isp_default_level')
    usb_isp = cross.get('usb_isp')
    usb_pins = cross.get('usb_pins')
    if serial_uart:
        parts.append(f'UART={_fmt_pin_value(serial_uart)}')
    elif serial_uart_options:
        parts.append(f'UART options={_fmt_pin_value(serial_uart_options)}')
    if boot_pin and str(boot_pin).strip().lower() not in {'none', 'n/a'}:
        parts.append(f'boot={_fmt_pin_value(boot_pin)}')
    if isp_level:
        parts.append(f'default ISP level={_fmt_pin_value(isp_level)}')
    if entry_mode:
        parts.append(f'entry={_fmt_entry_mode(str(entry_mode))}')
    if usb_isp is False:
        parts.append('USB ISP=no')
    elif usb_pins:
        parts.append(f'USB ISP pins={_fmt_pin_value(usb_pins)}')
    if not parts:
        return ''
    return f'manual entry hint for {chip_name}: ' + '; '.join(parts)

SERIAL_TIMEOUTS_S = {
    'identify': 1.0,
    'read_cfg': 1.2,
    'isp_key': 1.2,
    'erase': 20.0,
    'program': 2.5,
    'verify': 2.5,
    'set_baud': 1.2,
    'isp_end': 1.2,
}


class Backend(LegacyUsbBackend):
    family_name = 'wch_legacy_uart'
    supports_config_write_native_usb = False
    supports_config_write_uart_framed = False

    def supported_protocol_variants(self) -> list[str]:
        return [LEGACY_UART_NATIVE_WRAPPED.key, USB_NATIVE_PLAIN.key]

    def _make_serial_link(self, port: str, *, baud: int, trace: bool) -> SerialLink:
        return SerialLink(port=port, baud=baud, parity='N', trace=trace)

    def _wrap(self, payload: bytes) -> bytes:
        return pack_request(payload)

    def _txrx_native(self, link: SerialLink, payload: bytes, expect_cmd: int, timeout_s: float):
        return link.txrx(self._wrap(payload), expect_cmd, timeout_s)

    def _build_legacy_key_payload(self) -> bytes:
        return self._build_legacy_key_packet()

    def _normalize_serial_mode(self, mode: str) -> str:
        mode = (mode or 'ttl').strip().lower()
        if mode not in {'ttl', 'usb'}:
            raise BackendError(f'bad mode: {mode}')
        if mode != 'ttl':
            raise BackendError('legacy UART auto-di is not implemented yet; use manual serial boot entry')
        return mode

    def _require_port(self, port: str) -> str:
        port = (port or '').strip()
        if not port:
            raise BackendError('no port selected')
        return port

    def _probe_identify_serial(self, link: SerialLink, identify_candidates: list[dict]):
        last_err = None
        for expect in identify_candidates:
            try:
                code, data = self._txrx_native(
                    link,
                    build_identify(int(expect['identify_device_id']), int(expect['device_type'])),
                    CMD_IDENTIFY,
                    SERIAL_TIMEOUTS_S['identify'],
                )
                if code != 0x00:
                    last_err = BackendError(f'identify rejected: code=0x{code:02x}')
                    continue
                if len(data) < 2:
                    last_err = BackendError(f'identify response too short: {len(data)}')
                    continue
                chip_id = data[0]
                chip_type = data[1]
                if chip_id == int(expect['identify_device_id']) and chip_type == int(expect['device_type']):
                    return chip_id, chip_type, expect
                last_err = BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            except Exception as e:
                last_err = e
        if last_err is None:
            raise BackendError('identify failed')
        raise last_err

    def _open_and_identify_serial(self, chip_name: str, *, mode: str, port: str, baud: int, trace: bool, chip_meta=None, log_cb=None):
        self._normalize_serial_mode(mode)
        port = self._require_port(port)
        chip_cfg = self._chip_cfg(chip_name)
        identify_candidates = self._normalize_identify_candidates(chip_cfg)
        link = self._make_serial_link(port, baud=baud, trace=trace)
        self.log(log_cb, 'INFO', f'open port={port} mode=ttl chip={chip_name} host_baud={int(baud)}')
        link.open()
        try:
            link.flush()
            hint = _manual_entry_hint(chip_name, chip_meta)
            if hint:
                self.log(log_cb, 'INFO', hint)
            self.log(log_cb, 'ACTION', 'legacy UART manual mode: enter bootloader now (hold BOOT / set download pin, tap RESET or power-cycle). waiting for ISP...')
            chip_id, chip_type, matched_ident = self._probe_identify_serial(link, identify_candidates)
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            return link, chip_cfg, chip_id, chip_type, matched_ident, port
        except Exception:
            link.close()
            raise

    def _unlock_serial(self, link: SerialLink, cfg, chip_id: int, *, log_cb=None, verify_stage: bool = False) -> bytes:
        if len(cfg.uid) != 8:
            raise BackendError('uid length is not 8 bytes')
        xor_key = calc_xor_key_uid(cfg.uid, chip_id)
        expected = sum(xor_key) & 0xFF
        code, kresp = self._txrx_native(link, self._build_legacy_key_payload(), CMD_ISP_KEY, SERIAL_TIMEOUTS_S['isp_key'])
        if code != 0x00 or len(kresp) < 1:
            raise BackendError('isp_key failed')
        got = kresp[0] & 0xFF
        if got != expected:
            raise BackendError(f'isp_key checksum mismatch: boot=0x{got:02x} host=0x{expected:02x}')
        stage = 'verify' if verify_stage else 'program'
        self.log(log_cb, 'INFO', f'isp_key {stage} ok (uid key_sum=0x{got:02x})')
        return xor_key

    def detect_uart_framed(self, chip_name: str, *, mode: str = 'ttl', port: str = '', baud: int = 115200, trace: bool = False, log_cb=None, **_kwargs) -> dict:
        link, _chip_cfg, chip_id, chip_type, _matched_ident, port = self._open_and_identify_serial(chip_name, mode=mode, port=port, baud=baud, trace=trace, chip_meta=_kwargs.get('chip_meta'), log_cb=log_cb)
        try:
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'serial',
                'protocol_variant': LEGACY_UART_NATIVE_WRAPPED.key,
                'port': port,
                'baud': int(baud),
                'chip_id': chip_id,
                'chip_type': chip_type,
            }
        finally:
            link.close()

    def read_config_uart_framed(self, chip_name: str, *, mode: str = 'ttl', port: str = '', baud: int = 115200, trace: bool = False, log_cb=None, **_kwargs) -> dict:
        link, _chip_cfg, chip_id, chip_type, _matched_ident, port = self._open_and_identify_serial(chip_name, mode=mode, port=port, baud=baud, trace=trace, chip_meta=_kwargs.get('chip_meta'), log_cb=log_cb)
        try:
            code, cfg_raw = self._txrx_native(link, build_read_cfg(), CMD_READ_CFG, SERIAL_TIMEOUTS_S['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'serial',
                'protocol_variant': LEGACY_UART_NATIVE_WRAPPED.key,
                'port': port,
                'baud': int(baud),
                'chip_id': chip_id,
                'chip_type': chip_type,
                'uid_hex': cfg.uid.hex(),
                'cfg12_hex': cfg.cfg12.hex(),
                'reserved_hex': cfg.reserved.hex(),
                'wprotect_hex': cfg.wprotect.hex(),
                'user_cfg_hex': cfg.user_cfg.hex(),
                'raw_hex': cfg.raw_response.hex(),
                'btver_raw_hex': cfg.btver.hex(),
                'family_kind': cfg.family_kind,
            }
        finally:
            link.close()

    def write_config_uart_framed(self, chip_name: str, *, config, **_kwargs) -> dict:
        _ = chip_name, config
        raise BackendError('legacy UART config write is not implemented')

    def erase_uart_framed(self, chip_name: str, *, mode: str = 'ttl', port: str = '', baud: int = 115200, trace: bool = False, log_cb=None, progress_cb=None, **_kwargs) -> dict:
        link, chip_cfg, chip_id, chip_type, _matched_ident, port = self._open_and_identify_serial(chip_name, mode=mode, port=port, baud=baud, trace=trace, chip_meta=_kwargs.get('chip_meta'), log_cb=log_cb)
        t0 = time.monotonic()
        try:
            code, cfg_raw = self._txrx_native(link, build_read_cfg(), CMD_READ_CFG, SERIAL_TIMEOUTS_S['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            self._unlock_serial(link, cfg, chip_id, log_cb=log_cb)
            erase_kib = max(8, (int(chip_cfg['max_flash_size']) + 1023) // 1024)
            self.log(log_cb, 'INFO', f'stage erase blocks_kib={erase_kib}')
            code, _ = self._txrx_native(link, build_erase(erase_kib), CMD_ERASE, SERIAL_TIMEOUTS_S['erase'])
            if code != 0x00:
                raise BackendError('erase failed')
            try:
                self._txrx_native(link, build_isp_end(0), CMD_ISP_END, SERIAL_TIMEOUTS_S['isp_end'])
            except Exception:
                pass
            total = time.monotonic() - t0
            self.progress(progress_cb, 100, 1, 1)
            self.log(log_cb, 'INFO', f'erase-only ok total={total:.3f}s')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'serial',
                'protocol_variant': LEGACY_UART_NATIVE_WRAPPED.key,
                'port': port,
                'baud': int(link.baud),
                'chip_id': chip_id,
                'chip_type': chip_type,
                'duration_s': total,
            }
        finally:
            link.close()

    def verify_uart_framed(self, chip_name: str, firmware_path: str, *, mode: str = 'ttl', port: str = '', baud: int = 115200, trace: bool = False, log_cb=None, progress_cb=None, **_kwargs) -> dict:
        chip_cfg = self._chip_cfg(chip_name)
        firmware = self.require_file(firmware_path, chip_name=chip_name, max_size=int(chip_cfg['max_flash_size']))
        firmware_padded = self._pad_firmware(firmware)
        blocks = (len(firmware_padded) + CHUNK_SIZE - 1) // CHUNK_SIZE
        link, _chip_cfg, chip_id, chip_type, _matched_ident, port = self._open_and_identify_serial(chip_name, mode=mode, port=port, baud=baud, trace=trace, chip_meta=_kwargs.get('chip_meta'), log_cb=log_cb)
        t0 = time.monotonic()
        try:
            code, cfg_raw = self._txrx_native(link, build_read_cfg(), CMD_READ_CFG, SERIAL_TIMEOUTS_S['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            xor_key = self._unlock_serial(link, cfg, chip_id, log_cb=log_cb, verify_stage=True)
            self.progress(progress_cb, 0, 0, max(blocks, 1))
            last_ui_pct = -1
            last_log_pct = -1
            for i in range(blocks):
                addr = i * CHUNK_SIZE
                plain = firmware_padded[addr:addr + CHUNK_SIZE]
                enc = xor_crypt(plain, xor_key)
                code, _ = self._txrx_native(link, build_verify(addr, 0x00, enc), CMD_VERIFY, SERIAL_TIMEOUTS_S['verify'])
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
                self._txrx_native(link, build_isp_end(0), CMD_ISP_END, SERIAL_TIMEOUTS_S['isp_end'])
            except Exception:
                pass
            total = time.monotonic() - t0
            self.progress(progress_cb, 100, blocks, blocks)
            self.log(log_cb, 'INFO', f'verify-only ok total={total:.3f}s')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'serial',
                'protocol_variant': LEGACY_UART_NATIVE_WRAPPED.key,
                'port': port,
                'baud': int(link.baud),
                'chip_id': chip_id,
                'chip_type': chip_type,
                'bytes': len(firmware),
                'blocks': blocks,
                'duration_s': total,
            }
        finally:
            link.close()

    def flash_uart_framed(self, chip_name: str, firmware_path: str, *, mode: str = 'ttl', port: str = '', baud: int = 115200, fast_baud: int = 115200, no_fast: bool = False, verify: bool = True, trace: bool = False, log_cb=None, progress_cb=None, **_kwargs) -> dict:
        chip_cfg = self._chip_cfg(chip_name)
        firmware = self.require_file(firmware_path, chip_name=chip_name, max_size=int(chip_cfg['max_flash_size']))
        firmware_padded = self._pad_firmware(firmware)
        blocks = (len(firmware_padded) + CHUNK_SIZE - 1) // CHUNK_SIZE
        erase_kib = max(8, (len(firmware_padded) + 1023) // 1024)
        link, _chip_cfg, chip_id, chip_type, _matched_ident, port = self._open_and_identify_serial(chip_name, mode=mode, port=port, baud=baud, trace=trace, chip_meta=_kwargs.get('chip_meta'), log_cb=log_cb)
        t0 = time.monotonic()
        try:
            code, cfg_raw = self._txrx_native(link, build_read_cfg(), CMD_READ_CFG, SERIAL_TIMEOUTS_S['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            xor_key = self._unlock_serial(link, cfg, chip_id, log_cb=log_cb)
            self.log(log_cb, 'INFO', f'stage erase blocks_kib={erase_kib}')
            code, _ = self._txrx_native(link, build_erase(erase_kib), CMD_ERASE, SERIAL_TIMEOUTS_S['erase'])
            if code != 0x00:
                raise BackendError('erase failed')
            self.log(log_cb, 'INFO', 'erase ok')

            if not no_fast:
                try:
                    fast_baud = int(fast_baud)
                except Exception:
                    fast_baud = int(baud)
                if fast_baud > int(baud):
                    self.log(log_cb, 'INFO', f'stage set_baud mcu={fast_baud}')
                    code, _ = self._txrx_native(link, build_set_baud(fast_baud), CMD_SET_BAUD, SERIAL_TIMEOUTS_S['set_baud'])
                    if code != 0x00:
                        raise BackendError('set_baud failed')
                    time.sleep(0.03)
                    link.set_baud(fast_baud)
                    link.flush()
                    self.log(log_cb, 'INFO', f'set_baud ok host_baud={link.baud}')

            self.progress(progress_cb, 0, 0, max(blocks, 1))
            last_ui_pct = -1
            last_log_pct = -1
            self.log(log_cb, 'INFO', f'stage program addr=0x00000000..0x{len(firmware_padded):08x} blocks={blocks} chunk={CHUNK_SIZE} verify={"on" if verify else "off"}')
            for i in range(blocks):
                addr = i * CHUNK_SIZE
                plain = firmware_padded[addr:addr + CHUNK_SIZE]
                enc = xor_crypt(plain, xor_key)
                code, _ = self._txrx_native(link, build_program(addr, 0x00, enc), CMD_PROGRAM, SERIAL_TIMEOUTS_S['program'])
                if code != 0x00:
                    raise BackendError(f'program failed at 0x{addr:08x}')
                stage_pct = (i + 1) * 100 // blocks
                ui_pct = (i + 1) * 50 // blocks if verify else stage_pct
                if ui_pct != last_ui_pct:
                    last_ui_pct = ui_pct
                    self.progress(progress_cb, ui_pct, i + 1, blocks)
                if log_cb is not None and (stage_pct % 10) == 0 and stage_pct != last_log_pct:
                    last_log_pct = stage_pct
                    self.log(log_cb, 'INFO', f'program {stage_pct}% addr=0x{(i + 1) * CHUNK_SIZE:08x}')
            self.progress(progress_cb, 50 if verify else 100, blocks, blocks)

            if verify:
                self._unlock_serial(link, cfg, chip_id, log_cb=log_cb, verify_stage=True)
                last_ui_pct = -1
                last_log_pct = -1
                for i in range(blocks):
                    addr = i * CHUNK_SIZE
                    plain = firmware_padded[addr:addr + CHUNK_SIZE]
                    enc = xor_crypt(plain, xor_key)
                    code, _ = self._txrx_native(link, build_verify(addr, 0x00, enc), CMD_VERIFY, SERIAL_TIMEOUTS_S['verify'])
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
                self._txrx_native(link, build_isp_end(0), CMD_ISP_END, SERIAL_TIMEOUTS_S['isp_end'])
            except Exception:
                pass
            self.progress(progress_cb, 100, blocks, blocks)
            total = time.monotonic() - t0
            self.log(log_cb, 'INFO', f'OK total={total:.3f}s')
            return {
                'chip': chip_name,
                'backend': self.family_name,
                'transport': 'serial',
                'protocol_variant': LEGACY_UART_NATIVE_WRAPPED.key,
                'port': port,
                'baud': int(link.baud),
                'chip_id': chip_id,
                'chip_type': chip_type,
                'bytes': len(firmware),
                'blocks': blocks,
                'duration_s': total,
            }
        finally:
            link.close()
