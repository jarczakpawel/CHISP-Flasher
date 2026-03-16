from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from functools import lru_cache
import json
import os
import platform
import time

from chisp_flasher.chipdb.loader import load_chipdb
from chisp_flasher.chipdb.resolver import ChipResolver


def _state_dir() -> Path:
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or str(Path.home())
        return Path(base) / 'CHISPFlasher'
    if platform.system() == 'Darwin':
        return Path.home() / 'Library' / 'Application Support' / 'CHISPFlasher'
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'chisp_flasher'


def default_state_path() -> Path:
    return _state_dir() / 'state.json'


def _norm_text(value: object) -> str:
    return str(value or '').strip()


def _norm_lower(value: object) -> str:
    return _norm_text(value).lower()


@lru_cache(maxsize=1)
def _resolver() -> ChipResolver:
    return ChipResolver(load_chipdb())


def _chip_supports_transport(chip_name: str, transport: str) -> bool:
    chip = dict(load_chipdb().chips.get((chip_name or '').strip()) or {})
    if not chip:
        return False
    return str(transport or '').strip() in {str(x).strip() for x in (chip.get('transport_support') or [])}


def _chip_supports_serial_auto_di(chip_name: str) -> bool:
    return bool(_resolver().transport_mode_meta((chip_name or '').strip(), 'serial_auto_di'))


def _saved_connection_is_valid(item: 'SavedConnection') -> bool:
    kind = str(item.transport_kind or '').strip()
    chip_name = str(item.chip or '').strip()
    if kind not in {'serial', 'usb'}:
        return False
    if chip_name and not _chip_supports_transport(chip_name, kind):
        return False
    if bool(item.serial_auto_di):
        return kind == 'serial' and bool(item.serial_port) and not bool(item.usb_device) and (not chip_name or _chip_supports_serial_auto_di(chip_name))
    if kind == 'serial':
        return bool(item.serial_port) and not bool(item.usb_device)
    return bool(item.usb_device) and not bool(item.serial_port)


@dataclass(slots=True)
class SavedConnection:
    name: str = ''
    chip: str = ''
    family: str = ''
    transport_kind: str = 'serial'
    serial_port: str = ''
    usb_device: str = ''
    usb_interface_number: int | None = None
    usb_endpoint_out: int | None = None
    usb_endpoint_in: int | None = None
    fast_baud: int | None = None
    serial_auto_di: bool = False
    last_action: str = ''
    last_success_ts: int = 0
    notes: str = ''
    serial_vid: int | None = None
    serial_pid: int | None = None
    serial_manufacturer: str = ''
    serial_product: str = ''
    serial_hwid: str = ''
    usb_vid: int | None = None
    usb_pid: int | None = None
    usb_manufacturer: str = ''
    usb_product: str = ''
    usb_serial_number: str = ''
    device_fingerprint: str = ''

    @property
    def selector(self) -> str:
        if bool(self.serial_auto_di) or self.transport_kind == 'serial':
            return self.serial_port
        if self.transport_kind == 'usb':
            return self.usb_device
        return ''

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class AppState:
    schema: str = 'chisp-app-state'
    version: int = 5
    last_project_path: str = ''
    recent_connections: list[SavedConnection] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'schema': self.schema,
            'version': self.version,
            'last_project_path': self.last_project_path,
            'recent_connections': [x.to_dict() for x in self.recent_connections],
        }


def load_app_state(path: str | Path | None = None) -> AppState:
    target = Path(path) if path is not None else default_state_path()
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except Exception:
        return AppState()
    if not isinstance(raw, dict):
        return AppState()
    if raw.get('schema') != 'chisp-app-state':
        return AppState()
    try:
        if int(raw.get('version', 0)) != 5:
            return AppState()
    except Exception:
        return AppState()
    state = AppState()
    state.last_project_path = str(raw.get('last_project_path', ''))
    out: list[SavedConnection] = []
    for item in list(raw.get('recent_connections') or []):
        if not isinstance(item, dict):
            continue
        try:
            saved = SavedConnection(**item)
        except Exception:
            continue
        if not _saved_connection_is_valid(saved):
            continue
        out.append(saved)
    state.recent_connections = out
    return state


