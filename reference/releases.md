# Release assets

## GUI assets

GitHub releases include GUI builds for:

- Windows
- Linux
- macOS

## Python package

GitHub releases also include Python package artifacts:

- `chisp_flasher-<version>.tar.gz`
- `chisp_flasher-<version>-py3-none-any.whl`

The Python package includes:

- CLI entrypoint: `chisp`
- GUI entrypoint: `chisp-flasher`
- Python API: `from chisp_flasher import api`

## Linux native USB rule

Linux GUI release bundles include:

- `50-chisp-flasher.rules`

For source/manual usage the same file is in:

- `packaging/linux/50-chisp-flasher.rules`
