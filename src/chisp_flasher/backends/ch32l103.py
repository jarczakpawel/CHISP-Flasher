from __future__ import annotations

from chisp_flasher.backends.uart_framed_generic import UartFramedGenericBackend
from chisp_flasher.backends.usb_native_family import CHUNK_SIZE, NativeUsbFamilyBackend
from chisp_flasher.core.errors import BackendError


def _cand(chip_id: int, erase_kib: int) -> dict:
    return {
        'identify_device_id': chip_id,
        'device_type': 0x25,
        'erase_sectors': erase_kib,
        'chunk_size': CHUNK_SIZE,
        'preserve_user_word': True,
        'max_flash_size': erase_kib * 1024,
    }


CH32L103_CANDIDATES = [
    _cand(0x30, 64),
    _cand(0x31, 64),
    _cand(0x32, 64),
    _cand(0x3A, 64),
    _cand(0x3B, 64),
    _cand(0x3D, 64),
    _cand(0x37, 48),
]


class Backend(NativeUsbFamilyBackend):
    family_name = 'ch32l103'
    supports_config_write_native_usb = False
    supports_config_write_uart_framed = False
    def supported_protocol_variants(self) -> list[str]:
        return ['usb_native_plain', 'uart_framed']


    chip_uart_defaults = {
        'CH32L103': dict(CH32L103_CANDIDATES[0], identify_candidates=CH32L103_CANDIDATES),
    }
    chip_native_defaults = {
        'CH32L103': dict(CH32L103_CANDIDATES[0], identify_candidates=CH32L103_CANDIDATES),
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
        return self._uart_helper().read_config_chip(chip_name=chip_name, **self._uart_detect_defaults(chip_name), **kwargs)

    def write_config_uart_framed(self, chip_name: str, config, **kwargs):
        raise BackendError(f'config write is not implemented for {chip_name} yet')

    def flash_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self._uart_helper().flash_chip(firmware_path, chip_name=chip_name, **self._uart_flash_defaults(chip_name), **kwargs)

    def erase_uart_framed(self, chip_name: str, **kwargs):
        return self._uart_helper().erase_chip(chip_name=chip_name, **self._uart_erase_defaults(chip_name), **kwargs)

    def verify_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self._uart_helper().verify_chip(firmware_path, chip_name=chip_name, **self._uart_verify_defaults(chip_name), **kwargs)