def save_app_state(state: AppState, path: str | Path | None = None) -> None:
    target = Path(path) if path is not None else default_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=False), encoding='utf-8')



def build_connection_name(project, *, action: str = '', notes: str = '') -> str:
    if bool(project.transport.serial_auto_di) or project.transport.kind == 'serial':
        selector = str(project.transport.serial_port or '').strip()
    else:
        selector = str(project.transport.usb_device or '').strip()
    chip = (project.chip or '').strip() or 'Target'
    if selector:
        return f'{chip} - {selector}'
    if action:
        return f'{chip} - {action}'
    if notes:
        return f'{chip} - saved'
    return chip


def _serial_fingerprint(*, serial_port: str = '', vid: int | None = None, pid: int | None = None, manufacturer: str = '', product: str = '', hwid: str = '') -> str:
    return '|'.join([
        'serial',
        _norm_lower(serial_port),
        '' if vid is None else f'{int(vid) & 0xFFFF:04x}',
        '' if pid is None else f'{int(pid) & 0xFFFF:04x}',
        _norm_lower(manufacturer),
        _norm_lower(product),
        _norm_lower(hwid),
    ])


def _usb_fingerprint(*, usb_device: str = '', vid: int | None = None, pid: int | None = None, manufacturer: str = '', product: str = '', serial_number: str = '', interface_number: int | None = None, endpoint_out: int | None = None, endpoint_in: int | None = None) -> str:
    return '|'.join([
        'usb',
        _norm_lower(usb_device),
        '' if vid is None else f'{int(vid) & 0xFFFF:04x}',
        '' if pid is None else f'{int(pid) & 0xFFFF:04x}',
        _norm_lower(manufacturer),
        _norm_lower(product),
        _norm_lower(serial_number),
        '' if interface_number is None else str(int(interface_number)),
        '' if endpoint_out is None else f'{int(endpoint_out) & 0xFF:02x}',
        '' if endpoint_in is None else f'{int(endpoint_in) & 0xFF:02x}',
    ])


def project_to_saved_connection(project, *, action: str = '', notes: str = '', name: str = '') -> SavedConnection:
    transport_kind = str(project.transport.kind or 'serial')
    serial_port = str(project.transport.serial_port or '')
    usb_device = str(project.transport.usb_device or '')
    usb_interface_number = project.transport.usb_interface_number
    usb_endpoint_out = project.transport.usb_endpoint_out
    usb_endpoint_in = project.transport.usb_endpoint_in
    serial_auto_di = bool(project.transport.serial_auto_di) and _chip_supports_serial_auto_di(str(project.chip or ''))
    if serial_auto_di:
        transport_kind = 'serial'
    if transport_kind == 'serial' or serial_auto_di:
        usb_device = ''
        usb_interface_number = None
        usb_endpoint_out = None
        usb_endpoint_in = None
    elif usb_device:
        serial_port = ''
    saved = SavedConnection(
        name=(name or build_connection_name(project, action=action, notes=notes)).strip(),
        chip=str(project.chip or ''),
        family=str(project.family or ''),
        transport_kind=transport_kind,
        serial_port=serial_port,
        usb_device=usb_device,
        usb_interface_number=usb_interface_number,
        usb_endpoint_out=usb_endpoint_out,
        usb_endpoint_in=usb_endpoint_in,
        fast_baud=int(project.operations.fast_baud) if int(project.operations.fast_baud) > 0 else None,
        serial_auto_di=serial_auto_di,
        last_action=str(action or ''),
        last_success_ts=int(time.time()),
        notes=str(notes or ''),
    )
    if saved.transport_kind == 'usb' and saved.usb_device:
        saved.device_fingerprint = _usb_fingerprint(
            usb_device=saved.usb_device,
            interface_number=saved.usb_interface_number,
            endpoint_out=saved.usb_endpoint_out,
            endpoint_in=saved.usb_endpoint_in,
        )
    else:
        saved.device_fingerprint = _serial_fingerprint(serial_port=saved.serial_port)
    return saved


