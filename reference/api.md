# Python API

CHISP Flasher also exposes a Python API for integration with external applications, build systems and custom IDEs.

## Main entry points

- `api.make_project(...)`
- `api.resolve_effective_project(project)`
- `api.detect(project)`
- `api.read_config(project)`
- `api.write_config(project)`
- `api.erase(project)`
- `api.verify(project)`
- `api.flash(project)`
- `api.smart_detect(project)`

## Minimal example

```python
from chisp_flasher import api

project = api.make_project(
    chip='CH32X035',
    transport_kind='usb',
    usb_device='1a86:55e0',
    firmware_path='app.bin',
)

print(api.detect(project))
print(api.flash(project))
```

## Callbacks

Operations that talk to hardware accept callbacks:

- `log_cb(level, message)`
- `progress_cb(pct, done, total)`

## Examples

See:

- `examples/api_detect.py`
- `examples/api_flash.py`
- `examples/subprocess_resolve.py`
