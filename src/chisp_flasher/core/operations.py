from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from functools import lru_cache

from chisp_flasher.backends.factory import make_backend
from chisp_flasher.chipdb.loader import load_chipdb
from chisp_flasher.chipdb.resolver import ChipResolver
from chisp_flasher.core.errors import OperationError
from chisp_flasher.formats.projectfmt import CHISPProject
from chisp_flasher.transport.autodetect import SerialPortInfo, list_all_ports
from chisp_flasher.transport.usb_native import UsbNativeLink


def _parse_chip_id_values(values) -> list[int]:
    out: list[int] = []
    if values is None:
        return out
    seq = values if isinstance(values, (list, tuple)) else [values]
    for value in seq:
        if isinstance(value, int):
            out.append(int(value) & 0xFF)
            continue
        s = str(value).strip()
        if not s:
            continue
        try:
            out.append(int(s, 0) & 0xFF)
        except Exception:
            continue
    return out


def _chip_series_name(chip_name: str) -> str:
    name = (chip_name or '').strip().upper()
    if name.startswith('CH32F'):
        return 'CH32F'
    if name.startswith('CH32V'):
        return 'CH32V'
    if name.startswith('CH32X'):
        return 'CH32X'
    if name.startswith('CH32L'):
        return 'CH32L'
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


def _parse_identify_pair_values(values) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    if values is None:
        return pairs
    seq = values if isinstance(values, (list, tuple)) else [values]
    for value in seq:
        chip_id = None
        chip_type = None
        if isinstance(value, dict):
            chip_id = value.get('identify_device_id', value.get('chip_id'))
            chip_type = value.get('device_type', value.get('chip_type'))
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            chip_id, chip_type = value[0], value[1]
        else:
            s = str(value).strip()
            if '/' in s:
                a, b = s.split('/', 1)
                chip_id, chip_type = a.strip(), b.strip()
            elif ':' in s:
                a, b = s.split(':', 1)
                chip_id, chip_type = a.strip(), b.strip()
        try:
            if chip_id is None or chip_type is None:
                continue
            pairs.add((int(str(chip_id), 0) & 0xFF, int(str(chip_type), 0) & 0xFF))
        except Exception:
            continue
    return pairs


def _expected_identify_pairs(chip_meta: dict, protocol_variant: str) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    cross = dict(chip_meta.get('public_cross_check') or {})

    if protocol_variant == 'usb_native_plain':
        id_keys = [
            'identify_request_device_id_observed_native_usb',
            'identify_request_device_id_default',
        ]
        type_keys = [
            'identify_request_device_type_observed_native_usb',
            'identify_request_device_type_default',
        ]
        pair_keys = [
            'identify_request_pairs_observed_native_usb',
            'identify_request_pairs_default',
        ]
    else:
        id_keys = [
            'identify_request_device_id_observed',
            'identify_request_device_id_default',
        ]
        type_keys = [
            'identify_request_device_type_observed',
            'identify_request_device_type_default',
        ]
        pair_keys = [
            'identify_request_pairs_observed',
            'identify_request_pairs_default',
        ]

    explicit_pairs: set[tuple[int, int]] = set()
    for key in pair_keys:
        if key in chip_meta:
            explicit_pairs.update(_parse_identify_pair_values(chip_meta.get(key)))
    explicit_pairs.update(_parse_identify_pair_values(cross.get('wchisp_identify_pairs')))
    if explicit_pairs:
        return explicit_pairs

    ids: list[int] = []
    for key in id_keys:
        if key in chip_meta:
            ids.extend(_parse_chip_id_values(chip_meta.get(key)))
    ids.extend(_parse_chip_id_values(cross.get('wchisp_variant_chip_id')))
    ids.extend(_parse_chip_id_values(cross.get('wchisp_chip_ids')))

    types: list[int] = []
    for key in type_keys:
        if key in chip_meta:
            types.extend(_parse_chip_id_values(chip_meta.get(key)))
    types.extend(_parse_chip_id_values(cross.get('wchisp_device_type')))

    for chip_id in sorted(set(ids)):
        for chip_type in sorted(set(types)):
            pairs.add((int(chip_id) & 0xFF, int(chip_type) & 0xFF))
    return pairs


