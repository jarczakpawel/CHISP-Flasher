from __future__ import annotations

from chisp_flasher.backends.uart_framed_generic import UartFramedGenericBackend
from chisp_flasher.backends.usb_native_family import CHUNK_SIZE, NativeUsbFamilyBackend
from chisp_flasher.protocol.config_codec import decode_config_fields


def _cand(chip_id: int, chip_type: int, erase_kib: int) -> dict:
    return {
        'identify_device_id': chip_id,
        'device_type': chip_type,
        'erase_sectors': erase_kib,
        'chunk_size': CHUNK_SIZE,
        'preserve_user_word': True,
        'option_profile': 'x103',
        'max_flash_size': erase_kib * 1024,
    }


CH32F103_CANDIDATES = [
    _cand(0x33, 0x14, 64),
    _cand(0x32, 0x14, 32),
    _cand(0x3F, 0x14, 32),
]
CH32V103_CANDIDATES = [
    _cand(0x33, 0x15, 64),
    _cand(0x32, 0x15, 32),
    _cand(0x3F, 0x15, 32),
]


class Backend(NativeUsbFamilyBackend):
    family_name = 'ch32x103'
    def supported_protocol_variants(self) -> list[str]:
        return ['usb_native_plain', 'uart_framed']


    chip_uart_defaults = {
        'CH32F103': dict(CH32F103_CANDIDATES[0], identify_candidates=CH32F103_CANDIDATES),
        'CH32V103': dict(CH32V103_CANDIDATES[0], identify_candidates=CH32V103_CANDIDATES),
    }
    chip_native_defaults = {
        'CH32F103': dict(CH32F103_CANDIDATES[0], identify_candidates=CH32F103_CANDIDATES),
        'CH32V103': dict(CH32V103_CANDIDATES[0], identify_candidates=CH32V103_CANDIDATES),
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
            'chunk_size': base['chunk_size'],
            'identify_candidates': base['identify_candidates'],
        }

    def _uart_verify_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_uart_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'chunk_size': base['chunk_size'],
            'identify_candidates': base['identify_candidates'],
        }

    def _uart_flash_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_uart_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'erase_sectors': base['erase_sectors'],
            'chunk_size': base['chunk_size'],
            'preserve_user_word': base['preserve_user_word'],
            'identify_candidates': base['identify_candidates'],
        }

    def detect_uart_framed(self, chip_name: str, **kwargs):
        return self._uart_helper().detect_chip(chip_name=chip_name, **self._uart_detect_defaults(chip_name), **kwargs)

    def read_config_uart_framed(self, chip_name: str, **kwargs):
        result = self._uart_helper().read_config_chip(chip_name=chip_name, **self._uart_detect_defaults(chip_name), **kwargs)
        if result.get('cfg12_hex'):
            result.update(decode_config_fields('x103', bytes.fromhex(result['cfg12_hex'])))
        return result

    def write_config_uart_framed(self, chip_name: str, config, **kwargs):
        result = self._uart_helper().write_config_chip(chip_name=chip_name, config=config, option_profile='x103', **self._uart_detect_defaults(chip_name), **kwargs)
        if result.get('cfg12_hex'):
            result.update(decode_config_fields('x103', bytes.fromhex(result['cfg12_hex'])))
        return result

    def flash_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self._uart_helper().flash_chip(firmware_path, chip_name=chip_name, **self._uart_flash_defaults(chip_name), **kwargs)

    def erase_uart_framed(self, chip_name: str, **kwargs):
        return self._uart_helper().erase_chip(chip_name=chip_name, **self._uart_erase_defaults(chip_name), **kwargs)

    def verify_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self._uart_helper().verify_chip(firmware_path, chip_name=chip_name, **self._uart_verify_defaults(chip_name), **kwargs)
