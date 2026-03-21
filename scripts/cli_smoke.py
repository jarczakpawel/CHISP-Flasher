from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(__import__('os').environ)
ENV['PYTHONPATH'] = str(ROOT / 'src')

COMMANDS = [
    ['--help'],
    ['list', 'chips', '--format', 'json'],
    ['chip', 'info', 'CH32X035', '--format', 'json'],
    ['resolve', '--chip', 'CH32X035', '--mode', 'native-usb', '--usb-device', '1a86:55e0', '--format', 'json'],
    ['doctor', '--format', 'json'],
]

for args in COMMANDS:
    cmd = [sys.executable, '-m', 'chisp_flasher.cli.main', *args]
    print('RUN', ' '.join(args))
    proc = subprocess.run(cmd, cwd=ROOT, env=ENV, text=True, capture_output=True)
    print('exit =', proc.returncode)
    if proc.stderr.strip():
        print('stderr:')
        print(proc.stderr.strip())
    if proc.stdout.strip():
        print('stdout:')
        print(proc.stdout.strip())
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    if '--format' in args and 'json' in args:
        json.loads(proc.stdout)