def _db():
    return load_chipdb()


@lru_cache(maxsize=1)
def _resolver() -> ChipResolver:
    return ChipResolver(_db())


def _package_profile_base(chip_name: str) -> str:
    chip_meta = dict(_db().chips.get((chip_name or '').strip()) or {})
    cross = dict(chip_meta.get('public_cross_check') or {})
    return str(cross.get('package_profile_of') or '').strip()


def _collapse_package_match_candidates(project: CHISPProject, matches: list[str]) -> list[str]:
    matches = [str(x).strip() for x in matches if str(x).strip()]
    if len(matches) <= 1:
        return matches

    current = str(project.chip or '').strip()
    current_base = _package_profile_base(current)
    if current_base:
        grouped = [name for name in matches if _package_profile_base(name) == current_base]
        if current in grouped:
            return [current] + [name for name in grouped if name != current]
        if grouped:
            return grouped

    collapsed: list[str] = []
    seen: set[str] = set()
    match_set = set(matches)
    for name in matches:
        base = _package_profile_base(name)
        target = base if base and base in match_set else name
        if target not in seen:
            collapsed.append(target)
            seen.add(target)
    return collapsed


def _match_detect_result(project: CHISPProject, resolved, result: dict) -> dict:
    chip_id = result.get('chip_id')
    chip_type = result.get('chip_type')
    if chip_id is None or chip_type is None:
        return {}

    chip_id = int(chip_id) & 0xFF
    chip_type = int(chip_type) & 0xFF
    target_variant = str(result.get('protocol_variant') or resolved.protocol_variant or '')
    if target_variant == 'usb_native_plain':
        target_transport = 'usb'
    elif bool(project.transport.serial_auto_di):
        target_transport = str(project.transport.kind or 'usb')
    else:
        target_transport = str(result.get('transport') or project.transport.kind or 'serial')

    matches: list[str] = []
    db = _db()
    for chip_name, chip_meta in db.chips.items():
        if target_transport not in set(chip_meta.get('transport_support') or []):
            continue
        if (chip_id, chip_type) in _expected_identify_pairs(chip_meta, target_variant):
            matches.append(str(chip_name))

    if not matches:
        return {
            'matched_chip_candidates': [],
            'matched_unique': False,
            'matched_transport_kind': target_transport,
            'matched_protocol_variant': target_variant,
            'match_reason': f'No chipdb entry matched 0x{chip_id:02X}/0x{chip_type:02X} for {target_variant}.',
            'auto_update_recommended': False,
        }

    matches = _collapse_package_match_candidates(project, sorted(set(matches)))
    unique_chip = matches[0] if len(matches) == 1 else ''
    if unique_chip:
        selected_same = unique_chip == project.chip
        return {
            'matched_chip': unique_chip,
            'matched_series': _chip_series_name(unique_chip),
            'matched_chip_candidates': matches,
            'matched_unique': True,
            'matched_transport_kind': target_transport,
            'matched_protocol_variant': target_variant,
            'matched_connection_mode': _effective_connection_mode(project, resolved),
            'match_reason': f'Identify 0x{chip_id:02X}/0x{chip_type:02X} matches {unique_chip}.',
            'auto_update_recommended': not selected_same,
        }

    joined = ', '.join(matches)
    return {
        'matched_chip_candidates': matches,
        'matched_unique': False,
        'matched_transport_kind': target_transport,
        'matched_protocol_variant': target_variant,
        'match_reason': f'Identify 0x{chip_id:02X}/0x{chip_type:02X} matches multiple chips: {joined}.',
        'auto_update_recommended': False,
    }


WCH_SERIAL_VIDS = {0x1A86}
WCH_SERIAL_PIDS = {0x7523, 0x55D4, 0x55D3}
SILABS_VIDS = {0x10C4}
FTDI_VIDS = {0x0403}
PROLIFIC_VIDS = {0x067B}


def resolve_project(project: CHISPProject):
    resolver = _resolver()
    return resolver.resolve(project.chip, transport=project.transport.kind)


