from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from functools import lru_cache
import yaml

from chisp_flasher.chipdb.loader import load_chipdb
from chisp_flasher.chipdb.resolver import ChipResolver
from chisp_flasher.core.errors import ProjectFormatError

PROJECT_FORMAT = 'chisp-project'
PROJECT_VERSION = 6


@lru_cache(maxsize=1)
def _resolver() -> ChipResolver:
    return ChipResolver(load_chipdb())


def _chip_supports_serial_auto_di(chip_name: str) -> bool:
    return bool(_resolver().transport_mode_meta((chip_name or '').strip(), 'serial_auto_di'))


def _chip_supports_transport(chip_name: str, transport: str) -> bool:
    chip = dict(load_chipdb().chips.get((chip_name or '').strip()) or {})
    if not chip:
        return False
    return str(transport or '').strip() in {str(x).strip() for x in (chip.get('transport_support') or [])}


@dataclass(slots=True)
class TransportConfig:
    kind: str = 'serial'
    serial_port: str = ''
    usb_device: str = ''
    usb_interface_number: int | None = None
    usb_endpoint_out: int | None = None
    usb_endpoint_in: int | None = None
    serial_auto_di: bool = False


@dataclass(slots=True)
class OperationConfig:
    verify_after_flash: bool = True
    trace_mode: bool = False
    fast_baud: int = 1000000
    no_fast: bool = False


@dataclass(slots=True)
class ConfigState:
    enable_rrp: bool = False
    clear_codeflash: bool = False
    disable_stop_mode_rst: bool = False
    disable_standby_mode_rst: bool = False
    enable_soft_ctrl_iwdg: bool = False
    enable_long_delay_time: bool = False
    ramx_rom_mode: str = ''
    data0: str = '0x00'
    data1: str = '0x00'
    wrp0: str = '0xFF'
    wrp1: str = '0xFF'
    wrp2: str = '0xFF'
    wrp3: str = '0xFF'


@dataclass(slots=True)
class CHISPProject:
    schema: str = PROJECT_FORMAT
    version: int = PROJECT_VERSION
    name: str = ''
    family: str = 'CH32V'
    chip: str = 'CH32V203'
    firmware_path: str = ''
    transport: TransportConfig = field(default_factory=TransportConfig)
    operations: OperationConfig = field(default_factory=OperationConfig)
    config: ConfigState = field(default_factory=ConfigState)

    def to_dict(self) -> dict:
        return {
            'schema': self.schema,
            'version': self.version,
            'name': self.name,
            'family': self.family,
            'chip': self.chip,
            'firmware_path': self.firmware_path,
            'transport': asdict(self.transport),
            'operations': asdict(self.operations),
            'config': asdict(self.config),
        }


def _coerce_project(raw: dict) -> CHISPProject:
    if raw.get('schema') != PROJECT_FORMAT:
        raise ProjectFormatError(f"bad schema: {raw.get('schema')!r}")
    version = int(raw.get('version', 0))
    if version != PROJECT_VERSION:
        raise ProjectFormatError(f"bad version: {raw.get('version')!r}")
    transport_raw = dict(raw.get('transport') or {})
    operations_raw = dict(raw.get('operations') or {})
    transport = TransportConfig(**transport_raw)
    operations = OperationConfig(**operations_raw)
    kind = str(transport.kind or '').strip()
    if kind not in {'serial', 'usb'}:
        raise ProjectFormatError(f"bad transport.kind: {transport.kind!r}")
    if bool(transport.serial_auto_di):
        if kind != 'serial' or transport.usb_device:
            raise ProjectFormatError('invalid serial auto-di transport state')
    elif kind == 'serial':
        if transport.usb_device:
            raise ProjectFormatError('invalid serial transport state')
    else:
        if transport.serial_port:
            raise ProjectFormatError('invalid usb transport state')
    chip_name = str(raw.get('chip', 'CH32V203'))
    if not _chip_supports_transport(chip_name, kind):
        raise ProjectFormatError(f'transport not supported for {chip_name}: {kind}')
    if bool(transport.serial_auto_di) and not _chip_supports_serial_auto_di(chip_name):
        raise ProjectFormatError(f'serial auto-di not supported for {chip_name}')
    return CHISPProject(
        schema=raw['schema'],
        version=PROJECT_VERSION,
        name=str(raw.get('name', '')),
        family=str(raw.get('family', 'CH32V')),
        chip=str(raw.get('chip', 'CH32V203')),
        firmware_path=str(raw.get('firmware_path', '')),
        transport=transport,
        operations=operations,
        config=ConfigState(**dict(raw.get('config') or {})),
    )


def load_project(path: str | Path) -> CHISPProject:
    raw = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ProjectFormatError('project root must be a mapping')
    return _coerce_project(raw)


def save_project(path: str | Path, project: CHISPProject) -> None:
    Path(path).write_text(yaml.safe_dump(project.to_dict(), sort_keys=False, allow_unicode=True), encoding='utf-8')
