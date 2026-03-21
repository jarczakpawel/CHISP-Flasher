from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from chisp_flasher.chipdb.loader import load_chipdb
from chisp_flasher.chipdb.resolver import ChipResolver
from chisp_flasher.core.errors import OperationError, ProjectFormatError
from chisp_flasher.core.operations import (
    enumerate_connection_candidates,
    resolve_project,
    run_project_detect,
    run_project_erase_only,
    run_project_flash,
    run_project_read_config,
    run_project_smart_detect,
    run_project_verify_only,
    run_project_write_config,
)
from chisp_flasher.formats.projectfmt import (
    CHISPProject,
    ConfigState,
    OperationConfig,
    TransportConfig,
    load_project,
    save_project,
)
from chisp_flasher.transport.autodetect import list_all_ports
from chisp_flasher.transport.usb_native import UsbNativeLink


def _series_name(chip_name: str) -> str:
    name = (chip_name or '').strip().upper()
    if name.startswith('CH32F'):
        return 'CH32F'
    if name.startswith('CH32V'):
        return 'CH32V'
    if name.startswith('CH32X'):
        return 'CH32X'
    if name.startswith('CH32L'):
        return 'CH32L'
    if name.startswith('CH32M'):
        return 'CH32M'
    if name.startswith('CH54'):
        return 'CH54'
    if name.startswith('CH55'):
        return 'CH55'
    if name.startswith('CH56'):
        return 'CH56'
    if name.startswith('CH57'):
        return 'CH57'
    if name.startswith('CH58'):
        return 'CH58'
    if name.startswith('CH59'):
        return 'CH59'
    return ''


def new_project() -> CHISPProject:
    return CHISPProject()


def clone_project(project: CHISPProject) -> CHISPProject:
    return deepcopy(project)


def make_project(
    *,
    chip: str,
    family: str = '',
    firmware_path: str = '',
    transport_kind: str = 'serial',
    serial_port: str = '',
    usb_device: str = '',
    usb_interface_number: int | None = None,
    usb_endpoint_out: int | None = None,
    usb_endpoint_in: int | None = None,
    serial_auto_di: bool = False,
    verify_after_flash: bool = True,
    trace_mode: bool = False,
    fast_baud: int = 1000000,
    no_fast: bool = False,
    name: str = '',
) -> CHISPProject:
    project = CHISPProject()
    project.name = str(name or '').strip()
    project.chip = str(chip or '').strip() or project.chip
    project.family = str(family or '').strip() or _series_name(project.chip) or project.family
    project.firmware_path = str(firmware_path or '').strip()
    project.transport = TransportConfig(
        kind=str(transport_kind or 'serial').strip() or 'serial',
        serial_port=str(serial_port or '').strip(),
        usb_device=str(usb_device or '').strip(),
        usb_interface_number=usb_interface_number,
        usb_endpoint_out=usb_endpoint_out,
        usb_endpoint_in=usb_endpoint_in,
        serial_auto_di=bool(serial_auto_di),
    )
    project.operations = OperationConfig(
        verify_after_flash=bool(verify_after_flash),
        trace_mode=bool(trace_mode),
        fast_baud=int(fast_baud),
        no_fast=bool(no_fast),
    )
    project.config = ConfigState()
    return project


def load_project_file(path: str | Path) -> CHISPProject:
    return load_project(path)


def save_project_file(path: str | Path, project: CHISPProject) -> None:
    save_project(path, project)


def validate_project(project: CHISPProject) -> dict:
    resolved = resolve_project(project)
    return {
        'ok': True,
        'project': project.to_dict(),
        'resolved': {
            'chip': resolved.chip_name,
            'backend_family': resolved.backend_family,
            'protocol_variant': resolved.protocol_variant,
            'display_connection_mode': resolved.display_connection_mode,
        },
    }

def resolve_effective_project(project: CHISPProject) -> dict:
    resolved = resolve_project(project)
    return {
        'project': project.to_dict(),
        'resolved': {
            'chip': resolved.chip_name,
            'backend_family': resolved.backend_family,
            'protocol_variant': resolved.protocol_variant,
            'display_connection_mode': resolved.display_connection_mode,
            'mode': project_mode(project),
            'selector': str(project.transport.usb_device or '').strip() if project_mode(project) == 'native-usb' else str(project.transport.serial_port or '').strip(),
            'firmware_path': str(project.firmware_path or '').strip(),
            'visible_config_fields': sorted(set(resolved.gui_profile.get('controls_visible') or [])),
            'hidden_config_fields': sorted(set(resolved.gui_profile.get('controls_hidden') or [])),
        },
    }


def project_mode(project: CHISPProject) -> str:
    if str(project.transport.kind or '').strip() == 'usb':
        return 'native-usb'
    if bool(project.transport.serial_auto_di):
        return 'auto-di'
    return 'serial'


