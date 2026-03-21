from __future__ import annotations

import json
import subprocess
import sys

cmd = [
    sys.executable,
    '-m',
    'chisp_flasher.cli.main',
    'resolve',
    '--chip',
    'CH32X035',
    '--mode',
    'native-usb',
    '--usb-device',
    '1a86:55e0',
    '--format',
    'json',
]

proc = subprocess.run(cmd, text=True, capture_output=True)
print('exit_code =', proc.returncode)
print('stderr =')
print(proc.stderr.strip())
print('stdout =')
print(proc.stdout.strip())

if proc.returncode == 0:
    envelope = json.loads(proc.stdout)
    print('resolved backend_family =', envelope['result']['resolved']['backend_family'])
    print('resolved protocol_variant =', envelope['result']['resolved']['protocol_variant'])
