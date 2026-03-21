from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chisp_flasher import __version__
from chisp_flasher import api
from chisp_flasher.core.errors import BackendError, OperationError, ProjectFormatError, TransportError
from chisp_flasher.formats.projectfmt import CHISPProject

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_DEVICE = 3
EXIT_CONNECT = 4
EXIT_DETECT = 5
EXIT_CONFIG = 6
EXIT_FLASH = 7
EXIT_VERIFY = 8
EXIT_INTERRUPTED = 9
SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _print_stderr(text: str) -> None:
    sys.stderr.write(text.rstrip() + '\n')
    sys.stderr.flush()


class EventSink:
    def __init__(self, path: str):
        self.path = str(path or '').strip()
        self.stream = None
        self._owns_stream = False
        self._use_stderr = self.path == '-'
        if not self.path:
            return
        if self._use_stderr:
            self.stream = sys.stderr
            return
        target = Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.stream = target.open('w', encoding='utf-8')
        self._owns_stream = True

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.stream is None:
            return
        row = {'schema_version': SCHEMA_VERSION, 'ts_utc': _utc_now(), 'type': event_type}
        row.update(payload)
        self.stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.stream.flush()

    def close(self) -> None:
        if self._owns_stream and self.stream is not None:
            self.stream.close()
            self.stream = None