def _serial_candidate_score(project: CHISPProject, resolved, port: SerialPortInfo) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    text = ' '.join([port.description, port.manufacturer, port.product, port.hwid]).lower()
    tags = set(port.score_tags)

    if port.vid in WCH_SERIAL_VIDS and port.pid in WCH_SERIAL_PIDS:
        score += 90
        reasons.append('WCH USB-UART adapter')
    elif 'wch-usb-uart' in tags:
        score += 80
        reasons.append('WCH serial bridge detected')
    elif port.vid in SILABS_VIDS or 'cp210x' in tags:
        score += 65
        reasons.append('CP210x serial adapter')
    elif port.vid in FTDI_VIDS or 'ftdi' in tags:
        score += 60
        reasons.append('FTDI serial adapter')
    elif port.vid in PROLIFIC_VIDS or 'pl2303' in tags:
        score += 50
        reasons.append('PL2303 serial adapter')
    elif 'usb-serial' in tags:
        score += 35
        reasons.append('Generic USB serial adapter')

    if bool(project.transport.serial_auto_di):
        if port.vid in WCH_SERIAL_VIDS and port.pid in WCH_SERIAL_PIDS:
            score += 25
            reasons.append('Good match for Auto DI')
        elif 'wch-usb-uart' in tags:
            score += 15
            reasons.append('Likely supports DTR/RTS Auto DI')
    elif resolved.display_connection_mode == 'Serial bootloader':
        score += 10
        reasons.append('Suitable for manual bootloader entry')

    if not reasons and (port.device.lower().startswith('/dev/tty') or port.device.upper().startswith('COM')):
        score += 10
        reasons.append('Detected serial port')

    if 'bluetooth' in text:
        score -= 50
        reasons.append('Probably not relevant')

    return score, reasons


def _usb_candidate_score(project: CHISPProject, resolved, dev_info) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    resolver = _resolver()
    common = set(str(x).lower() for x in (resolver.transport_meta(resolved.chip_name, 'usb').get('common_usb_selectors') or []))
    selector = dev_info.selector.lower()
    vp = f'{dev_info.vid:04x}:{dev_info.pid:04x}'

    if selector in common or vp in common:
        score += 100
        reasons.append('Known selector for this chip family')
    elif dev_info.pid == 0x55E0:
        score += 85
        reasons.append('Looks like WCH native USB bootloader')
    elif 'wch' in (dev_info.manufacturer or '').lower():
        score += 55
        reasons.append('WCH USB device')

    if dev_info.interface_number is not None:
        score += 10
        reasons.append('Interface discovered')
    if dev_info.endpoint_out is not None and dev_info.endpoint_in is not None:
        score += 10
        reasons.append('Bulk endpoints discovered')

    return score, reasons


