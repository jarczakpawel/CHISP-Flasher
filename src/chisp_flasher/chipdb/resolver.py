from __future__ import annotations

from dataclasses import dataclass

from chisp_flasher.chipdb.loader import ChipDB
from chisp_flasher.core.errors import ResolverError


@dataclass(slots=True)
class ResolvedChip:
    chip_name: str
    backend_family: str
    gui_profile_name: str
    gui_profile: dict
    protocol_variant: str
    chip_meta: dict
    display_connection_mode: str


class ChipResolver:
    def __init__(self, chipdb: ChipDB):
        self.chipdb = chipdb

    def chips_for_series(self, series: str) -> list[str]:
        series = (series or '').strip().upper()
        return [name for name in self.chipdb.chips.keys() if name.upper().startswith(series)]

    def chip_meta(self, chip_name: str) -> dict:
        return dict(self.chipdb.chips.get(chip_name) or {})

    def package_profile_base(self, chip_name: str) -> str:
        chip = self.chip_meta(chip_name)
        cross = dict(chip.get('public_cross_check') or {})
        return str(cross.get('package_profile_of') or '').strip()

    def transport_mode_meta(self, chip_name: str, mode_key: str) -> dict:
        chip = self.chip_meta(chip_name)
        supported = {str(x).strip() for x in (chip.get('transport_support') or [])}
        transport = ''
        if mode_key in {'serial_manual', 'serial_auto_di'}:
            transport = 'serial'
        elif mode_key == 'native_usb':
            transport = 'usb'
        if transport and transport not in supported:
            return {}
        merged: dict = {}
        base = self.package_profile_base(chip_name)
        if base:
            merged.update(dict((self.chipdb.transport_matrix.get(base) or {}).get(mode_key) or {}))
        chip_matrix = dict(self.chipdb.transport_matrix.get(chip_name) or {})
        if mode_key in chip_matrix:
            local_row = chip_matrix.get(mode_key)
            if local_row is None:
                return {}
            if isinstance(local_row, dict):
                merged.update(dict(local_row))
        local = chip.get(mode_key)
        if isinstance(local, dict):
            merged.update(dict(local))
        return merged

    def transport_meta(self, chip_name: str, transport: str) -> dict:
        return self.transport_mode_meta(chip_name, 'native_usb' if str(transport or '').strip() == 'usb' else 'serial_manual')

    def resolve(self, chip_name: str, transport: str = 'serial') -> ResolvedChip:
        chip = self.chip_meta(chip_name)
        if not chip:
            raise ResolverError(f'unknown chip: {chip_name}')
        transport = str(transport or '').strip()
        supported = [str(x) for x in (chip.get('transport_support') or [])]
        if supported and transport not in supported:
            raise ResolverError(f'transport not supported for {chip_name}: {transport}')
        profile_key = 'gui_profile_usb' if transport == 'usb' else 'gui_profile_serial'
        profile_name = str(chip.get(profile_key) or '')
        if not profile_name:
            raise ResolverError(f'missing gui profile for {chip_name}/{transport}')
        profile = self.chipdb.gui_profiles.get(profile_name)
        if not profile:
            raise ResolverError(f'unknown gui profile: {profile_name}')
        transport_meta = self.transport_meta(chip_name, transport)
        backend_family = str(transport_meta.get('backend_family') or '').strip()
        if not backend_family:
            raise ResolverError(f'missing backend family for {chip_name}/{transport}')
        protocol_variant = str(transport_meta.get('protocol') or '').strip()
        if not protocol_variant:
            raise ResolverError(f'missing protocol variant for {chip_name}/{transport}')
        return ResolvedChip(
            chip_name=chip_name,
            backend_family=backend_family,
            gui_profile_name=profile_name,
            gui_profile=dict(profile),
            protocol_variant=protocol_variant,
            chip_meta=chip,
            display_connection_mode=self._human_connection_mode(transport, protocol_variant),
        )

    def _human_connection_mode(self, transport: str, protocol_variant: str) -> str:
        if transport == 'serial':
            return 'Serial bootloader'
        if protocol_variant == 'usb_native_plain':
            return 'Native USB bootloader'
        return 'USB bootloader'
