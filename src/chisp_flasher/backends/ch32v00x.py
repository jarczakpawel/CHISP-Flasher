from __future__ import annotations

from chisp_flasher.backends.uart_framed_generic import UartFramedGenericBackend
from chisp_flasher.core.errors import BackendError

CHUNK_SIZE = 56


def _cand(chip_id: int, erase_kib: int) -> dict:
    return {
        'identify_device_id': chip_id,
        'device_type': 0x21,
        'erase_sectors': erase_kib,
        'chunk_size': CHUNK_SIZE,
        'preserve_user_word': True,
        'max_flash_size': erase_kib * 1024,
    }


CH32V002_CANDIDATES = [
    _cand(0x20, 16),
    _cand(0x21, 16),
    _cand(0x22, 16),
    _cand(0x23, 16),
    _cand(0x24, 16),
]
CH32V003_CANDIDATES = [
    _cand(0x30, 16),
    _cand(0x31, 16),
    _cand(0x32, 16),
    _cand(0x33, 16),
]
CH32V004_CANDIDATES = [
    _cand(0x40, 32),
    _cand(0x41, 32),
]
CH32V005_CANDIDATES = [
    _cand(0x50, 32),
    _cand(0x51, 32),
    _cand(0x52, 32),
    _cand(0x53, 32),
]
CH32V006_CANDIDATES = [
    _cand(0x60, 62),
    _cand(0x61, 62),
    _cand(0x62, 62),
    _cand(0x63, 62),
]
CH32V007_CANDIDATES = [
    _cand(0x71, 62),
    _cand(0x72, 62),
]
CH32M007_CANDIDATES = [
    _cand(0x70, 62),
]


class Backend(UartFramedGenericBackend):
    family_name = 'ch32v00x'
    supports_config_write_uart_framed = False
    supports_config_write_native_usb = False

    chip_defaults = {
        'CH32V002': dict(CH32V002_CANDIDATES[0], identify_candidates=CH32V002_CANDIDATES),
        'CH32V003': dict(CH32V003_CANDIDATES[0], identify_candidates=CH32V003_CANDIDATES),
        'CH32V004': dict(CH32V004_CANDIDATES[0], identify_candidates=CH32V004_CANDIDATES),
        'CH32V005': dict(CH32V005_CANDIDATES[0], identify_candidates=CH32V005_CANDIDATES),
        'CH32V006': dict(CH32V006_CANDIDATES[0], identify_candidates=CH32V006_CANDIDATES),
        'CH32V007': dict(CH32V007_CANDIDATES[0], identify_candidates=CH32V007_CANDIDATES),
        'CH32M007': dict(CH32M007_CANDIDATES[0], identify_candidates=CH32M007_CANDIDATES),
    }

    def _detect_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'identify_candidates': base['identify_candidates'],
        }

    def _erase_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'erase_sectors': base['erase_sectors'],
            'chunk_size': base['chunk_size'],
            'identify_candidates': base['identify_candidates'],
        }

    def _verify_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'chunk_size': base['chunk_size'],
            'identify_candidates': base['identify_candidates'],
        }

    def _flash_defaults(self, chip_name: str) -> dict:
        base = dict(self.chip_defaults[chip_name])
        return {
            'identify_device_id': base['identify_device_id'],
            'device_type': base['device_type'],
            'erase_sectors': base['erase_sectors'],
            'chunk_size': base['chunk_size'],
            'preserve_user_word': base['preserve_user_word'],
            'identify_candidates': base['identify_candidates'],
        }

    def detect_uart_framed(self, chip_name: str, **kwargs):
        return self.detect_chip(chip_name=chip_name, **self._detect_defaults(chip_name), **kwargs)

    def read_config_uart_framed(self, chip_name: str, **kwargs):
        return self.read_config_chip(chip_name=chip_name, **self._detect_defaults(chip_name), **kwargs)

    def write_config_uart_framed(self, chip_name: str, config, **kwargs):
        raise BackendError(f'config write is not implemented for {chip_name} yet')

    def flash_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self.flash_chip(firmware_path, chip_name=chip_name, **self._flash_defaults(chip_name), **kwargs)

    def erase_uart_framed(self, chip_name: str, **kwargs):
        return self.erase_chip(chip_name=chip_name, **self._erase_defaults(chip_name), **kwargs)

    def verify_uart_framed(self, chip_name: str, firmware_path: str, **kwargs):
        return self.verify_chip(firmware_path, chip_name=chip_name, **self._verify_defaults(chip_name), **kwargs)
