# CLI

CHISP CLI is a cross-platform command line frontend over the same flashing core used by the GUI.

## Connection modes

- `serial`
- `auto-di`
- `native-usb`

## Main commands

```bash
chisp list chips
chisp list ports
chisp list usb
chisp chip info CH32X035
chisp resolve --chip CH32X035 --mode native-usb --usb-device 1a86:55e0
chisp detect --chip CH32X035 --mode native-usb --usb-device 1a86:55e0
chisp flash --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --firmware app.bin
```

## Project files

Project files are optional.

```bash
chisp flash --project board.chisp
chisp flash --chip CH32V203 --mode auto-di --serial-port /dev/ttyUSB0 --firmware firmware.bin
```

## Machine-readable output

Use JSON for external tools, CI and custom IDEs.

```bash
chisp detect --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --format json
chisp flash --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --firmware app.bin --format json --events-jsonl flash.jsonl
```

- `stdout` carries the final result envelope
- `stderr` carries live logs
- `--events-jsonl` writes structured event records
- envelopes and events include `schema_version`

## Exit codes

- `0` success
- `2` usage / validation error
- `3` no device
- `4` connect / permission error
- `5` detect mismatch
- `6` config read / write error
- `7` flash / erase error
- `8` verify error
- `9` interrupted

## Examples

See:

- `examples/cli_json_detect.py`
- `examples/subprocess_resolve.py`
