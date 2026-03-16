from __future__ import annotations

from chisp_flasher.backends.uart_framed_generic import UartFramedGenericBackend
from chisp_flasher.backends.usb_native_family import NativeUsbFamilyBackend
from chisp_flasher.protocol.config_codec import decode_config_fields


def _cand(chip_id: int, erase_kib: int, max_flash_size: int) -> dict:
    return {
        'identify_device_id': chip_id,
        'device_type': 0x23,
        'erase_sectors': erase_kib,
        'option_profile': 'x035',
        'max_flash_size': max_flash_size,
    }


CH32X033_CANDIDATES = [
    _cand(0x5A, 64, 62 * 1024),
]
CH32X035_CANDIDATES = [
    _cand(0x50, 64, 62 * 1024),
    _cand(0x51, 64, 62 * 1024),
    _cand(0x56, 64, 62 * 1024),
    _cand(0x57, 48, 48 * 1024),
    _cand(0x5B, 64, 62 * 1024),
    _cand(0x5E, 64, 62 * 1024),
]


class Backend(NativeUsbFamilyBackend):
    family_name = 'ch32x03x'

    def supported_protocol_variants(self) -> list[str]:
        return ['usb_native_plain', 'uart_framed']

    chip_uart_defaults = {
        'CH32X033': dict(CH32X033_CANDIDATES[0], identify_candidates=CH32X033_CANDIDATES),
        'CH32X035': dict(CH32X035_CANDIDATES[0], identify_candidates=CH32X035_CANDIDATES),
    }
    chip_native_defaults = {
        'CH32X033': dict(CH32X033_CANDIDATES[0], identify_candidates=CH32X033_CANDIDATES),
        'CH32X035': dict(CH32X035_CANDIDATES[0], identify_candidates=CH32X035_CANDIDATES),
    }

    def _uart_helper(self) -> UartFramedGenericBackend:
        helper = UartFramedGenericBackend()
        helper.family_name = self.family_name
        return helper

    def _uart_detect_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_uart_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'identify_candidates': base['identify_candidates'],
        }

    def _uart_erase_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_uart_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'erase_sectors': base['erase_sectors'],
            'identify_candidates': base['identify_candidates'],
        }

    def _uart_verify_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_uart_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'identify_candidates': base['identify_candidates'],
        }

    def _uart_flash_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_uart_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'erase_sectors': base['erase_sectors'],
            'identify_candidates': base['identify_candidates'],
        }

    def detect_uart_framed(self, chip_name: str, **kwargs):
        return self._uart_helper().detect_chip(chip_name=chip_name, **self._uart_detect_defaults(chip_name), **kwargs)

    def read_config_uart_framed(self, chip_name: str, **kwargs):
        result = self._uart_helper().read_config_chip(chip_name=chip_name, **self._uart_detect_defaults(chip_name), **kwargs)
        if result.get('cfg12_hex'):
            result.update(decode_config_fields('x035', bytes.fromhex(result['cfg12_hex'])))
        return result

    def write_config_uart_framed(self, chip_name: str, config, **kwargs):
        result = self._uart_helper().write_config_chip(chip_name=chip_name, config=config, option_profile='x035', **self._uart_detect_defaults(chip_name), **kwargs)
        if result.get('cfg12_hex'):
            result.update(decode_config_fields('x035', bytes.fromhex(result['cfg12_hex'])))
        return result

    def flash_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self._uart_helper().flash_chip(firmware_path, chip_name=chip_name, **self._uart_flash_defaults(chip_name), **kwargs)

    def erase_uart_framed(self, chip_name: str, **kwargs):
        return self._uart_helper().erase_chip(chip_name=chip_name, **self._uart_erase_defaults(chip_name), **kwargs)

    def verify_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self._uart_helper().verify_chip(firmware_path, chip_name=chip_name, **self._uart_verify_defaults(chip_name), **kwargs)

    def read_config_native_usb(self, chip_name: str, **kwargs):
        result = super().read_config_native_usb(chip_name, **kwargs)
        if result.get('cfg12_hex'):
            result.update(decode_config_fields('x035', bytes.fromhex(result['cfg12_hex'])))
        return result

    def write_config_native_usb(self, chip_name: str, config, **kwargs):
        result = super().write_config_native_usb(chip_name, config=config, **kwargs)
        if result.get('cfg12_hex'):
            result.update(decode_config_fields('x035', bytes.fromhex(result['cfg12_hex'])))
        return result
