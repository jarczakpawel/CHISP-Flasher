#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -U pip
if [ -f requirements-cli.txt ]; then
  pip install -q -r requirements-cli.txt
else
  pip install -q -r requirements.txt
fi
export PYTHONPATH=src
exec python -B -m chisp_flasher.cli.main "$@"
