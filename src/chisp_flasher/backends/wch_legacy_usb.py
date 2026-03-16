from __future__ import annotations

import time
from dataclasses import dataclass

from chisp_flasher.backends.base import BackendBase, LogFn, ProgressFn
from chisp_flasher.chipdb.loader import load_chipdb
from chisp_flasher.core.errors import BackendError, FrameError
from chisp_flasher.protocol.commands import CMD_ERASE, CMD_IDENTIFY, CMD_ISP_END, CMD_ISP_KEY, CMD_PROGRAM, CMD_READ_CFG, CMD_VERIFY
from chisp_flasher.protocol.crypto import calc_xor_key_uid, xor_crypt
from chisp_flasher.protocol.native_usb import build_erase, build_identify, build_isp_end, build_program, build_read_cfg, build_verify, make_frame
from chisp_flasher.protocol.variants import USB_NATIVE_PLAIN
from chisp_flasher.transport.usb_native import UsbNativeLink

CHUNK_SIZE = 56
KEY_REQUEST_LEN = 0x1E
USB_TIMEOUTS_MS = {
    'identify': 1000,
    'read_cfg': 1200,
    'isp_key': 1200,
    'erase': 20000,
    'program': 2500,
    'verify': 2500,
    'isp_end': 1200,
}


def _build_package_aliases() -> dict[str, str]:
    try:
        chipdb = load_chipdb()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for chip_name, chip_meta in chipdb.chips.items():
        cross = dict(chip_meta.get('public_cross_check') or {})
        base = str(cross.get('package_profile_of') or '').strip()
        if base and str(chip_name).startswith('CH5') and str(base).startswith('CH5'):
            out[str(chip_name)] = base
    return out


LEGACY_PACKAGE_ALIASES = _build_package_aliases()


@dataclass(slots=True)
class LegacyConfigState:
    raw_response: bytes
    family_kind: str
    mask_echo: bytes
    uid: bytes
    btver: bytes
    cfg12: bytes
    reserved: bytes
    wprotect: bytes
    user_cfg: bytes


def _fmt_btver(btver: bytes) -> str:
    if len(btver) == 4 and btver[0] == 0x00 and btver[3] == 0x00:
        return f'{btver[1]:02d}.{btver[2]:02d}'
    return btver.hex()


