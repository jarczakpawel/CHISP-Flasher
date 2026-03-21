# CHISP Flasher

<p align="center">
  <img src="assets/readme/app-icon.png" alt="CHISP Flasher" width="160">
</p>

Cross-platform ISP flasher for WCH CH32, CH5x and CH6x devices.

## What it does

- flashes firmware images from `.bin`, `.hex`, `.ihex`, `.elf`, `.srec`, `.s19`, `.s28`, `.s37`, `.mot`
- saves and reopens `.chisp` project files
- supports `Serial / USB-UART`
- supports `USB-UART Auto DI`
- supports `Native USB bootloader`
- exposes supported config / option-byte operations from the active chip profile
- provides 3 interfaces over the same flashing core:
  - GUI app
  - CLI tool
  - Python API

## Supported operating systems

- Windows
- Linux
- macOS

## Release artifacts

| OS / type | Artifacts |
| --- | --- |
| Windows | GUI portable `.zip`, GUI installer `.exe` |
| Linux | GUI portable `.tar.gz`, `.deb`, `.rpm` |
| macOS | GUI portable `.zip`, `.dmg` |
| Python package | `chisp_flasher-<version>.tar.gz`, `chisp_flasher-<version>-py3-none-any.whl` |

## Transport modes

| Mode | Use case | Linux note |
| --- | --- | --- |
| Serial / USB-UART | Manual bootloader entry with a regular USB-UART adapter | Usually works if the user has access to the serial device |
| USB-UART Auto DI | USB-UART bridge wired so the app can drive boot/reset automatically | Same serial-device access rules as above |
| Native USB bootloader | Direct USB bootloader path for supported chips | Usually requires the bundled `50-chisp-flasher.rules` rule when using the portable/manual build |

## Supported chip groups

| Family / group | Chip list | Serial / USB-UART | USB-UART Auto DI | Native USB bootloader | Notes |
| --- | --- | --- | --- | --- | --- |
| CH32V00x / CH32M007 | CH32V002, CH32V003, CH32V004, CH32V005, CH32V006, CH32V007, CH32M007 | Yes | No | No | UART-only bootloader path |
| CH32F103 / CH32V103 | CH32F103, CH32V103 | Yes | Yes | Yes | Shared x103 path |
| CH32V20x | CH32V203, CH32V208 | Yes | Yes | Yes | Shared V20x path |
| CH32V30x | CH32V303, CH32V305, CH32V307, CH32V317 | Yes | Yes | Yes | Shared V30x path |
| CH32X03x | CH32X033, CH32X035 | Yes | Yes | Yes | Serial and native USB are available |
| CH32L103 | CH32L103 | Yes | Yes | Yes | Single-family path |
| CH32F20x | CH32F203, CH32F205, CH32F207, CH32F208 | Yes | Yes | Yes | Shared F20x path |
| CH54x | CH540, CH541, CH542, CH543, CH544, CH545, CH546, CH547, CH548, CH549 | Varies | Varies | Yes | Legacy family, native USB on supported chips |
| CH55x | CH551, CH552, CH553, CH554, CH555, CH556, CH557, CH558, CH559 | Varies | Varies | Yes | Legacy family, native USB on supported chips |
| CH56x | CH563, CH565, CH566, CH567, CH568, CH569 | No | Varies | Yes | Legacy family, native USB only |
| CH57x | CH570, CH570D, CH570E, CH570Q, CH571, CH571F, CH571K, CH571R, CH572, CH572D, CH572Q, CH573, CH577, CH578, CH578F, CH579 | Varies | Varies | Varies | Legacy family, support varies by exact chip |
| CH58x | CH581, CH582, CH583, CH584, CH584F, CH584M, CH585, CH585C, CH585D, CH585F, CH585M | Varies | Varies | Yes | Legacy family, support varies by exact chip |
| CH59x | CH591, CH591D, CH591F, CH591R, CH592, CH592A, CH592D, CH592F, CH592X | Varies | Varies | Yes | Legacy family, support varies by exact chip |

The current chip list is data-driven in `src/chisp_flasher/data/chipdb.yaml`.

## Quick start

### GUI

```bash
chmod +x run.sh
./run.sh
```

### CLI

```bash
chmod +x run_cli.sh
./run_cli.sh --help
```

List supported chips:

```bash
chisp list chips
```

Detect a native USB target:

```bash
chisp detect --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --format json
```

Flash firmware:

```bash
chisp flash --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --firmware app.bin
```

### Python API

```python
from chisp_flasher import api

project = api.make_project(
    chip='CH32X035',
    transport_kind='usb',
    usb_device='1a86:55e0',
    firmware_path='app.bin',
)

print(api.detect(project))
```

## Source environment

### GUI environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m chisp_flasher.app.main
```

### CLI-only environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-cli.txt
PYTHONPATH=src python -m chisp_flasher.cli.main --help
```

## Machine-readable integration

For scripts, CI and custom IDEs:

```bash
chisp flash --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --firmware app.bin --format json
chisp flash --chip CH32X035 --mode native-usb --usb-device 1a86:55e0 --firmware app.bin --format json --events-jsonl flash-events.jsonl
```

- `stdout` carries the final result envelope
- `stderr` carries live logs
- `--events-jsonl` writes structured event records

## Linux udev rule

Portable/manual Linux usage for the native USB bootloader path usually needs the bundled rule file.

The rule is already included in the Linux release packages:
- portable archive: `50-chisp-flasher.rules`
- system packages: installed automatically by the `.deb` / `.rpm`

Manual installation example:

```bash
sudo install -m 0644 packaging/linux/50-chisp-flasher.rules /etc/udev/rules.d/50-chisp-flasher.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
```

If you run from source, the same rule is available here:

```text
packaging/linux/50-chisp-flasher.rules
```

## Public reference

- `reference/cli.md`
- `reference/api.md`
- `reference/releases.md`
- `CHANGELOG.md`
- `examples/api_detect.py`
- `examples/api_flash.py`
- `examples/cli_json_detect.py`
- `examples/subprocess_resolve.py`

## Screenshot

![CHISP Flasher screenshot](assets/readme/screen.png)

## Using CHISP Flasher in other software

CHISP Flasher can be used in other software, including commercial projects.

If your project uses CHISP Flasher code, please include a visible note somewhere in your application, documentation, or repository that it is based on or uses CHISP Flasher.

If you want, send me a link to your project - I will be happy to add it to this README as software built with CHISP Flasher.

## Feedback

Issue reports are welcome:
- confirm what works
- report what does not work
- attach logs when possible
- request support for additional WCH chip families or exact chips
- share ideas for new features or workflow improvements
