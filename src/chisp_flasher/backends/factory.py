from __future__ import annotations

from functools import lru_cache

from chisp_flasher.core.errors import BackendError


_BACKEND_MODULES = {
    'ch32v20x': 'chisp_flasher.backends.ch32v20x',
    'ch32v30x': 'chisp_flasher.backends.ch32v30x',
    'ch32x103': 'chisp_flasher.backends.ch32x103',
    'ch32f20x': 'chisp_flasher.backends.ch32f20x',
    'ch32x03x': 'chisp_flasher.backends.ch32x03x',
    'ch32l103': 'chisp_flasher.backends.ch32l103',
    'ch32v00x': 'chisp_flasher.backends.ch32v00x',
    'wch_legacy_usb': 'chisp_flasher.backends.wch_legacy_usb',
    'wch_legacy_uart': 'chisp_flasher.backends.wch_legacy_uart',
}


@lru_cache(maxsize=None)
def make_backend(family_name: str):
    family_name = (family_name or '').strip()
    module_name = _BACKEND_MODULES.get(family_name)
    if not module_name:
        raise BackendError(f'unknown backend family: {family_name}')
    module = __import__(module_name, fromlist=['Backend'])
    return module.Backend()