def enumerate_connection_candidates(project: CHISPProject) -> dict:
    resolved = resolve_project(project)
    serial_infos = list_all_ports()
    serial_entries = []
    for info in serial_infos:
        score, reasons = _serial_candidate_score(project, resolved, info)
        serial_entries.append({
            'selector': info.selector,
            'display': info.display_text,
            'vid': info.vid,
            'pid': info.pid,
            'manufacturer': info.manufacturer,
            'product': info.product,
            'hwid': info.hwid,
            'description': info.description,
            'score': score,
            'reasons': reasons,
        })
    serial_entries.sort(key=lambda x: (-int(x['score']), str(x['display'])))

    usb_entries = []
    try:
        usb_infos = UsbNativeLink.list_candidate_infos()
    except Exception:
        usb_infos = []
    for info in usb_infos:
        score, reasons = _usb_candidate_score(project, resolved, info)
        usb_entries.append({
            'selector': info.selector,
            'display': info.display_text,
            'vid': info.vid,
            'pid': info.pid,
            'manufacturer': info.manufacturer,
            'product': info.product,
            'serial_number': info.serial_number,
            'bus': info.bus,
            'address': info.address,
            'interface_number': info.interface_number,
            'endpoint_out': info.endpoint_out,
            'endpoint_in': info.endpoint_in,
            'score': score,
            'reasons': reasons,
        })
    usb_entries.sort(key=lambda x: (-int(x['score']), str(x['display'])))

    suggestion: dict[str, object] = {
        'transport': _effective_connection_mode(project, resolved),
        'selector': '',
        'kind': project.transport.kind,
        'label': 'No strong match found',
        'details': 'Pick the connection manually and use Detect target.',
    }

    if resolved.protocol_variant == 'usb_native_plain':
        if usb_entries:
            best = usb_entries[0]
            suggestion = {
                'transport': _effective_connection_mode(project, resolved),
                'kind': 'usb',
                'selector': best['selector'],
                'interface_number': best.get('interface_number'),
                'endpoint_out': best.get('endpoint_out'),
                'endpoint_in': best.get('endpoint_in'),
                'label': f'Suggested USB device: {best.get("display") or best["selector"]}',
                'details': '; '.join(best.get('reasons') or []) or 'Best native USB candidate for the selected chip.',
            }
    else:
        if serial_entries:
            best = serial_entries[0]
            suggestion = {
                'transport': _effective_connection_mode(project, resolved),
                'kind': project.transport.kind,
                'selector': best['selector'],
                'label': f'Suggested port: {best.get("display") or best["selector"]}',
                'details': '; '.join(best.get('reasons') or []) or 'Best serial candidate for the selected chip.',
            }

    return {
        'serial_ports': [x['selector'] for x in serial_entries],
        'serial_port_entries': serial_entries,
        'usb_devices': [x['selector'] for x in usb_entries],
        'usb_device_entries': usb_entries,
        'suggestion': suggestion,
    }



def _chip_supports_serial_auto_di(project: CHISPProject) -> bool:
    chip_name = (project.chip or '').strip()
    if not chip_name:
        return False
    resolver = _resolver()
    return bool(resolver.transport_mode_meta(chip_name, 'serial_auto_di'))


def _mode_from_serial_settings(project: CHISPProject) -> str:
    return 'usb' if bool(project.transport.serial_auto_di) and _chip_supports_serial_auto_di(project) else 'ttl'


def _effective_connection_mode(project: CHISPProject, resolved) -> str:
    if getattr(resolved, 'protocol_variant', '') != 'usb_native_plain':
        return 'USB-UART Auto DI' if bool(project.transport.serial_auto_di) and _chip_supports_serial_auto_di(project) else 'Serial bootloader'
    return resolved.display_connection_mode


def _serial_port_selector(project: CHISPProject) -> str:
    return (project.transport.serial_port or '').strip()


def _chip_serial_default(project: CHISPProject, key: str, fallback: int) -> int:
    chip_name = (project.chip or '').strip()
    if not chip_name:
        return int(fallback)
    db = _db()
    resolver = _resolver()
    serial_meta = resolver.transport_meta(chip_name, 'serial')
    family_name = str(serial_meta.get('backend_family') or '').strip()
    family_meta = db.families.get(family_name, {}) if family_name else {}
    for section_name in ('flash_defaults', 'flash_defaults_serial_inferred'):
        section = dict(family_meta.get(section_name) or {})
        chip_defaults = dict(section.get(chip_name) or {})
        value = chip_defaults.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
        value = section.get(f'default_{key}')
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
    for mode_key in ('serial_manual', 'serial_auto_di'):
        row = resolver.transport_mode_meta(chip_name, mode_key)
        value = row.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
    return int(fallback)


def _chip_serial_initial_baud(project: CHISPProject) -> int:
    _ = project
    return 115200


def _chip_serial_fast_baud(project: CHISPProject) -> int:
    try:
        value = int(project.operations.fast_baud)
        if value > 0:
            return value
    except Exception:
        pass
    return _chip_serial_default(project, 'fast_baud', 1000000)


def _common_serial(project: CHISPProject) -> dict:
    return {
        'mode': _mode_from_serial_settings(project),
        'port': _serial_port_selector(project),
        'baud': _chip_serial_initial_baud(project),
        'trace': bool(project.operations.trace_mode),
    }


def _common_serial_flash(project: CHISPProject) -> dict:
    common = _common_serial(project)
    common.update({
        'fast_baud': _chip_serial_fast_baud(project),
        'no_fast': bool(project.operations.no_fast),
        'verify': bool(project.operations.verify_after_flash),
        'verify_last': True,
    })
    return common


