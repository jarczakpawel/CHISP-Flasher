#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -U pip
pip install -q -r requirements.txt
export PYTHONPATH=src
exec python -m chisp_flasher.app.main
