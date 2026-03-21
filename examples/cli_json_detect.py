from __future__ import annotations

import json
import subprocess
import sys

cmd = [
    sys.executable,
    '-m',
    'chisp_flasher.cli.main',
    'detect',
    '--chip',
    'CH32X035',
    '--mode',
    'native-usb',
    '--usb-device',
    '1a86:55e0',
    '--format',
    'json',
]

proc = subprocess.run(cmd, capture_output=True, text=True)
if proc.stderr:
    print('stderr logs:')
    print(proc.stderr.rstrip())

data = json.loads(proc.stdout)
print('exit code:', proc.returncode)
print('ok:', data.get('ok'))
print('schema_version:', data.get('schema_version'))
print('action:', data.get('action'))
print('result keys:', sorted((data.get('result') or {}).keys()))