def _common_native_usb(project: CHISPProject) -> dict:
    return {
        'usb_selector': project.transport.usb_device.strip(),
        'usb_interface_number': project.transport.usb_interface_number,
        'usb_endpoint_out': project.transport.usb_endpoint_out,
        'usb_endpoint_in': project.transport.usb_endpoint_in,
        'trace': bool(project.operations.trace_mode),
        'verify': bool(project.operations.verify_after_flash),
    }




def _chip_probe_order(project: CHISPProject) -> list[str]:
    db = _db()
    current = (project.chip or '').strip()
    family = (project.family or '').strip().upper()
    ordered: list[str] = []
    seen: set[str] = set()

    def is_package_profile(name: str) -> bool:
        meta = dict(db.chips.get(name) or {})
        cross = dict(meta.get('public_cross_check') or {})
        return bool(str(cross.get('package_profile_of') or '').strip())

    def add(name: str, *, allow_package: bool = False) -> None:
        key = (name or '').strip()
        if not key or key in seen or key not in db.chips:
            return
        if not allow_package and is_package_profile(key):
            return
        ordered.append(key)
        seen.add(key)

    add(current, allow_package=True)
    for name in db.chips:
        if _chip_series_name(name) == family:
            add(str(name))
    for name in db.chips:
        add(str(name))
    return ordered


def _clone_project(project: CHISPProject) -> CHISPProject:
    return deepcopy(project)