def enrich_saved_connection_from_candidates(saved: SavedConnection, candidates: dict | None) -> SavedConnection:
    if not isinstance(candidates, dict):
        return saved
    if saved.transport_kind == 'usb' and saved.usb_device:
        for item in list(candidates.get('usb_device_entries') or []):
            if _norm_text(item.get('selector')) != saved.usb_device:
                continue
            saved.usb_vid = item.get('vid')
            saved.usb_pid = item.get('pid')
            saved.usb_manufacturer = _norm_text(item.get('manufacturer'))
            saved.usb_product = _norm_text(item.get('product'))
            saved.usb_serial_number = _norm_text(item.get('serial_number'))
            if saved.usb_interface_number is None:
                saved.usb_interface_number = item.get('interface_number')
            if saved.usb_endpoint_out is None:
                saved.usb_endpoint_out = item.get('endpoint_out')
            if saved.usb_endpoint_in is None:
                saved.usb_endpoint_in = item.get('endpoint_in')
            saved.device_fingerprint = _usb_fingerprint(
                usb_device=saved.usb_device,
                vid=saved.usb_vid,
                pid=saved.usb_pid,
                manufacturer=saved.usb_manufacturer,
                product=saved.usb_product,
                serial_number=saved.usb_serial_number,
                interface_number=saved.usb_interface_number,
                endpoint_out=saved.usb_endpoint_out,
                endpoint_in=saved.usb_endpoint_in,
            )
            break
        return saved
    if saved.serial_port:
        for item in list(candidates.get('serial_port_entries') or []):
            if _norm_text(item.get('selector')) != saved.serial_port:
                continue
            saved.serial_vid = item.get('vid')
            saved.serial_pid = item.get('pid')
            saved.serial_manufacturer = _norm_text(item.get('manufacturer'))
            saved.serial_product = _norm_text(item.get('product'))
            saved.serial_hwid = _norm_text(item.get('hwid'))
            saved.device_fingerprint = _serial_fingerprint(
                serial_port=saved.serial_port,
                vid=saved.serial_vid,
                pid=saved.serial_pid,
                manufacturer=saved.serial_manufacturer,
                product=saved.serial_product,
                hwid=saved.serial_hwid,
            )
            break
    return saved


def _same_connection(a: SavedConnection, b: SavedConnection) -> bool:
    return (
        a.chip == b.chip
        and a.transport_kind == b.transport_kind
        and bool(a.serial_auto_di) == bool(b.serial_auto_di)
        and a.serial_port == b.serial_port
        and a.usb_device == b.usb_device
        and a.usb_interface_number == b.usb_interface_number
        and a.usb_endpoint_out == b.usb_endpoint_out
        and a.usb_endpoint_in == b.usb_endpoint_in
        and a.device_fingerprint == b.device_fingerprint
    )


def remember_recent_connection(state: AppState, saved: SavedConnection, *, limit: int = 8) -> None:
    kept: list[SavedConnection] = [saved]
    for item in state.recent_connections:
        if _same_connection(item, saved):
            continue
        kept.append(item)
        if len(kept) >= limit:
            break
    state.recent_connections = kept


def apply_saved_connection(project, saved: SavedConnection) -> None:
    project.family = saved.family or project.family
    project.chip = saved.chip or project.chip
    project.transport.kind = saved.transport_kind or project.transport.kind
    if project.transport.kind == 'serial' or bool(saved.serial_auto_di):
        project.transport.serial_port = saved.serial_port or ''
        project.transport.usb_device = ''
        project.transport.usb_interface_number = None
        project.transport.usb_endpoint_out = None
        project.transport.usb_endpoint_in = None
    else:
        project.transport.serial_port = ''
        project.transport.usb_device = saved.usb_device or ''
        project.transport.usb_interface_number = saved.usb_interface_number
        project.transport.usb_endpoint_out = saved.usb_endpoint_out
        project.transport.usb_endpoint_in = saved.usb_endpoint_in
    try:
        if saved.fast_baud is not None and int(saved.fast_baud) > 0:
            project.operations.fast_baud = int(saved.fast_baud)
    except Exception:
        pass
    project.transport.serial_auto_di = bool(saved.serial_auto_di)