class Backend(BackendBase):
    family_name = 'wch_legacy_usb'
    supports_config_write_native_usb = False

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

    def _canonical_chip_name(self, chip_name: str) -> str:
        chip_name = str(chip_name or '').strip()
        return LEGACY_PACKAGE_ALIASES.get(chip_name, chip_name)

    def _chip_cfg(self, chip_name: str) -> dict:
        base_chip = self._canonical_chip_name(chip_name)
        cfg = self.chip_native_defaults.get(base_chip)
        if not cfg:
            raise BackendError(f'unsupported legacy chip config: {chip_name}')
        return dict(cfg)


    chip_native_defaults = {
        'CH540': {
            'identify_device_id': 0x40,
            'device_type': 0x12,
            'max_flash_size': 10240,
            'family_kind': 'f12',
        },
        'CH541': {
            'identify_device_id': 0x41,
            'device_type': 0x12,
            'max_flash_size': 14336,
            'family_kind': 'f12',
        },
        'CH542': {
            'identify_device_id': 0x42,
            'device_type': 0x12,
            'max_flash_size': 14336,
            'family_kind': 'f12',
        },
        'CH543': {
            'identify_device_id': 0x43,
            'device_type': 0x12,
            'max_flash_size': 14336,
            'family_kind': 'f12',
        },
        'CH544': {
            'identify_device_id': 0x44,
            'device_type': 0x12,
            'max_flash_size': 61440,
            'family_kind': 'f12',
        },
        'CH545': {
            'identify_device_id': 0x45,
            'device_type': 0x12,
            'max_flash_size': 61440,
            'family_kind': 'f12',
        },
        'CH546': {
            'identify_device_id': 0x46,
            'device_type': 0x12,
            'max_flash_size': 32768,
            'family_kind': 'f12',
        },
        'CH547': {
            'identify_device_id': 0x47,
            'device_type': 0x12,
            'max_flash_size': 61440,
            'family_kind': 'f12',
        },
        'CH548': {
            'identify_device_id': 0x48,
            'device_type': 0x12,
            'max_flash_size': 32768,
            'family_kind': 'f12',
        },
        'CH549': {
            'identify_device_id': 0x49,
            'device_type': 0x12,
            'max_flash_size': 61440,
            'family_kind': 'f12',
        },
        'CH551': {
            'identify_device_id': 0x51,
            'device_type': 0x11,
            'max_flash_size': 10240,
            'family_kind': 'f11',
        },
        'CH552': {
            'identify_device_id': 0x52,
            'device_type': 0x11,
            'max_flash_size': 14336,
            'family_kind': 'f11',
        },
        'CH553': {
            'identify_device_id': 0x53,
            'device_type': 0x11,
            'max_flash_size': 10240,
            'family_kind': 'f11',
        },
        'CH554': {
            'identify_device_id': 0x54,
            'device_type': 0x11,
            'max_flash_size': 14336,
            'family_kind': 'f11',
        },
        'CH555': {
            'identify_device_id': 0x55,
            'device_type': 0x11,
            'max_flash_size': 61440,
            'family_kind': 'f11',
        },
        'CH556': {
            'identify_device_id': 0x56,
            'device_type': 0x11,
            'max_flash_size': 61440,
            'family_kind': 'f11',
        },
        'CH557': {
            'identify_device_id': 0x57,
            'device_type': 0x11,
            'max_flash_size': 61440,
            'family_kind': 'f11',
        },
        'CH558': {
            'identify_device_id': 0x58,
            'device_type': 0x11,
            'max_flash_size': 32768,
            'family_kind': 'f11',
        },
        'CH559': {
            'identify_device_id': 0x59,
            'device_type': 0x11,
            'max_flash_size': 61440,
            'family_kind': 'f11',
        },
        'CH563': {
            'identify_device_id': 0x63,
            'device_type': 0x10,
            'identify_candidates': [
                {'identify_device_id': 0x63, 'device_type': 0x10},
                {'identify_device_id': 0x42, 'device_type': 0x10},
                {'identify_device_id': 0x43, 'device_type': 0x10},
                {'identify_device_id': 0x44, 'device_type': 0x10},
                {'identify_device_id': 0x45, 'device_type': 0x10},
            ],
            'max_flash_size': 229376,
            'family_kind': 'f10',
        },
        'CH565': {
            'identify_device_id': 0x65,
            'device_type': 0x10,
            'max_flash_size': 458752,
            'family_kind': 'f10',
        },
        'CH566': {
            'identify_device_id': 0x66,
            'device_type': 0x10,
            'max_flash_size': 65536,
            'family_kind': 'f10',
        },
        'CH567': {
            'identify_device_id': 0x67,
            'device_type': 0x10,
            'max_flash_size': 196608,
            'family_kind': 'f10',
        },
        'CH568': {
            'identify_device_id': 0x68,
            'device_type': 0x10,
            'max_flash_size': 196608,
            'family_kind': 'f10',
        },
        'CH569': {
            'identify_device_id': 0x69,
            'device_type': 0x10,
            'max_flash_size': 458752,
            'family_kind': 'f10',
        },
        'CH570': {
            'identify_device_id': 0x70,
            'device_type': 0x13,
            'max_flash_size': 245760,
            'family_kind': 'f13',
        },
        'CH571': {
            'identify_device_id': 0x71,
            'device_type': 0x13,
            'max_flash_size': 196608,
            'family_kind': 'f13',
        },
        'CH572': {
            'identify_device_id': 0x72,
            'device_type': 0x13,
            'max_flash_size': 245760,
            'family_kind': 'f13',
        },
        'CH573': {
            'identify_device_id': 0x73,
            'device_type': 0x13,
            'max_flash_size': 458752,
            'family_kind': 'f13',
        },
        'CH577': {
            'identify_device_id': 0x77,
            'device_type': 0x13,
            'max_flash_size': 131072,
            'family_kind': 'f13',
        },
        'CH578': {
            'identify_device_id': 0x58,
            'device_type': 0x13,
            'max_flash_size': 163840,
            'family_kind': 'f13',
        },
        'CH579': {
            'identify_device_id': 0x79,
            'device_type': 0x13,
            'max_flash_size': 256000,
            'family_kind': 'f13',
        },
        'CH581': {
            'identify_device_id': 0x81,
            'device_type': 0x16,
            'max_flash_size': 196608,
            'family_kind': 'f22',
        },
        'CH582': {
            'identify_device_id': 0x82,
            'device_type': 0x16,
            'max_flash_size': 458752,
            'family_kind': 'f22',
        },
        'CH583': {
            'identify_device_id': 0x83,
            'device_type': 0x16,
            'max_flash_size': 458752,
            'family_kind': 'f22',
        },
        'CH584': {
            'identify_device_id': 0x84,
            'device_type': 0x16,
            'max_flash_size': 458752,
            'family_kind': 'f22',
        },
        'CH585': {
            'identify_device_id': 0x85,
            'device_type': 0x16,
            'max_flash_size': 458752,
            'family_kind': 'f22',
        },
        'CH591': {
            'identify_device_id': 0x91,
            'device_type': 0x22,
            'max_flash_size': 196608,
            'family_kind': 'f22',
        },
        'CH592': {
            'identify_device_id': 0x92,
            'device_type': 0x22,
            'max_flash_size': 458752,
            'family_kind': 'f22',
        },
    }

    def _normalize_identify_candidates(self, chip_cfg: dict) -> list[dict]:
        raw = chip_cfg.get('identify_candidates')
        seq = raw if isinstance(raw, (list, tuple)) else []
        out: list[dict] = []
        seen: set[tuple[int, int]] = set()
        if not seq:
            seq = [{'identify_device_id': chip_cfg['identify_device_id'], 'device_type': chip_cfg['device_type']}]
        for item in seq:
            try:
                chip_id = int(item.get('identify_device_id', item.get('chip_id'))) & 0xFF
                chip_type = int(item.get('device_type', item.get('chip_type'))) & 0xFF
            except Exception:
                continue
            pair = (chip_id, chip_type)
            if pair in seen:
                continue
            seen.add(pair)
            out.append({'identify_device_id': chip_id, 'device_type': chip_type})
        if not out:
            out.append({'identify_device_id': int(chip_cfg['identify_device_id']) & 0xFF, 'device_type': int(chip_cfg['device_type']) & 0xFF})
        return out

    def _probe_identify_native(self, link: UsbNativeLink, identify_candidates: list[dict]):
        last_err = None
        for cand in identify_candidates:
            try:
                parsed = link.txrx_frame(build_identify(int(cand['identify_device_id']), int(cand['device_type'])), CMD_IDENTIFY, USB_TIMEOUTS_MS['identify'])
                if parsed.code != 0x00 or len(parsed.data) < 2:
                    last_err = BackendError('identify failed')
                    continue
                chip_id = parsed.data[0]
                chip_type = parsed.data[1]
                for expect in identify_candidates:
                    if chip_id == int(expect['identify_device_id']) and chip_type == int(expect['device_type']):
                        return parsed, expect
                last_err = BackendError(f'unexpected chip_id/type: 0x{chip_id:02x}/0x{chip_type:02x}')
            except Exception as e:
                last_err = e
        if last_err is None:
            raise BackendError('identify failed')
        raise last_err



    def supported_protocol_variants(self) -> list[str]:
        return [USB_NATIVE_PLAIN.key]

    def detect_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, **_kwargs) -> dict:
        chip_cfg = self._chip_cfg(chip_name)
        selector = (usb_selector or '').strip()
        if not selector:
            raise BackendError('usb device selector is empty')
        identify_candidates = self._normalize_identify_candidates(chip_cfg)
        self.log(log_cb, 'INFO', f'native usb open selector={selector} chip={chip_name}')
        link = self._make_link(selector, trace=trace, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in)
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates)
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

    def read_config_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, **_kwargs) -> dict:
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb)
        chip_cfg = self._chip_cfg(chip_name)
        identify_candidates = self._normalize_identify_candidates(chip_cfg)
        link = self._make_link(str(detected['usb_selector']), trace=trace, usb_interface_number=detected.get('interface_number'), usb_endpoint_out=detected.get('endpoint_out'), usb_endpoint_in=detected.get('endpoint_in'))
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates)
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            return {
                **detected,
                'uid_hex': cfg.uid.hex(),
                'btver_hex': cfg.btver.hex(),
                'btver_text': _fmt_btver(cfg.btver),
                'raw_hex': cfg.raw_response.hex(),
                'cfg12_hex': cfg.cfg12.hex(),
                'reserved_hex': cfg.reserved.hex(),
                'wprotect_hex': cfg.wprotect.hex(),
                'user_cfg_hex': cfg.user_cfg.hex(),
                'family_kind': cfg.family_kind,
            }
        finally:
            link.close()

    def write_config_native_usb(self, chip_name: str, *, config, **_kwargs) -> dict:
        raise BackendError(f'config write is not implemented for {chip_name} yet')

    def erase_native_usb(self, chip_name: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, progress_cb: ProgressFn = None, **_kwargs) -> dict:
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb)
        chip_cfg = self._chip_cfg(chip_name)
        identify_candidates = self._normalize_identify_candidates(chip_cfg)
        erase_kib = max(8, int(chip_cfg['max_flash_size']) // 1024)
        t0 = time.monotonic()
        link = self._make_link(str(detected['usb_selector']), trace=trace, usb_interface_number=detected.get('interface_number'), usb_endpoint_out=detected.get('endpoint_out'), usb_endpoint_in=detected.get('endpoint_in'))
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates)
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            self._unlock(link, cfg, parsed.data[0], log_cb=log_cb)
            self.log(log_cb, 'INFO', f'stage erase blocks_kib={erase_kib}')
            code, _ = link.txrx(build_erase(erase_kib), CMD_ERASE, USB_TIMEOUTS_MS['erase'])
            if code != 0x00:
                raise BackendError('erase failed')
            try:
                link.txrx(build_isp_end(0), CMD_ISP_END, USB_TIMEOUTS_MS['isp_end'])
            except Exception:
                pass
            self.progress(progress_cb, 100, 1, 1)
            total = time.monotonic() - t0
            self.log(log_cb, 'INFO', f'erase-only ok total={total:.3f}s')
            return {**detected, 'duration_s': total, 'erase_blocks_kib': erase_kib, 'interface_number': link.info.interface_number, 'endpoint_out': link.info.endpoint_out, 'endpoint_in': link.info.endpoint_in}
        finally:
            link.close()

    def verify_native_usb(self, chip_name: str, firmware_path: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, trace: bool = False, log_cb: LogFn = None, progress_cb: ProgressFn = None, **_kwargs) -> dict:
        chip_cfg = self._chip_cfg(chip_name)
        firmware = self.require_file(firmware_path, chip_name=chip_name, max_size=int(chip_cfg['max_flash_size']))
        firmware_padded = self._pad_firmware(firmware)
        blocks = (len(firmware_padded) + CHUNK_SIZE - 1) // CHUNK_SIZE
        detected = self.detect_native_usb(chip_name, usb_selector=usb_selector, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in, trace=trace, log_cb=log_cb)
        identify_candidates = self._normalize_identify_candidates(chip_cfg)
        t0 = time.monotonic()
        link = self._make_link(str(detected['usb_selector']), trace=trace, usb_interface_number=detected.get('interface_number'), usb_endpoint_out=detected.get('endpoint_out'), usb_endpoint_in=detected.get('endpoint_in'))
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates)
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            xor_key = self._unlock(link, cfg, parsed.data[0], log_cb=log_cb)
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

    def flash_native_usb(self, chip_name: str, firmware_path: str, *, usb_selector: str, usb_interface_number=None, usb_endpoint_out=None, usb_endpoint_in=None, verify: bool = True, trace: bool = False, log_cb: LogFn = None, progress_cb: ProgressFn = None, **_kwargs) -> dict:
        chip_cfg = self._chip_cfg(chip_name)
        firmware = self.require_file(firmware_path, chip_name=chip_name, max_size=int(chip_cfg['max_flash_size']))
        firmware_padded = self._pad_firmware(firmware)
        blocks = (len(firmware_padded) + CHUNK_SIZE - 1) // CHUNK_SIZE
        erase_kib = max(8, (len(firmware_padded) + 1023) // 1024)
        selector = (usb_selector or '').strip()
        if not selector:
            raise BackendError('usb device selector is empty')
        identify_candidates = self._normalize_identify_candidates(chip_cfg)
        link = self._make_link(selector, trace=trace, usb_interface_number=usb_interface_number, usb_endpoint_out=usb_endpoint_out, usb_endpoint_in=usb_endpoint_in)
        t0 = time.monotonic()
        self.log(log_cb, 'INFO', f'native usb open selector={selector} chip={chip_name}')
        link.open()
        try:
            link.flush()
            parsed, matched_ident = self._probe_identify_native(link, identify_candidates)
            chip_id = parsed.data[0]
            chip_type = parsed.data[1]
            self.log(log_cb, 'INFO', f'identify ok chip_id=0x{chip_id:02x} chip_type=0x{chip_type:02x}')
            code, cfg_raw = link.txrx(build_read_cfg(), CMD_READ_CFG, USB_TIMEOUTS_MS['read_cfg'])
            if code != 0x00:
                raise BackendError('read_cfg failed')
            cfg = self._parse_cfg_response(chip_name, cfg_raw)
            self._log_cfg(log_cb, cfg)
            xor_key = self._unlock(link, cfg, chip_id, log_cb=log_cb)
            self.log(log_cb, 'INFO', f'stage erase blocks_kib={erase_kib}')
            code, _ = link.txrx(build_erase(erase_kib), CMD_ERASE, USB_TIMEOUTS_MS['erase'])
            if code != 0x00:
                raise BackendError('erase failed')
            self.log(log_cb, 'INFO', 'erase ok')
            self.progress(progress_cb, 0, 0, blocks)
            last_ui_pct = -1
            last_log_pct = -1
            self.log(log_cb, 'INFO', f'stage program addr=0x00000000..0x{len(firmware_padded):08x} blocks={blocks} chunk={CHUNK_SIZE}')
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
            self.progress(progress_cb, 50, blocks, blocks)
            if verify:
                self._unlock(link, cfg, chip_id, log_cb=log_cb, verify_stage=True)
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
            }
        finally:
            link.close()

    def _pad_firmware(self, firmware: bytes) -> bytes:
        if not firmware:
            return b''
        aligned = (len(firmware) + 7) & ~7
        return firmware + (b'\xff' * (aligned - len(firmware)))

    def _parse_cfg_response(self, chip_name: str, data: bytes) -> LegacyConfigState:
        family_kind = str(self._chip_cfg(chip_name)['family_kind'])
        if len(data) < 2:
            raise FrameError(f'READ_CFG response too short: {len(data)}')
        mask_echo = bytes(data[:2])
        payload = bytes(data[2:])
        if family_kind in ('f10', 'f13'):
            if len(payload) < 24:
                raise FrameError(f'READ_CFG {family_kind} response too short: {len(payload)}')
            return LegacyConfigState(
                raw_response=bytes(data),
                family_kind=family_kind,
                mask_echo=mask_echo,
                uid=payload[16:24],
                btver=payload[12:16],
                cfg12=payload[:12],
                reserved=b'',
                wprotect=b'',
                user_cfg=b'',
            )
        if family_kind in ('f11', 'f12', 'f22'):
            if len(payload) < 24:
                raise FrameError(f'READ_CFG {family_kind} response too short: {len(payload)}')
            return LegacyConfigState(
                raw_response=bytes(data),
                family_kind=family_kind,
                mask_echo=mask_echo,
                uid=payload[16:24],
                btver=payload[12:16],
                cfg12=b'',
                reserved=payload[0:4],
                wprotect=payload[4:8],
                user_cfg=payload[8:12],
            )
        raise BackendError(f'unsupported family kind: {family_kind}')

    def _build_legacy_key_packet(self) -> bytes:
        return make_frame(CMD_ISP_KEY, b'\x00' * KEY_REQUEST_LEN)

    def _unlock(self, link: UsbNativeLink, cfg: LegacyConfigState, chip_id: int, *, log_cb: LogFn = None, verify_stage: bool = False) -> bytes:
        if len(cfg.uid) != 8:
            raise BackendError('uid length is not 8 bytes')
        xor_key = calc_xor_key_uid(cfg.uid, chip_id)
        expected = sum(xor_key) & 0xFF
        code, kresp = link.txrx(self._build_legacy_key_packet(), CMD_ISP_KEY, USB_TIMEOUTS_MS['isp_key'])
        if code != 0x00 or len(kresp) < 1:
            raise BackendError('isp_key failed')
        got = kresp[0] & 0xFF
        if got != expected:
            raise BackendError(f'isp_key checksum mismatch: boot=0x{got:02x} host=0x{expected:02x}')
        stage = 'verify' if verify_stage else 'program'
        self.log(log_cb, 'INFO', f'isp_key {stage} ok (uid key_sum=0x{got:02x})')
        return xor_key

    def _log_cfg(self, log_cb: LogFn, cfg: LegacyConfigState) -> None:
        if log_cb is None:
            return
        self.log(log_cb, 'INFO', f'uid={cfg.uid.hex("-")}')
        self.log(log_cb, 'INFO', f'btver={_fmt_btver(cfg.btver)} raw_btver={cfg.btver.hex()}')
        if cfg.family_kind in ('f10', 'f13'):
            self.log(log_cb, 'INFO', f'cfg12={cfg.cfg12.hex()}')
        else:
            self.log(log_cb, 'INFO', f'reserved={cfg.reserved.hex()} wprotect={cfg.wprotect.hex()} user_cfg={cfg.user_cfg.hex()}')