def _iter_probe_projects(project: CHISPProject) -> list[tuple[CHISPProject, str]]:
    candidates = enumerate_connection_candidates(project)
    serial_entries = list(candidates.get('serial_port_entries') or [])[:3]
    usb_entries = list(candidates.get('usb_device_entries') or [])[:3]
    resolver = _resolver()

    current_serial = _serial_port_selector(project)
    current_usb = (project.transport.usb_device or '').strip() if project.transport.kind == 'usb' and not bool(project.transport.serial_auto_di) else ''
    if current_serial and not any(str(x.get('selector') or '').strip() == current_serial for x in serial_entries):
        serial_entries.insert(0, {'selector': current_serial})
    if current_usb and not any(str(x.get('selector') or '').strip() == current_usb for x in usb_entries):
        usb_entries.insert(0, {
            'selector': current_usb,
            'interface_number': project.transport.usb_interface_number,
            'endpoint_out': project.transport.usb_endpoint_out,
            'endpoint_in': project.transport.usb_endpoint_in,
        })
    attempts: list[tuple[CHISPProject, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_probe(chip: str, transport_kind: str, selector: str, *, interface_number=None, endpoint_out=None, endpoint_in=None, serial_auto_di: bool = False, note: str='') -> None:
        if transport_kind == 'usb':
            key = (
                chip,
                transport_kind,
                selector,
                '' if interface_number is None else str(int(interface_number)),
                '' if endpoint_out is None else f'{int(endpoint_out) & 0xFF:02x}',
                '' if endpoint_in is None else f'{int(endpoint_in) & 0xFF:02x}',
                bool(serial_auto_di),
            )
        else:
            key = (chip, transport_kind, selector, bool(serial_auto_di))
        if not chip or not selector or key in seen:
            return
        probe = _clone_project(project)
        probe.chip = chip
        probe.family = _chip_series_name(chip) or probe.family
        probe.transport.kind = transport_kind
        probe.transport.serial_port = ''
        probe.transport.usb_device = ''
        probe.transport.usb_interface_number = interface_number
        probe.transport.usb_endpoint_out = endpoint_out
        probe.transport.usb_endpoint_in = endpoint_in
        probe.transport.serial_auto_di = bool(serial_auto_di)
        if transport_kind == 'serial':
            probe.transport.serial_port = selector
        else:
            probe.transport.usb_device = selector
        attempts.append((probe, note or f'{chip} via {transport_kind} @ {selector}'))
        seen.add(key)

    for chip in _chip_probe_order(project):
        serial_resolved = None
        usb_resolved = None
        try:
            serial_resolved = resolver.resolve(chip, transport='serial')
        except Exception:
            serial_resolved = None
        try:
            usb_resolved = resolver.resolve(chip, transport='usb')
        except Exception:
            usb_resolved = None

        chip_probe = _clone_project(project)
        chip_probe.chip = chip

        if serial_resolved is not None:
            for entry in serial_entries:
                selector = str(entry.get('selector') or '').strip()
                add_probe(chip, 'serial', selector, note=f'{chip} serial @ {selector}')
            if _chip_supports_serial_auto_di(chip_probe):
                for entry in serial_entries:
                    selector = str(entry.get('selector') or '').strip()
                    add_probe(chip, 'serial', selector, serial_auto_di=True, note=f'{chip} USB-UART Auto DI @ {selector}')

        if usb_resolved is not None and getattr(usb_resolved, 'protocol_variant', '') == 'usb_native_plain':
            for entry in usb_entries:
                selector = str(entry.get('selector') or '').strip()
                add_probe(
                    chip,
                    'usb',
                    selector,
                    interface_number=entry.get('interface_number'),
                    endpoint_out=entry.get('endpoint_out'),
                    endpoint_in=entry.get('endpoint_in'),
                    note=f'{chip} native USB @ {selector}',
                )

    return attempts


def run_project_smart_detect(project: CHISPProject, *, log_cb=None) -> dict:
    attempts = _iter_probe_projects(project)
    if not attempts:
        raise OperationError('no connection candidates to probe')

    last_error = ''
    for idx, (probe, label) in enumerate(attempts, start=1):
        if log_cb is not None:
            log_cb('INFO', f'smart detect: try {idx}/{len(attempts)} - {label}')
        try:
            result = run_project_detect(probe, log_cb=log_cb)
            result['probe_label'] = label
            result['probe_attempts'] = idx
            result['smart_detect'] = True
            return result
        except Exception as exc:
            last_error = str(exc)
            if log_cb is not None:
                log_cb('INFO', f'smart detect miss: {label} - {last_error}')

    raise OperationError(last_error or 'smart detect failed')

def run_project_detect(project: CHISPProject, *, log_cb=None) -> dict:
    resolved = resolve_project(project)
    backend = make_backend(resolved.backend_family)

    if resolved.protocol_variant == 'usb_native_plain':
        if not hasattr(backend, 'detect_native_usb'):
            raise OperationError(f'detect is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        result = backend.detect_native_usb(
            resolved.chip_name,
            log_cb=log_cb,
            **_common_native_usb(project),
        )
    else:
        if not hasattr(backend, 'detect_uart_framed'):
            raise OperationError(f'detect is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        result = backend.detect_uart_framed(
            resolved.chip_name,
            log_cb=log_cb,
            chip_meta=resolved.chip_meta,
            **_common_serial(project),
        )

    result['serial_auto_di'] = bool(project.transport.serial_auto_di) and _chip_supports_serial_auto_di(project)
    result.update(_match_detect_result(project, resolved, result))
    return result


def run_project_read_config(project: CHISPProject, *, log_cb=None) -> dict:
    resolved = resolve_project(project)
    backend = make_backend(resolved.backend_family)

    if resolved.protocol_variant == 'usb_native_plain':
        if not hasattr(backend, 'read_config_native_usb'):
            raise OperationError(f'read config is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        return backend.read_config_native_usb(
            resolved.chip_name,
            log_cb=log_cb,
            **_common_native_usb(project),
        )

    if not hasattr(backend, 'read_config_uart_framed'):
        raise OperationError(f'read config is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
    return backend.read_config_uart_framed(
        resolved.chip_name,
        log_cb=log_cb,
        chip_meta=resolved.chip_meta,
        **_common_serial(project),
    )



def run_project_write_config(project: CHISPProject, *, log_cb=None) -> dict:
    resolved = resolve_project(project)
    backend = make_backend(resolved.backend_family)

    if resolved.protocol_variant == 'usb_native_plain':
        if not hasattr(backend, 'write_config_native_usb'):
            raise OperationError(f'write config is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        return backend.write_config_native_usb(
            resolved.chip_name,
            config=project.config,
            log_cb=log_cb,
            **_common_native_usb(project),
        )

    if not hasattr(backend, 'write_config_uart_framed'):
        raise OperationError(f'write config is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
    return backend.write_config_uart_framed(
        resolved.chip_name,
        config=project.config,
        log_cb=log_cb,
        **_common_serial(project),
    )


def run_project_erase_only(project: CHISPProject, *, log_cb=None, progress_cb=None) -> dict:
    resolved = resolve_project(project)
    backend = make_backend(resolved.backend_family)
    common = {
        'trace': bool(project.operations.trace_mode),
        'log_cb': log_cb,
        'progress_cb': progress_cb,
    }

    if resolved.protocol_variant == 'usb_native_plain':
        if not hasattr(backend, 'erase_native_usb'):
            raise OperationError(f'erase only is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        return backend.erase_native_usb(
            resolved.chip_name,
            usb_selector=project.transport.usb_device.strip(),
            usb_interface_number=project.transport.usb_interface_number,
            usb_endpoint_out=project.transport.usb_endpoint_out,
            usb_endpoint_in=project.transport.usb_endpoint_in,
            **common,
        )

    if not hasattr(backend, 'erase_uart_framed'):
        raise OperationError(f'erase only is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
    common.update(_common_serial(project))
    return backend.erase_uart_framed(
        resolved.chip_name,
        chip_meta=resolved.chip_meta,
        **common,
    )


def run_project_verify_only(project: CHISPProject, *, log_cb=None, progress_cb=None) -> dict:
    if not project.firmware_path:
        raise OperationError('firmware path is empty')
    if not Path(project.firmware_path).is_file():
        raise OperationError(f'firmware file not found: {project.firmware_path}')

    resolved = resolve_project(project)
    backend = make_backend(resolved.backend_family)
    common = {
        'trace': bool(project.operations.trace_mode),
        'log_cb': log_cb,
        'progress_cb': progress_cb,
    }

    if resolved.protocol_variant == 'usb_native_plain':
        if not hasattr(backend, 'verify_native_usb'):
            raise OperationError(f'verify only is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        return backend.verify_native_usb(
            resolved.chip_name,
            project.firmware_path,
            usb_selector=project.transport.usb_device.strip(),
            usb_interface_number=project.transport.usb_interface_number,
            usb_endpoint_out=project.transport.usb_endpoint_out,
            usb_endpoint_in=project.transport.usb_endpoint_in,
            **common,
        )

    if not hasattr(backend, 'verify_uart_framed'):
        raise OperationError(f'verify only is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
    common.update(_common_serial(project))
    return backend.verify_uart_framed(
        resolved.chip_name,
        project.firmware_path,
        chip_meta=resolved.chip_meta,
        **common,
    )

def run_project_flash(project: CHISPProject, *, log_cb=None, progress_cb=None) -> dict:
    if not project.firmware_path:
        raise OperationError('firmware path is empty')
    if not Path(project.firmware_path).is_file():
        raise OperationError(f'firmware file not found: {project.firmware_path}')

    resolved = resolve_project(project)
    backend = make_backend(resolved.backend_family)
    common = {
        'trace': bool(project.operations.trace_mode),
        'log_cb': log_cb,
        'progress_cb': progress_cb,
    }

    if resolved.protocol_variant == 'usb_native_plain':
        if not hasattr(backend, 'flash_native_usb'):
            raise OperationError(f'flash is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')
        return backend.flash_native_usb(
            resolved.chip_name,
            project.firmware_path,
            verify=bool(project.operations.verify_after_flash),
            usb_selector=project.transport.usb_device.strip(),
            usb_interface_number=project.transport.usb_interface_number,
            usb_endpoint_out=project.transport.usb_endpoint_out,
            usb_endpoint_in=project.transport.usb_endpoint_in,
            **common,
        )

    if not hasattr(backend, 'flash_uart_framed'):
        raise OperationError(f'flash is not implemented for backend={resolved.backend_family} variant={resolved.protocol_variant}')

    common.update(_common_serial_flash(project))
    return backend.flash_uart_framed(
        resolved.chip_name,
        project.firmware_path,
        chip_meta=resolved.chip_meta,
        **common,
    )