def _connection_sort_key(item: SavedConnection) -> tuple[int, str, str]:
    return (int(item.last_success_ts), str(item.selector), str(item.name))


def _candidate_fingerprints(candidates: dict | None, transport_kind: str, *, serial_auto_di: bool = False) -> tuple[set[str], set[str]]:
    selectors: set[str] = set()
    fps: set[str] = set()
    if not isinstance(candidates, dict):
        return selectors, fps
    if transport_kind == 'usb' and not serial_auto_di:
        for item in list(candidates.get('usb_device_entries') or []):
            selector = _norm_text(item.get('selector'))
            if selector:
                selectors.add(selector)
            fps.add(_usb_fingerprint(
                usb_device=selector,
                vid=item.get('vid'),
                pid=item.get('pid'),
                manufacturer=item.get('manufacturer'),
                product=item.get('product'),
                serial_number=item.get('serial_number'),
                interface_number=item.get('interface_number'),
                endpoint_out=item.get('endpoint_out'),
                endpoint_in=item.get('endpoint_in'),
            ))
        return selectors, fps
    for item in list(candidates.get('serial_port_entries') or []):
        selector = _norm_text(item.get('selector'))
        if selector:
            selectors.add(selector)
        fps.add(_serial_fingerprint(
            serial_port=selector,
            vid=item.get('vid'),
            pid=item.get('pid'),
            manufacturer=item.get('manufacturer'),
            product=item.get('product'),
            hwid=item.get('hwid'),
        ))
    return selectors, fps


def find_best_recent_connection(state: AppState, *, chip: str = '', family: str = '', transport_kind: str = '', candidates: dict | None = None, serial_auto_di: bool = False) -> SavedConnection | None:
    chip = str(chip or '').strip()
    family = str(family or '').strip()
    transport_kind = str(transport_kind or '').strip()
    selectors, fps = _candidate_fingerprints(candidates, transport_kind or 'serial', serial_auto_di=bool(serial_auto_di))
    rows: list[tuple[int, tuple[int, str, str], SavedConnection]] = []
    for item in state.recent_connections:
        score = 0
        if chip:
            if item.chip != chip:
                continue
            score += 100
        elif family:
            if item.family != family:
                continue
            score += 25
        if transport_kind:
            if item.transport_kind != transport_kind:
                continue
            score += 35
        if bool(item.serial_auto_di) != bool(serial_auto_di):
            continue
        if item.serial_auto_di:
            score += 20
        if item.selector:
            score += 5
        if selectors:
            if item.selector and item.selector in selectors:
                score += 35
            elif item.selector:
                score -= 15
        if fps:
            if item.device_fingerprint and item.device_fingerprint in fps:
                score += 60
            elif item.device_fingerprint:
                score -= 20
        if score <= 0:
            continue
        rows.append((score, _connection_sort_key(item), item))
    if not rows:
        return None
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return rows[0][2]


def describe_saved_connection(item: SavedConnection) -> str:
    selector = item.selector or '-'
    transport = 'usb-uart-auto-di' if bool(item.serial_auto_di) else (item.transport_kind or '-')
    chip = item.chip or 'Target'
    extra = ''
    if item.transport_kind == 'usb' and item.usb_product:
        extra = f' - {item.usb_product}'
    elif item.transport_kind != 'usb' and item.serial_product:
        extra = f' - {item.serial_product}'
    note = (item.notes or '').strip()
    if note:
        extra = f'{extra} [{note[:32]}]'
    return f'{chip} - {transport} - {selector}{extra}'