def get_visible_config_fields(chip_name: str, *, mode: str = 'serial') -> dict:
    chip = str(chip_name or '').strip()
    if not chip:
        raise ProjectFormatError('chip name is empty')
    mode_text = str(mode or 'serial').strip() or 'serial'
    if mode_text not in {'serial', 'auto-di', 'native-usb'}:
        raise ProjectFormatError(f'unknown mode: {mode!r}')
    transport = 'usb' if mode_text == 'native-usb' else 'serial'
    resolved = ChipResolver(load_chipdb()).resolve(chip, transport=transport)
    return {
        'chip': chip,
        'family': _series_name(chip),
        'mode': mode_text,
        'protocol_variant': resolved.protocol_variant,
        'fields': sorted(set(resolved.gui_profile.get('controls_visible') or [])),
    }

def list_chips(*, family: str = '') -> list[dict]:
    db = load_chipdb()
    resolver = ChipResolver(db)
    names = resolver.chips_for_series(family) if str(family or '').strip() else sorted(db.chips.keys())
    out: list[dict] = []
    for name in names:
        chip = dict(db.chips.get(name) or {})
        out.append({
            'chip': str(name),
            'family': _series_name(str(name)),
            'transport_support': list(chip.get('transport_support') or []),
            'package_profile_of': str(dict(chip.get('public_cross_check') or {}).get('package_profile_of') or '').strip(),
        })
    return out


def get_chip_info(chip_name: str) -> dict:
    db = load_chipdb()
    resolver = ChipResolver(db)
    chip = dict(db.chips.get((chip_name or '').strip()) or {})
    if not chip:
        raise ProjectFormatError(f'unknown chip: {chip_name}')
    cross = dict(chip.get('public_cross_check') or {})
    transport_support = [str(x).strip() for x in (chip.get('transport_support') or []) if str(x).strip()]
    modes: list[str] = []
    if 'serial' in transport_support:
        modes.append('serial')
        if resolver.transport_mode_meta(chip_name, 'serial_auto_di'):
            modes.append('auto-di')
    if 'usb' in transport_support:
        modes.append('native-usb')
    return {
        'chip': str(chip_name).strip(),
        'family': _series_name(chip_name),
        'transport_support': transport_support,
        'modes': modes,
        'package_profile_of': str(cross.get('package_profile_of') or '').strip(),
        'chipdb': chip,
        'serial_mode': resolver.transport_mode_meta(chip_name, 'serial'),
        'serial_auto_di_mode': resolver.transport_mode_meta(chip_name, 'serial_auto_di'),
        'usb_mode': resolver.transport_mode_meta(chip_name, 'usb'),
    }


def list_ports() -> list[dict]:
    return [{
        'selector': info.selector,
        'display': info.display_text,
        'device': info.device,
        'description': info.description,
        'manufacturer': info.manufacturer,
        'product': info.product,
        'hwid': info.hwid,
        'vid': info.vid,
        'pid': info.pid,
        'score_tags': list(info.score_tags),
    } for info in list_all_ports()]


def list_usb_devices() -> list[dict]:
    try:
        infos = UsbNativeLink.list_candidate_infos()
    except Exception as exc:
        raise OperationError(str(exc)) from exc
    return [{
        'selector': info.selector,
        'display': info.display_text,
        'vid': info.vid,
        'pid': info.pid,
        'bus': info.bus,
        'address': info.address,
        'manufacturer': info.manufacturer,
        'product': info.product,
        'serial_number': info.serial_number,
        'interface_number': info.interface_number,
        'endpoint_out': info.endpoint_out,
        'endpoint_in': info.endpoint_in,
    } for info in infos]


def suggest(project: CHISPProject) -> dict:
    return enumerate_connection_candidates(project)


def detect(project: CHISPProject, *, log_cb=None) -> dict:
    return run_project_detect(project, log_cb=log_cb)


def smart_detect(project: CHISPProject, *, log_cb=None, max_ports: int = 3, max_usb: int = 3, refresh_usb_between_attempts: bool = True) -> dict:
    return run_project_smart_detect(
        project,
        log_cb=log_cb,
        max_ports=max_ports,
        max_usb=max_usb,
        refresh_usb_between_attempts=refresh_usb_between_attempts,
    )


def read_config(project: CHISPProject, *, log_cb=None) -> dict:
    return run_project_read_config(project, log_cb=log_cb)


def write_config(project: CHISPProject, *, log_cb=None) -> dict:
    return run_project_write_config(project, log_cb=log_cb)


def erase(project: CHISPProject, *, log_cb=None, progress_cb=None) -> dict:
    return run_project_erase_only(project, log_cb=log_cb, progress_cb=progress_cb)


def verify(project: CHISPProject, *, log_cb=None, progress_cb=None) -> dict:
    return run_project_verify_only(project, log_cb=log_cb, progress_cb=progress_cb)


def flash(project: CHISPProject, *, log_cb=None, progress_cb=None) -> dict:
    return run_project_flash(project, log_cb=log_cb, progress_cb=progress_cb)


def project_to_dict(project: CHISPProject) -> dict:
    return project.to_dict()


def dataclass_to_dict(value) -> dict:
    return asdict(value)