class Reporter:
    def __init__(self, *, quiet: bool, event_sink: EventSink | None):
        self.quiet = bool(quiet)
        self.event_sink = event_sink
        self._last_progress = None
        self._text_logs_enabled = not (self.event_sink is not None and self.event_sink.path == '-')

    def log(self, level: str, message: str) -> None:
        text = str(message or '').rstrip()
        if self.event_sink is not None:
            self.event_sink.emit('log', {'level': str(level or 'INFO'), 'message': text})
        if self.quiet or not self._text_logs_enabled:
            return
        _print_stderr(f'[{level}] {text}')

    def progress(self, pct: int, done: int, total: int) -> None:
        pct = int(pct)
        if self.event_sink is not None:
            self.event_sink.emit('progress', {'pct': pct, 'done': int(done), 'total': int(total)})
        if self.quiet or not self._text_logs_enabled:
            return
        if pct == self._last_progress:
            return
        self._last_progress = pct
        _print_stderr(f'[INFO] progress: {pct}%')

    def result(self, envelope: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink.emit('result', envelope)

    def error(self, envelope: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink.emit('error', envelope)


EXAMPLES = '''Examples:
  chisp list chips
  chisp chip info CH32X035
  chisp list usb
  chisp suggest --chip CH32X035 --mode native-usb
  chisp resolve --chip CH32X035 --mode native-usb --usb-device 1a86:55e0
  chisp detect --chip CH32X035 --mode native-usb --usb-device 1a86:55e0
  chisp flash --project board.chisp --firmware build/app.bin
  chisp flash --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --firmware app.bin --format json
  chisp write-config --project board.chisp --set data0=0x12 --set enable_rrp=true
  chisp doctor
'''


MODE_HELP = 'Connection mode: serial, auto-di, native-usb'


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--format', choices=['text', 'json'], default='text', help='Output format for final result')
    parser.add_argument('--quiet', action='store_true', help='Suppress live logs on stderr')
    parser.add_argument('--events-jsonl', default='', help='Optional path for structured JSONL event stream, or - for stderr-only JSONL')


def _add_target_args(parser: argparse.ArgumentParser, *, require_chip: bool, include_project: bool = True) -> None:
    if include_project:
        parser.add_argument('--project', default='', help='Path to .chisp project file')
    parser.add_argument('--chip', required=require_chip, default='', help='Target chip name, e.g. CH32X035')
    parser.add_argument('--family', default='', help='Optional family override, e.g. CH32X')
    parser.add_argument('--mode', choices=['serial', 'auto-di', 'native-usb'], default='', help=MODE_HELP)
    parser.add_argument('--serial-port', default='', help='Serial port selector, e.g. /dev/ttyUSB0 or COM4')
    parser.add_argument('--usb-device', default='', help='USB selector, e.g. 1a86:55e0 or 1a86:55e0:03:10')
    parser.add_argument('--usb-interface', default='', help='USB interface number, decimal or hex')
    parser.add_argument('--usb-ep-out', default='', help='USB bulk OUT endpoint, decimal or hex')
    parser.add_argument('--usb-ep-in', default='', help='USB bulk IN endpoint, decimal or hex')


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--trace', action='store_true', help='Enable transport trace mode')
    parser.add_argument('--fast-baud', default='', help='Serial fast baud for flash/verify, decimal or hex')
    parser.add_argument('--no-fast', action='store_true', help='Disable fast baud switch during flash/verify')


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--set', action='append', default=[], metavar='KEY=VALUE', help='Override project config field(s), repeatable')


def _add_smart_detect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--max-ports', type=int, default=3, help='Maximum number of serial port candidates to probe')
    parser.add_argument('--max-usb', type=int, default=3, help='Maximum number of native USB candidates to probe')


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='chisp',
        description='CHISP CLI - cross-platform CLI for WCH CH32, CH5x and CH6x flashing. Supports serial bootloader, serial Auto DI and native USB bootloader workflows.',
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    sub = parser.add_subparsers(dest='command')

    list_parser = sub.add_parser('list', help='List chips, serial ports or USB devices', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    list_sub = list_parser.add_subparsers(dest='list_command')
    p = list_sub.add_parser('chips', help='List supported chips')
    p.add_argument('--family', default='', help='Optional family filter, e.g. CH32X')
    _add_output_args(p)
    p.set_defaults(func=_cmd_list_chips)
    p = list_sub.add_parser('ports', help='List serial ports')
    _add_output_args(p)
    p.set_defaults(func=_cmd_list_ports)
    p = list_sub.add_parser('usb', help='List native USB devices')
    _add_output_args(p)
    p.set_defaults(func=_cmd_list_usb)
    p = list_sub.add_parser('config-fields', help='List config fields visible for a chip/mode')
    _add_output_args(p)
    _add_target_args(p, require_chip=True, include_project=False)
    p.set_defaults(func=_cmd_list_config_fields)

    chip_parser = sub.add_parser('chip', help='Chip info helpers', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    chip_sub = chip_parser.add_subparsers(dest='chip_command')
    p = chip_sub.add_parser('info', help='Show info about one chip')
    p.add_argument('chip', help='Chip name, e.g. CH32X035')
    _add_output_args(p)
    p.set_defaults(func=_cmd_chip_info)

    for name, help_text, func in [
        ('suggest', 'Suggest likely serial/USB connection for the selected chip', _cmd_suggest),
        ('resolve', 'Resolve effective backend, protocol and visible config fields without touching hardware', _cmd_resolve),
        ('detect', 'Detect selected chip using the provided connection', _cmd_detect),
        ('smart-detect', 'Try multiple chips and transports until detect succeeds', _cmd_smart_detect),
        ('read-config', 'Read option/config bytes from target', _cmd_read_config),
        ('write-config', 'Write option/config bytes to target', _cmd_write_config),
        ('erase', 'Erase target flash only', _cmd_erase),
        ('verify', 'Verify target flash against firmware file', _cmd_verify),
        ('flash', 'Flash firmware to target', _cmd_flash),
    ]:
        p = sub.add_parser(name, help=help_text, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
        _add_output_args(p)
        _add_target_args(p, require_chip=False)
        _add_runtime_args(p)
        _add_config_args(p)
        if name == 'smart-detect':
            _add_smart_detect_args(p)
        if name in {'verify', 'flash'}:
            p.add_argument('--firmware', default='', help='Firmware file path')
        if name == 'flash':
            g = p.add_mutually_exclusive_group()
            g.add_argument('--verify', dest='verify_after_flash', action='store_true', default=None, help='Verify after flash')
            g.add_argument('--no-verify', dest='verify_after_flash', action='store_false', default=None, help='Disable verify after flash')
        p.set_defaults(func=func)

    project_parser = sub.add_parser('project', help='Project file helpers', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    project_sub = project_parser.add_subparsers(dest='project_command')
    p = project_sub.add_parser('init', help='Create a .chisp project file')
    _add_output_args(p)
    _add_target_args(p, require_chip=True, include_project=False)
    _add_runtime_args(p)
    _add_config_args(p)
    p.add_argument('--firmware', default='', help='Optional firmware path to store in project')
    p.add_argument('--name', default='', help='Optional project display name')
    p.add_argument('--output', required=True, help='Output .chisp path')
    p.set_defaults(func=_cmd_project_init)
    p = project_sub.add_parser('show', help='Show project file content')
    p.add_argument('--project', required=True, help='Path to .chisp project')
    _add_output_args(p)
    p.set_defaults(func=_cmd_project_show)
    p = project_sub.add_parser('validate', help='Validate project file')
    p.add_argument('--project', required=True, help='Path to .chisp project')
    _add_output_args(p)
    p.set_defaults(func=_cmd_project_validate)

    p = sub.add_parser('doctor', help='Check CLI/runtime environment and visible devices', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_output_args(p)
    p.set_defaults(func=_cmd_doctor)
    return parser


def _parse_int(value: str, *, name: str) -> int | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return int(raw, 0)
    except Exception as exc:
        raise ProjectFormatError(f'bad {name}: {value!r}') from exc


def _coerce_bool(value: str) -> bool:
    s = str(value or '').strip().lower()
    if s in {'1', 'true', 'yes', 'on', 'y'}:
        return True
    if s in {'0', 'false', 'no', 'off', 'n'}:
        return False
    raise ProjectFormatError(f'bad boolean value: {value!r}')


def _default_chip_for_family(family: str) -> str:
    names = api.list_chips(family=str(family or '').strip())
    if not names:
        raise ProjectFormatError(f'no chips found for family: {family!r}')
    return str(names[0]['chip'])


def _load_base_project(args) -> CHISPProject:
    if str(getattr(args, 'project', '') or '').strip():
        project = api.load_project_file(str(args.project).strip())
        return api.clone_project(project)

    chip = str(getattr(args, 'chip', '') or '').strip()
    family = str(getattr(args, 'family', '') or '').strip()
    if chip or family:
        if not chip and family:
            chip = _default_chip_for_family(family)
        return api.make_project(chip=chip, family=family)

    project = CHISPProject()
    project.chip = ''
    project.family = ''
    project.firmware_path = ''
    return project


def _apply_config_overrides(project: CHISPProject, pairs: list[str]) -> None:
    for item in pairs:
        text = str(item or '').strip()
        if '=' not in text:
            raise ProjectFormatError(f'bad --set entry: {item!r}')
        key, raw_value = text.split('=', 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not hasattr(project.config, key):
            raise ProjectFormatError(f'unknown config field: {key!r}')
        current = getattr(project.config, key)
        setattr(project.config, key, _coerce_bool(raw_value) if isinstance(current, bool) else raw_value)


def _apply_target_overrides(project: CHISPProject, args) -> CHISPProject:
    chip = str(getattr(args, 'chip', '') or '').strip()
    family = str(getattr(args, 'family', '') or '').strip()
    mode = str(getattr(args, 'mode', '') or '').strip()
    if chip:
        project.chip = chip
    if family:
        project.family = family
    elif project.chip:
        project.family = api.make_project(chip=project.chip).family
    if mode == 'serial':
        project.transport.kind = 'serial'
        project.transport.serial_auto_di = False
    elif mode == 'auto-di':
        project.transport.kind = 'serial'
        project.transport.serial_auto_di = True
    elif mode == 'native-usb':
        project.transport.kind = 'usb'
        project.transport.serial_auto_di = False
    serial_port = str(getattr(args, 'serial_port', '') or '').strip()
    if serial_port:
        project.transport.serial_port = serial_port
    usb_device = str(getattr(args, 'usb_device', '') or '').strip()
    if usb_device:
        project.transport.usb_device = usb_device
    usb_interface = _parse_int(getattr(args, 'usb_interface', ''), name='usb-interface')
    usb_ep_out = _parse_int(getattr(args, 'usb_ep_out', ''), name='usb-ep-out')
    usb_ep_in = _parse_int(getattr(args, 'usb_ep_in', ''), name='usb-ep-in')
    if usb_interface is not None:
        project.transport.usb_interface_number = usb_interface
    if usb_ep_out is not None:
        project.transport.usb_endpoint_out = usb_ep_out
    if usb_ep_in is not None:
        project.transport.usb_endpoint_in = usb_ep_in
    firmware = str(getattr(args, 'firmware', '') or '').strip()
    if firmware:
        project.firmware_path = firmware
    if getattr(args, 'trace', False):
        project.operations.trace_mode = True
    fast_baud = _parse_int(getattr(args, 'fast_baud', ''), name='fast-baud')
    if fast_baud is not None:
        project.operations.fast_baud = int(fast_baud)
    if getattr(args, 'no_fast', False):
        project.operations.no_fast = True
    verify_after_flash = getattr(args, 'verify_after_flash', None)
    if verify_after_flash is not None:
        project.operations.verify_after_flash = bool(verify_after_flash)

    if _project_mode(project) == 'native-usb':
        project.transport.serial_port = ''
    else:
        project.transport.usb_device = ''
        project.transport.usb_interface_number = None
        project.transport.usb_endpoint_out = None
        project.transport.usb_endpoint_in = None

    _apply_config_overrides(project, list(getattr(args, 'set', []) or []))
    return project


def _project_mode(project: CHISPProject) -> str:
    if str(project.transport.kind or '').strip() == 'usb':
        return 'native-usb'
    if bool(project.transport.serial_auto_di):
        return 'auto-di'
    return 'serial'


def _project_selector(project: CHISPProject) -> str:
    if _project_mode(project) == 'native-usb':
        return str(project.transport.usb_device or '').strip()
    return str(project.transport.serial_port or '').strip()


def _require_project_chip(project: CHISPProject, *, project_arg: str = '') -> None:
    if str(project.chip or '').strip():
        return
    if str(project_arg or '').strip():
        raise ProjectFormatError('project file does not define chip; pass --chip to override or fix the .chisp file')
    raise ProjectFormatError('chip is required unless --project points to a valid .chisp file')


def _project_meta(project: CHISPProject | None) -> dict[str, Any]:
    if project is None:
        return {}
    return {
        'chip': str(project.chip or '').strip(),
        'family': str(project.family or '').strip(),
        'mode': _project_mode(project),
        'selector': _project_selector(project),
        'firmware_path': str(project.firmware_path or '').strip(),
    }


def _result_envelope(*, action: str, ok: bool, payload: Any, project: CHISPProject | None = None, exit_code: int = EXIT_OK) -> dict[str, Any]:
    out: dict[str, Any] = {
        'ok': bool(ok),
        'action': action,
        'version': __version__,
        'timestamp_utc': _utc_now(),
        'exit_code': int(exit_code),
        'meta': _project_meta(project),
        'schema_version': SCHEMA_VERSION,
        'result': payload,
    }
    if project is not None:
        out['project'] = api.project_to_dict(project)
    return out


def _command_name(args) -> str:
    command = str(getattr(args, 'command', '') or '').strip()
    if command == 'list':
        command = f'list-{str(getattr(args, "list_command", "") or "").strip()}'
    elif command == 'chip':
        command = f'chip-{str(getattr(args, "chip_command", "") or "").strip()}'
    elif command == 'project':
        command = f'project-{str(getattr(args, "project_command", "") or "").strip()}'
    return command or 'cli'


def _exception_hints(exc: Exception) -> list[str]:
    text = str(exc).lower()
    hints: list[str] = []
    if 'permission' in text or 'access denied' in text:
        hints.append('Check OS permissions for the selected serial port or USB device.')
        hints.append('On Linux native USB bootloader targets usually need the bundled 50-chisp-flasher.rules rule.')
    if 'could not configure port' in text:
        hints.append('Make sure no other application is holding the serial port open.')
    if 'device not found' in text or 'selector is empty' in text:
        hints.append('Run chisp list ports or chisp list usb and reselect the current device.')
        hints.append('Native USB bootloaders can re-enumerate and change bus/address between attempts.')
    if 'unexpected chip_id/type' in text or 'identify' in text:
        hints.append('Selected chip and actual target do not match, or the target is not in the expected boot mode.')
    if 'verify' in text:
        hints.append('Firmware content on target does not match the selected image.')
    if 'firmware path is empty' in text or 'firmware file not found' in text:
        hints.append('Pass --firmware or store firmware_path in the .chisp project file.')
    uniq: list[str] = []
    for item in hints:
        if item not in uniq:
            uniq.append(item)
    return uniq


def _print_key_values(pairs: list[tuple[str, Any]]) -> None:
    for key, value in pairs:
        if value is None or value == '' or value == []:
            continue
        sys.stdout.write(f'{key}: {value}\n')


def _print_text_result(envelope: dict[str, Any]) -> None:
    action = str(envelope.get('action') or '')
    result = envelope.get('result')
    meta = dict(envelope.get('meta') or {})

    if action == 'list-chips':
        for row in result:
            sys.stdout.write(f"{row['chip']} [{row['family']}] {', '.join(row.get('transport_support') or [])}\n")
        return

    if action == 'list-ports':
        for row in result:
            sys.stdout.write(f"{row['selector']} - {row['display']}\n")
        return

    if action == 'list-usb':
        for row in result:
            sys.stdout.write(f"{row['selector']} - {row['display']}\n")
        return

    if action == 'chip-info':
        sys.stdout.write(f"Chip: {result['chip']}\n")
        _print_key_values([
            ('Family', result.get('family')),
            ('Transport support', ', '.join(result.get('transport_support') or [])),
            ('Known modes', ', '.join(result.get('modes') or [])),
            ('Package profile of', result.get('package_profile_of')),
        ])
        return

    if action == 'list-config-fields':
        sys.stdout.write(f"Chip: {result['chip']}\nMode: {result['mode']}\nVisible config fields:\n")
        for key in result.get('fields') or []:
            sys.stdout.write(f'- {key}\n')
        return

    if action == 'project-init':
        sys.stdout.write(f"Project saved: {result['path']}\n")
        return

    if action == 'project-show':
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        return

    if action == 'project-validate':
        sys.stdout.write('Project valid\n')
        resolved = dict((result or {}).get('resolved') or {})
        _print_key_values([
            ('Chip', resolved.get('chip')),
            ('Backend family', resolved.get('backend_family')),
            ('Protocol variant', resolved.get('protocol_variant')),
            ('Connection mode', resolved.get('display_connection_mode')),
        ])
        return

    if action == 'doctor':
        sys.stdout.write('Doctor report\n')
        for check in result.get('checks') or []:
            line = f"- {check['name']}: {check['status']}"
            detail = check.get('detail')
            if isinstance(detail, str) and detail:
                line += f' - {detail}'
            elif isinstance(detail, dict) and 'count' in detail:
                line += f" - count={detail['count']}"
            sys.stdout.write(line + '\n')
        hints = result.get('hints') or []
        if hints:
            sys.stdout.write('\nHints:\n')
            for item in hints:
                sys.stdout.write(f'- {item}\n')
        return

    if action == 'suggest':
        suggestion = dict(result.get('suggestion') or {})
        sys.stdout.write(f"{suggestion.get('label') or 'No suggestion'}\n")
        _print_key_values([
            ('Selector', suggestion.get('selector')),
            ('Mode', suggestion.get('transport')),
            ('Details', suggestion.get('details')),
        ])
        return

    if action == 'resolve':
        resolved = dict((result or {}).get('resolved') or {})
        sys.stdout.write('Resolved project\n')
        _print_key_values([
            ('Chip', resolved.get('chip')),
            ('Backend family', resolved.get('backend_family')),
            ('Protocol variant', resolved.get('protocol_variant')),
            ('Connection mode', resolved.get('display_connection_mode')),
            ('Mode', resolved.get('mode')),
            ('Selector', resolved.get('selector')),
            ('Firmware', resolved.get('firmware_path')),
            ('Visible config fields', ', '.join(resolved.get('visible_config_fields') or [])),
        ])
        return

    if action in {'detect', 'smart-detect'}:
        sys.stdout.write(f"Detected: {result.get('chip') or meta.get('chip') or '-'}\n")
        _print_key_values([
            ('Mode', meta.get('mode')),
            ('Selector', meta.get('selector')),
            ('Identify', None if result.get('chip_id') is None or result.get('chip_type') is None else f"0x{int(result['chip_id']):02x}/0x{int(result['chip_type']):02x}"),
            ('Matched chip', result.get('matched_chip')),
            ('Match reason', result.get('match_reason')),
            ('Probe label', result.get('probe_label')),
            ('Probe attempts', None if result.get('probe_attempts') is None else f"{int(result['probe_attempts'])}/{int(result.get('probe_attempts_total') or result.get('probe_attempts') or 0)}"),
            ('UID', result.get('uid_hex')),
        ])
        return

    if action in {'read-config', 'write-config'}:
        sys.stdout.write(f"Config {'read' if action == 'read-config' else 'written'}: {meta.get('chip') or '-'}\n")
        _print_key_values([
            ('Mode', meta.get('mode')),
            ('Selector', meta.get('selector')),
            ('UID', result.get('uid_hex')),
            ('rdpr_user', result.get('rdpr_user_hex')),
            ('cfg_data', result.get('cfg_data_hex')),
            ('cfg_wpr', result.get('cfg_wpr_hex')),
        ])
        return

    if action in {'erase', 'verify', 'flash'}:
        title = {'erase': 'Erase OK', 'verify': 'Verify OK', 'flash': 'Flash OK'}[action]
        sys.stdout.write(f"{title}: {meta.get('chip') or '-'}\n")
        _print_key_values([
            ('Mode', meta.get('mode')),
            ('Selector', meta.get('selector')),
            ('Firmware', meta.get('firmware_path')),
            ('Duration', None if result.get('duration_s') is None else f"{float(result['duration_s']):.3f}s"),
            ('UID', result.get('uid_hex')),
        ])
        return

    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + '\n')


def _print_result(args, envelope: dict[str, Any]) -> int:
    if args.format == 'json':
        sys.stdout.write(json.dumps(envelope, indent=2, ensure_ascii=False) + '\n')
    else:
        _print_text_result(envelope)
    return int(envelope.get('exit_code', EXIT_OK))


def _map_exception_to_exit(exc: Exception) -> int:
    text = str(exc).lower()
    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    if isinstance(exc, ProjectFormatError):
        return EXIT_USAGE
    if isinstance(exc, (TransportError, BackendError)):
        if 'not found' in text or 'selector is empty' in text or 'no connection candidates' in text:
            return EXIT_NO_DEVICE
        if 'unexpected chip_id/type' in text or 'detect' in text or 'identify' in text:
            return EXIT_DETECT
        if 'permission' in text or 'access denied' in text or 'could not configure port' in text:
            return EXIT_CONNECT
        return EXIT_CONNECT
    if isinstance(exc, OperationError):
        if 'firmware file not found' in text or 'firmware path is empty' in text:
            return EXIT_USAGE
        if 'verify' in text:
            return EXIT_VERIFY
        if 'unexpected chip_id/type' in text or 'detect' in text or 'identify' in text:
            return EXIT_DETECT
        if 'config' in text:
            return EXIT_CONFIG
        if (
            'not found' in text
            or 'no connection candidates' in text
            or 'selector is empty' in text
            or 'no native usb candidates visible' in text
        ):
            return EXIT_NO_DEVICE
        return EXIT_FLASH
    if 'permission' in text or 'access denied' in text or 'could not configure port' in text:
        return EXIT_CONNECT
    if 'not found' in text or 'selector is empty' in text:
        return EXIT_NO_DEVICE
    return EXIT_FLASH


def _error_envelope(args, exc: Exception, code: int) -> dict[str, Any]:
    return {
        'ok': False,
        'action': _command_name(args),
        'version': __version__,
        'timestamp_utc': _utc_now(),
        'exit_code': int(code),
        'schema_version': SCHEMA_VERSION,
        'error': {
            'type': exc.__class__.__name__,
            'message': str(exc),
            'hints': _exception_hints(exc),
        },
    }


def _cmd_list_chips(args, _reporter: Reporter):
    return _result_envelope(action='list-chips', ok=True, payload=api.list_chips(family=args.family)), EXIT_OK


def _cmd_list_ports(args, _reporter: Reporter):
    return _result_envelope(action='list-ports', ok=True, payload=api.list_ports()), EXIT_OK


def _cmd_list_usb(args, _reporter: Reporter):
    return _result_envelope(action='list-usb', ok=True, payload=api.list_usb_devices()), EXIT_OK


def _cmd_chip_info(args, _reporter: Reporter):
    return _result_envelope(action='chip-info', ok=True, payload=api.get_chip_info(args.chip)), EXIT_OK


def _cmd_list_config_fields(args, _reporter: Reporter):
    project = _apply_target_overrides(_load_base_project(args), args)
    _require_project_chip(project, project_arg=getattr(args, 'project', ''))
    payload = api.get_visible_config_fields(project.chip, mode=_project_mode(project))
    return _result_envelope(action='list-config-fields', ok=True, payload=payload, project=project), EXIT_OK


def _cmd_suggest(args, _reporter: Reporter):
    project = _apply_target_overrides(_load_base_project(args), args)
    _require_project_chip(project, project_arg=getattr(args, 'project', ''))
    return _result_envelope(action='suggest', ok=True, payload=api.suggest(project), project=project), EXIT_OK


def _cmd_resolve(args, _reporter: Reporter):
    project = _apply_target_overrides(_load_base_project(args), args)
    _require_project_chip(project, project_arg=getattr(args, 'project', ''))
    return _result_envelope(action='resolve', ok=True, payload=api.resolve_effective_project(project), project=project), EXIT_OK


def _run_action(action: str, args, reporter: Reporter):
    project = _apply_target_overrides(_load_base_project(args), args)
    _require_project_chip(project, project_arg=getattr(args, 'project', ''))
    fn_map = {
        'detect': api.detect,
        'smart-detect': api.smart_detect,
        'read-config': api.read_config,
        'write-config': api.write_config,
        'erase': api.erase,
        'verify': api.verify,
        'flash': api.flash,
    }
    kwargs = {'log_cb': reporter.log}
    if action in {'erase', 'verify', 'flash'}:
        kwargs['progress_cb'] = reporter.progress
    if action == 'smart-detect':
        result = fn_map[action](
            project,
            max_ports=max(1, int(getattr(args, 'max_ports', 3) or 3)),
            max_usb=max(1, int(getattr(args, 'max_usb', 3) or 3)),
            **kwargs,
        )
    else:
        result = fn_map[action](project, **kwargs)
    return _result_envelope(action=action, ok=True, payload=result, project=project), EXIT_OK


def _cmd_detect(args, reporter: Reporter):
    return _run_action('detect', args, reporter)


def _cmd_smart_detect(args, reporter: Reporter):
    return _run_action('smart-detect', args, reporter)


def _cmd_read_config(args, reporter: Reporter):
    return _run_action('read-config', args, reporter)


def _cmd_write_config(args, reporter: Reporter):
    return _run_action('write-config', args, reporter)


def _cmd_erase(args, reporter: Reporter):
    return _run_action('erase', args, reporter)


def _cmd_verify(args, reporter: Reporter):
    return _run_action('verify', args, reporter)


def _cmd_flash(args, reporter: Reporter):
    return _run_action('flash', args, reporter)


def _cmd_project_init(args, _reporter: Reporter):
    project = _apply_target_overrides(_load_base_project(args), args)
    project.name = str(args.name or '').strip()
    output = str(args.output or '').strip()
    if not output:
        raise ProjectFormatError('output path is empty')
    path = Path(output)
    if path.suffix.lower() != '.chisp':
        path = path.with_suffix('.chisp')
    path.parent.mkdir(parents=True, exist_ok=True)
    api.save_project_file(path, project)
    return _result_envelope(action='project-init', ok=True, payload={'path': str(path)}, project=project), EXIT_OK


def _cmd_project_show(args, _reporter: Reporter):
    project = api.load_project_file(args.project)
    return _result_envelope(action='project-show', ok=True, payload=api.project_to_dict(project), project=project), EXIT_OK


def _cmd_project_validate(args, _reporter: Reporter):
    project = api.load_project_file(args.project)
    return _result_envelope(action='project-validate', ok=True, payload=api.validate_project(project), project=project), EXIT_OK


def _try_module_version(import_name: str, attr_name: str = '__version__') -> str:
    try:
        mod = __import__(import_name)
        return str(getattr(mod, attr_name, 'unknown'))
    except Exception:
        return 'missing'


def _linux_group_names() -> list[str]:
    if platform.system().lower() != 'linux':
        return []
    try:
        import grp
        return sorted({grp.getgrgid(gid).gr_name for gid in os.getgroups()})
    except Exception:
        return []


def _cmd_doctor(args, _reporter: Reporter):
    checks: list[dict[str, Any]] = []
    hints: list[str] = []

    def add(name: str, status: str, detail: Any = None):
        row = {'name': name, 'status': status}
        if detail is not None:
            row['detail'] = detail
        checks.append(row)

    add('python', 'ok', sys.version.split()[0])
    add('platform', 'ok', {'system': platform.system(), 'release': platform.release()})
    add('cwd', 'ok', os.getcwd())
    add('package_version', 'ok', __version__)
    add('pyserial', 'ok' if _try_module_version('serial') != 'missing' else 'missing', _try_module_version('serial'))
    add('pyusb', 'ok' if _try_module_version('usb') != 'missing' else 'missing', _try_module_version('usb'))
    add('pyside6', 'ok' if _try_module_version('PySide6') != 'missing' else 'missing', _try_module_version('PySide6'))
    add('requirements_cli_file', 'ok' if Path('requirements-cli.txt').is_file() else 'missing', 'requirements-cli.txt')
    add('requirements_gui_file', 'ok' if Path('requirements-gui.txt').is_file() else 'missing', 'requirements-gui.txt')

    groups = _linux_group_names()
    if groups:
        add('linux_groups', 'ok', groups)
        if not any(name in groups for name in ['dialout', 'uucp', 'plugdev']):
            hints.append('Serial access on Linux often requires membership in dialout or uucp, depending on distro.')

    try:
        ports = api.list_ports()
        add('serial_ports', 'ok', {'count': len(ports), 'items': ports})
    except Exception as exc:
        add('serial_ports', 'error', str(exc))
        hints.extend(_exception_hints(exc))

    try:
        usb = api.list_usb_devices()
        add('usb_devices', 'ok', {'count': len(usb), 'items': usb})
        bootloaders = [row for row in usb if int(row.get('vid') or 0) == 0x1A86 and int(row.get('pid') or 0) == 0x55E0]
        add('wch_native_usb_bootloaders', 'ok', {'count': len(bootloaders), 'items': bootloaders})
        if not bootloaders:
            hints.append('No WCH native USB bootloader (1a86:55e0) is visible right now.')
    except Exception as exc:
        add('usb_devices', 'error', str(exc))
        hints.extend(_exception_hints(exc))

    local_rules = Path('packaging/linux/50-chisp-flasher.rules')
    add('linux_udev_rules_repo_file', 'ok' if local_rules.is_file() else 'missing', str(local_rules))

    if platform.system().lower() == 'linux':
        system_rule_paths = [
            Path('/etc/udev/rules.d/50-chisp-flasher.rules'),
            Path('/lib/udev/rules.d/50-chisp-flasher.rules'),
            Path('/usr/lib/udev/rules.d/50-chisp-flasher.rules'),
        ]
        installed = [str(p) for p in system_rule_paths if p.is_file()]
        add('linux_udev_rules_installed', 'ok' if installed else 'missing', installed or [str(p) for p in system_rule_paths])
        if not installed:
            hints.append('For native USB on Linux install packaging/linux/50-chisp-flasher.rules and reload udev rules.')

    uniq_hints: list[str] = []
    for item in hints:
        if item not in uniq_hints:
            uniq_hints.append(item)

    return _result_envelope(action='doctor', ok=True, payload={'checks': checks, 'hints': uniq_hints}), EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, 'func'):
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    event_sink = EventSink(getattr(args, 'events_jsonl', ''))
    reporter = Reporter(quiet=bool(args.quiet), event_sink=event_sink)
    try:
        envelope, code = args.func(args, reporter)
        reporter.result(envelope)
        return _print_result(args, envelope) if code == EXIT_OK else code
    except KeyboardInterrupt as exc:
        envelope = _error_envelope(args, exc, EXIT_INTERRUPTED)
        reporter.error(envelope)
        if getattr(args, 'format', 'text') == 'json':
            sys.stdout.write(json.dumps(envelope, indent=2, ensure_ascii=False) + '\n')
        else:
            _print_stderr('Interrupted')
        return EXIT_INTERRUPTED
    except Exception as exc:
        code = _map_exception_to_exit(exc)
        envelope = _error_envelope(args, exc, code)
        reporter.error(envelope)
        if getattr(args, 'format', 'text') == 'json':
            sys.stdout.write(json.dumps(envelope, indent=2, ensure_ascii=False) + '\n')
        else:
            _print_stderr(f'[ERROR] {exc}')
            for hint in envelope['error'].get('hints') or []:
                _print_stderr(f'[HINT] {hint}')
        return code
    finally:
        event_sink.close()


if __name__ == '__main__':
    raise SystemExit(main())
