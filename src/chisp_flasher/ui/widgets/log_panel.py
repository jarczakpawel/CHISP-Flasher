from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QPlainTextEdit


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str


class LogPanel(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setPlaceholderText('Log output will appear here')
        base = QColor('#fcfaf7')
        text_color = QColor('#302923')

        palette = self.palette()
        palette.setColor(QPalette.Base, base)
        palette.setColor(QPalette.Window, base)
        palette.setColor(QPalette.Text, text_color)
        self.setPalette(palette)

        viewport_palette = self.viewport().palette()
        viewport_palette.setColor(QPalette.Base, base)
        viewport_palette.setColor(QPalette.Window, base)
        viewport_palette.setColor(QPalette.Text, text_color)
        self.viewport().setPalette(viewport_palette)
        self.viewport().setAutoFillBackground(True)
        self.setStyleSheet('QPlainTextEdit { background: #fcfaf7; color: #302923; }')
        self.viewport().setStyleSheet('background: #fcfaf7;')
        self._entries: list[LogEntry] = []

    def append_line(self, level: str, message: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S')
        entry = LogEntry(timestamp=ts, level=str(level).upper(), message=str(message))
        self._entries.append(entry)
        self.appendPlainText(self._format_entry(entry))

    def clear_entries(self) -> None:
        self._entries.clear()
        self.clear()

    def format_entries(self, entries: Iterable[LogEntry] | None = None) -> str:
        rows = entries if entries is not None else self._entries
        return '\n'.join(self._format_entry(entry) for entry in rows)

    def export_text(self, *, visible_only: bool = True, start_line: int | None = None, end_line: int | None = None) -> str:
        entries = list(self._entries)
        start_index = start_line - 1 if start_line and start_line > 0 else 0
        entries = entries[start_index:]
        if end_line and end_line > 0:
            if start_line and start_line > 0:
                max_count = max(0, end_line - start_line + 1)
                entries = entries[:max_count]
            else:
                entries = entries[:end_line]
        return self.format_entries(entries)

    @staticmethod
    def _format_entry(entry: LogEntry) -> str:
        return f'[{entry.timestamp}] [{entry.level}] {entry.message}'
