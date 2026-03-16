from __future__ import annotations

from typing import Callable

from chisp_flasher.formats.firmware_image import load_firmware_image

LogFn = Callable[[str, str], None] | None
ProgressFn = Callable[[int, int, int], None] | None


class BackendBase:
    family_name = 'base'

    def require_file(self, path: str, *, chip_name: str = '', max_size: int = 0) -> bytes:
        return load_firmware_image(path, chip_name=chip_name, max_size=max_size)

    def log(self, log_cb: LogFn, level: str, message: str) -> None:
        if log_cb is not None:
            log_cb(level, message)

    def progress(self, progress_cb: ProgressFn, pct: int, done: int, total: int) -> None:
        if progress_cb is not None:
            progress_cb(int(pct), int(done), int(total))
