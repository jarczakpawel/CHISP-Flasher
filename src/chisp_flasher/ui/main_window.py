from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
from dataclasses import replace
from copy import deepcopy
import re

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QToolButton,
    QStyleFactory,
)

from chisp_flasher.chipdb.loader import load_chipdb
from chisp_flasher.chipdb.resolver import ChipResolver
from chisp_flasher.backends.factory import make_backend
from chisp_flasher.core.app_state import (
    apply_saved_connection,
    default_state_path,
    describe_saved_connection,
    enrich_saved_connection_from_candidates,
    find_best_recent_connection,
    load_app_state,
    project_to_saved_connection,
    remember_recent_connection,
    save_app_state,
)
from chisp_flasher.core.errors import ChispError
from chisp_flasher.core.operations import (
    enumerate_connection_candidates,
    run_project_detect,
    run_project_erase_only,
    run_project_flash,
    run_project_read_config,
    run_project_smart_detect,
    run_project_verify_only,
    run_project_write_config,
)
from chisp_flasher.core.session import Session
from chisp_flasher.formats.projectfmt import CHISPProject, load_project, save_project
from chisp_flasher import __version__
from chisp_flasher.ui.config_layout import FIELD_NOTES, PROFILE_SUMMARY, SECTION_META, SECTION_ORDER
from chisp_flasher.ui.connection_guide import get_guide
from chisp_flasher.ui.widgets.log_panel import LogPanel

LABELS = {
    'enable_rrp': 'Read protection',
    'clear_codeflash': 'Clear code flash before apply',
    'disable_stop_mode_rst': 'Disable stop-mode reset',
    'disable_standby_mode_rst': 'Disable standby-mode reset',
    'enable_soft_ctrl_iwdg': 'Software IWDG control',
    'enable_long_delay_time': 'Long power-on delay',
    'ramx_rom_mode': 'RAMX / ROM layout',
    'data0': 'Data0',
    'data1': 'Data1',
    'wrp0': 'WRP0',
    'wrp1': 'WRP1',
    'wrp2': 'WRP2',
    'wrp3': 'WRP3',
    'fast_baud': 'Fast baud',
    'serial_auto_di': 'USB-UART Auto DI',
    'serial_port': 'Serial port',
    'usb_device': 'USB device',
    'usb_interface_number': 'Interface',
    'usb_endpoint_out': 'Endpoint OUT',
    'usb_endpoint_in': 'Endpoint IN',
}


class NoWheelComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(12)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def showPopup(self) -> None:
        super().showPopup()

        popup = self.view().window()
        if popup is None:
            return

        below = self.mapToGlobal(self.rect().bottomLeft())
        screen = QApplication.screenAt(below) or QApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        popup_geom = popup.frameGeometry()

        width = popup_geom.width()
        height = popup_geom.height()

        x = below.x()
        if x + width > available.right():
            x = available.right() - width
        if x < available.left():
            x = available.left()

        y = below.y()

        max_y = available.bottom() - height + 1
        if y > max_y:
            y = max_y
        if y < available.top():
            y = available.top()

        popup.setGeometry(x, y, width, height)


def _create_standard_file_dialog(parent: QWidget, title: str) -> QFileDialog:
    dialog = QFileDialog(parent, title)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setViewMode(QFileDialog.Detail)
    dialog.setMinimumSize(940, 660)
    dialog.resize(980, 680)

    style = QStyleFactory.create('Fusion')
    if style is not None:
        dialog.setStyle(style)

    palette = dialog.palette()
    palette.setColor(QPalette.Window, QColor('#ddd5ca'))
    palette.setColor(QPalette.Base, QColor('#fcfaf7'))
    palette.setColor(QPalette.AlternateBase, QColor('#f5efe8'))
    palette.setColor(QPalette.Button, QColor('#eee5da'))
    palette.setColor(QPalette.ButtonText, QColor('#302923'))
    palette.setColor(QPalette.WindowText, QColor('#302923'))
    palette.setColor(QPalette.Text, QColor('#302923'))
    palette.setColor(QPalette.Highlight, QColor('#e2d4c6'))
    palette.setColor(QPalette.HighlightedText, QColor('#302923'))
    dialog.setPalette(palette)
    return dialog


class FileField(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.setProperty('flatField', True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.line_edit = QLineEdit(self)
        self.button = QPushButton('Browse', self)
        self.button.setProperty('ghost', True)
        self.button.clicked.connect(self.pick)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.button)

    def pick(self) -> None:
        dialog = _create_standard_file_dialog(self, self.title)
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setNameFilters([
            'Firmware files (*.bin *.hex *.ihex *.elf *.srec *.s19 *.s28 *.s37 *.mot)',
            'All files (*.*)',
        ])
        dialog.selectNameFilter('Firmware files (*.bin *.hex *.ihex *.elf *.srec *.s19 *.s28 *.s37 *.mot)')

        current = self.text()
        if current:
            current_path = Path(current)
            if current_path.is_file():
                dialog.setDirectory(str(current_path.parent))
                dialog.selectFile(str(current_path))
            elif current_path.parent.exists():
                dialog.setDirectory(str(current_path.parent))
        elif Path.cwd().exists():
            dialog.setDirectory(str(Path.cwd()))

        if dialog.exec() == QDialog.Accepted:
            files = dialog.selectedFiles()
            if files:
                self.line_edit.setText(files[0])

    def text(self) -> str:
        return self.line_edit.text().strip()

    def setText(self, value: str) -> None:
        self.line_edit.setText(value)


class ActionWorker(QObject):
    log = Signal(str, str)
    progress = Signal(int)
    done = Signal(str, dict)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, action: str, project):
        super().__init__()
        self.action = action
        self.project = replace(project)

    def run(self) -> None:
        try:
            if self.action == 'flash':
                result = run_project_flash(self.project, log_cb=lambda level, msg: self.log.emit(level, msg), progress_cb=lambda pct, _done, _total: self.progress.emit(int(pct)))
            elif self.action == 'detect':
                result = run_project_detect(self.project, log_cb=lambda level, msg: self.log.emit(level, msg))
            elif self.action == 'smart_detect':
                result = run_project_smart_detect(self.project, log_cb=lambda level, msg: self.log.emit(level, msg))
            elif self.action == 'read_config':
                result = run_project_read_config(self.project, log_cb=lambda level, msg: self.log.emit(level, msg))
            elif self.action == 'write_config':
                result = run_project_write_config(self.project, log_cb=lambda level, msg: self.log.emit(level, msg))
            elif self.action == 'erase_only':
                result = run_project_erase_only(self.project, log_cb=lambda level, msg: self.log.emit(level, msg), progress_cb=lambda pct, _done, _total: self.progress.emit(int(pct)))
            elif self.action == 'verify_only':
                result = run_project_verify_only(self.project, log_cb=lambda level, msg: self.log.emit(level, msg), progress_cb=lambda pct, _done, _total: self.progress.emit(int(pct)))
            else:
                raise RuntimeError(f'unknown action: {self.action}')
            self.done.emit(self.action, result)
        except Exception as exc:
            self.failed.emit(self.action, str(exc))
        finally:
            self.finished.emit()


class ProjectManagerDialog(QDialog):
    def __init__(self, owner: 'MainWindow'):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('Project manager')
        self.resize(760, 460)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel('Project manager', self)
        title.setProperty('role', 'section')
        head.addWidget(title)
        head.addStretch(1)
        root.addLayout(head)

        info = QLabel('Open, save or remove project files. Keep one project per board or firmware setup.', self)
        info.setProperty('role', 'muted')
        info.setWordWrap(True)
        root.addWidget(info)

        grid = QGridLayout()
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        root.addLayout(grid, 1)

        self.project_list = QListWidget(self)
        self.project_list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        grid.addWidget(self.project_list, 0, 0, 4, 1)

        right = QFrame(self)
        right.setProperty('card', True)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)
        grid.addWidget(right, 0, 1, 4, 1)

        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        form.addRow('Project name', self.name_edit)
        form.addRow('File', self.path_edit)
        right_layout.addLayout(form)

        self.hint = QLabel('', self)
        self.hint.setProperty('role', 'muted')
        self.hint.setWordWrap(True)
        right_layout.addWidget(self.hint)
        right_layout.addStretch(1)

        btn_row1 = QHBoxLayout()
        self.new_btn = QPushButton('New', self)
        self.open_btn = QPushButton('Open selected', self)
        self.save_btn = QPushButton('Save current', self)
        self.save_as_btn = QPushButton('Save as', self)
        btn_row1.addWidget(self.new_btn)
        btn_row1.addWidget(self.open_btn)
        btn_row1.addWidget(self.save_btn)
        btn_row1.addWidget(self.save_as_btn)
        right_layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.delete_btn = QPushButton('Delete selected', self)
        self.delete_btn.setProperty('ghost', True)
        self.close_btn = QPushButton('Close', self)
        self.close_btn.setProperty('accent', True)
        btn_row2.addWidget(self.delete_btn)
        btn_row2.addStretch(1)
        btn_row2.addWidget(self.close_btn)
        right_layout.addLayout(btn_row2)

        self.new_btn.clicked.connect(self._new_project)
        self.open_btn.clicked.connect(self._open_selected)
        self.save_btn.clicked.connect(self._save_current)
        self.save_as_btn.clicked.connect(self._save_as)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.close_btn.clicked.connect(self.accept)
        self.project_list.currentItemChanged.connect(self._on_item_changed)

        self._refresh_list()
        self._load_current()

    def _projects_dir(self) -> Path:
        return default_state_path().parent / 'projects'

    def _scan_projects(self) -> list[Path]:
        roots = []
        current = Path.cwd()
        roots.append(current)
        roots.append(self._projects_dir())
        if self.owner.session.last_project_path:
            roots.append(Path(self.owner._normalize_project_path(self.owner.session.last_project_path)).expanduser().resolve().parent)
        seen = set()
        out: list[Path] = []
        for root in roots:
            try:
                root = root.resolve()
            except Exception:
                continue
            if root in seen or not root.exists():
                continue
            seen.add(root)
            out.extend(sorted(root.glob('*.chisp')))
        uniq = {}
        for p in out:
            try:
                uniq[p.resolve()] = p.resolve()
            except Exception:
                continue
        return sorted(uniq.values(), key=lambda p: (p.name.lower(), str(p)))

    def _refresh_list(self) -> None:
        self.project_list.clear()
        for path in self._scan_projects():
            path_str = str(path)
            item = QListWidgetItem(self.owner._project_display_name(path_str))
            item.setData(Qt.UserRole, path_str)
            detail = self.owner._project_dirty_tooltip(path_str)
            item.setToolTip(f'{path_str}\n{detail}' if detail else path_str)
            self.project_list.addItem(item)
        if self.project_list.count() == 0:
            self.path_edit.clear()
        self.hint.setText(f'Project store: {self._projects_dir()}')

    def _clean_name_value(self, name: str) -> str:
        return re.sub(r'\s+\*+$', '', str(name or '').strip())

    def _load_current(self) -> None:
        self.name_edit.setText(self._clean_name_value(self.owner.session.project.name))
        self.path_edit.setText(self.owner.session.last_project_path or '')
        self.project_list.clearSelection()
        current_path = self.owner._normalize_project_path(self.owner.session.last_project_path)
        if current_path:
            for i in range(self.project_list.count()):
                item = self.project_list.item(i)
                if str(item.data(Qt.UserRole)) == current_path:
                    self.project_list.setCurrentItem(item)
                    break

    def _on_item_changed(self, current, _previous) -> None:
        if current is None:
            return
        path = str(current.data(Qt.UserRole) or '')
        self.path_edit.setText(path)
        name = ''
        try:
            loaded = load_project(path)
            name = str(loaded.name or '').strip()
        except Exception:
            name = ''
        if not name and path:
            name = Path(path).stem
        self.name_edit.setText(self._clean_name_value(name))

    def _new_project(self) -> None:
        self.owner._stage_current_project_draft()
        self.owner._drop_project_tracking('')
        self.owner.session.project = CHISPProject()
        self.owner.session.last_project_path = ''
        self.owner._load_project_into_ui()
        self.owner._remember_current_project_clean()
        self.owner._update_project_header()
        self._load_current()
        self._refresh_list()

    def _open_selected(self) -> None:
        item = self.project_list.currentItem()
        if item is None:
            return
        path = Path(str(item.data(Qt.UserRole)))
        try:
            self.owner._load_project_reference(str(path), discard_draft=True)
            self.owner._append_log('INFO', f'project loaded: {path}')
            self._load_current()
            self._refresh_list()
        except Exception as exc:
            QMessageBox.critical(self, 'Open project', str(exc))

    def _save_current(self) -> None:
        self.owner._collect_project_from_ui(silent=True)
        self.owner.session.project.name = self._clean_name_value(self.name_edit.text())
        path = (self.owner.session.last_project_path or '').strip()
        if not path:
            self._save_as()
            return
        try:
            save_project(path, self.owner.session.project)
            self.owner._mark_current_project_saved(path)
            self.owner._save_app_state()
            self.owner._append_log('INFO', f'project saved: {path}')
            self._refresh_list()
            self._load_current()
        except Exception as exc:
            QMessageBox.critical(self, 'Save project', str(exc))

    def _sanitize_name(self, name: str) -> str:
        out = re.sub(r'[^A-Za-z0-9._-]+', '_', name.strip())
        return out.strip('._-') or 'project'

    def _save_as(self) -> None:
        self.owner._collect_project_from_ui(silent=True)
        self.owner.session.project.name = self._clean_name_value(self.name_edit.text())
        start_dir = self._projects_dir()
        start_dir.mkdir(parents=True, exist_ok=True)
        default_name = self._sanitize_name(self.owner.session.project.name or self.owner.session.project.chip)
        dialog = _create_standard_file_dialog(self, 'Save CHISP project')
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        dialog.setFileMode(QFileDialog.AnyFile)
        dialog.setNameFilters(['CHISP Project (*.chisp)', 'All files (*.*)'])
        dialog.selectNameFilter('CHISP Project (*.chisp)')
        dialog.setDirectory(str(start_dir))
        dialog.selectFile(str(start_dir / f'{default_name}.chisp'))
        if dialog.exec() != QDialog.Accepted:
            return
        files = dialog.selectedFiles()
        path = files[0] if files else ''
        if not path:
            return
        if not path.endswith('.chisp'):
            path += '.chisp'
        try:
            previous_key = self.owner._current_project_key()
            save_project(path, self.owner.session.project)
            self.owner._mark_current_project_saved(path, previous_key=previous_key)
            self.owner._save_app_state()
            self.owner._append_log('INFO', f'project saved: {path}')
            self._refresh_list()
            self._load_current()
        except Exception as exc:
            QMessageBox.critical(self, 'Save project', str(exc))

    def _delete_selected(self) -> None:
        item = self.project_list.currentItem()
        if item is None:
            return
        path = Path(str(item.data(Qt.UserRole)))
        answer = QMessageBox.question(self, 'Delete project', f'Delete project file?\n\n{path}')
        if answer != QMessageBox.Yes:
            return
        try:
            path_str = str(path)
            path.unlink(missing_ok=True)
            self.owner._drop_project_tracking(path_str)
            if self.owner.session.last_project_path == path_str:
                self.owner.session.last_project_path = ''
                self.owner.app_state.last_project_path = ''
                self.owner._save_app_state()
                self.owner._refresh_project_select()
                self.owner._update_project_header()
            self.owner._append_log('INFO', f'project deleted: {path}')
            self._refresh_list()
            self._load_current()
        except Exception as exc:
            QMessageBox.critical(self, 'Delete project', str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('CHISP Flasher')
        self.resize(1320, 900)
        self.session = Session()
        self.app_state = load_app_state()
        self.session.last_project_path = self.app_state.last_project_path
        self.chipdb = load_chipdb()
        self.resolver = ChipResolver(self.chipdb)
        self.transport_controls: dict[str, QWidget] = {}
        self.config_controls: dict[str, QWidget] = {}
        self.config_rows: dict[str, QWidget] = {}
        self.config_section_boxes: dict[str, QGroupBox] = {}
        self.config_section_notes: dict[str, QLabel] = {}
        self._worker_thread: QThread | None = None
        self._worker: ActionWorker | None = None
        self._candidate_cache: dict = {}
        self._last_config_result: dict = {}
        self._project_loaded: dict[str, CHISPProject] = {}
        self._project_drafts: dict[str, CHISPProject] = {}
        self._building = False
        self._build_ui()
        self._bind_edit_tracking()
        self._try_load_last_project()
        self._populate_series()
        self._load_project_into_ui()
        self._maybe_auto_apply_best_connection(force=False, log=False)
        self._remember_current_project_clean()
        self._update_project_header()
        self._append_log('INFO', 'UI ready')
        self.setUnifiedTitleAndToolBarOnMac(False)

    def closeEvent(self, event):
        if self._worker_thread is not None and self._worker_thread.isRunning():
            QMessageBox.warning(self, 'Busy', 'An operation is still running.')
            event.ignore()
            return
        self._collect_project_from_ui(silent=True)
        self.app_state.last_project_path = self.session.last_project_path
        self._save_app_state()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setProperty('appRoot', True)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        splitter = QSplitter(Qt.Horizontal, self)
        root.addWidget(splitter)

        left_scroll = QScrollArea(self)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(392)
        left = QWidget(self)
        left.setMinimumWidth(378)
        self.left_layout = QVBoxLayout(left)
        self.left_layout.setContentsMargins(0, 0, 12, 0)
        self.left_layout.setSpacing(10)
        self.left_layout.addWidget(self._build_project_card())
        self.left_layout.addWidget(self._build_device_card())
        self.left_layout.addWidget(self._build_connection_card())
        self.left_layout.addWidget(self._build_run_card())
        self.left_layout.addStretch(1)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._build_header())
        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet('QTabWidget::tab-bar { left: 12px; }')
        self.tabs.addTab(self._build_logs_page(), 'Log')
        self.tabs.addTab(self._build_config_page(), 'Config')
        right_layout.addWidget(self.tabs, 1)
        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([404, 916])

        status = QStatusBar(self)
        self.setStatusBar(status)
        self.status_label = QLabel('Ready', self)
        status.addWidget(self.status_label)
        self.progress = QProgressBar(self)
        self.progress.setFixedWidth(220)
        status.addPermanentWidget(self.progress)

    def _build_header(self) -> QWidget:
        card = self._card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)

        title_box = QVBoxLayout()

        self.header_title = QLabel('CHISP Flasher', self)
        self.header_title.setProperty('role', 'title')

        self.header_subtitle = QLabel('Cross-platform ISP flasher for WCH CH32 and CH5x/CH6x families', self)
        self.header_subtitle.setProperty('role', 'subtitle')

        self.header_meta = QLabel(
            f'Version {__version__} - Author: Paweł Jarczak - '
            '<a href="https://github.com/jarczakpawel/CHISP-Flasher">GitHub</a>',
            self
        )
        self.header_meta.setProperty('role', 'muted')
        self.header_meta.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.header_meta.setOpenExternalLinks(True)

        title_box.addWidget(self.header_title)
        title_box.addWidget(self.header_subtitle)
        title_box.addWidget(self.header_meta)

        layout.addLayout(title_box, 1)

        self.mode_pill = QLabel('Serial bootloader', self)
        self.mode_pill.setProperty('role', 'pill')

        self.device_pill = QLabel('CH32V203', self)
        self.device_pill.setProperty('role', 'pill')

        layout.addWidget(self.mode_pill)
        layout.addWidget(self.device_pill)
        return card

    def _build_project_card(self) -> QWidget:
        box = QGroupBox('Project', self)
        outer = QVBoxLayout(box)
        outer.setSpacing(10)

        row = QHBoxLayout()
        self.project_select = NoWheelComboBox(self)
        self.project_select.currentIndexChanged.connect(self._on_project_combo_changed)
        self.manage_project_btn = QPushButton('Manage', self)
        self.manage_project_btn.clicked.connect(self._show_project_manager)
        self.options_btn = QPushButton('Options', self)
        self.options_btn.setProperty('ghost', True)
        self.options_btn.clicked.connect(self._show_project_options)
        row.addWidget(self.project_select, 1)
        row.addWidget(self.manage_project_btn)
        row.addWidget(self.options_btn)
        outer.addLayout(row)

        self.firmware_field = FileField('Firmware file', self)
        outer.addWidget(self._row('Firmware', self.firmware_field, label_min_width=60))

        self.project_options_summary = QLabel('', self)
        self.project_options_summary.setProperty('role', 'muted')
        self.project_options_summary.setWordWrap(True)
        outer.addWidget(self.project_options_summary)
        return box

    def _build_device_card(self) -> QWidget:
        box = QGroupBox('Device', self)
        form = QFormLayout(box)
        self.series_box = NoWheelComboBox(self)
        self.series_box.addItems(self._series_names())
        self.series_box.currentTextChanged.connect(self._on_series_changed)
        self.chip_box = NoWheelComboBox(self)
        self.chip_box.currentTextChanged.connect(self._on_chip_changed)
        self.connection_summary = QLabel('-', self)
        self.connection_summary.setWordWrap(True)
        self.connection_summary.setProperty('role', 'muted')
        form.addRow('Series', self.series_box)
        form.addRow('Chip', self.chip_box)
        form.addRow('Profile', self.connection_summary)
        return box

    def _build_connection_card(self) -> QWidget:
        box = QGroupBox('Connection', self)
        outer = QVBoxLayout(box)
        outer.setSpacing(10)

        top = QHBoxLayout()
        self.connection_mode_title = QLabel('', self)
        self.connection_mode_title.setProperty('role', 'section')
        top.addWidget(self.connection_mode_title, 1)
        self.manual_btn = QPushButton('Manual', self)
        self.manual_btn.setProperty('ghost', True)
        self.manual_btn.clicked.connect(self._show_manual)
        top.addWidget(self.manual_btn)
        outer.addLayout(top)

        form = QFormLayout()
        self.transport_box = NoWheelComboBox(self)
        self.transport_box.currentTextChanged.connect(self._on_transport_changed)
        self.transport_controls['serial_port'] = self._combo([])
        self.transport_controls['usb_device'] = self._combo([])
        self.transport_controls['usb_device'].currentTextChanged.connect(self._on_usb_device_changed)
        self.transport_controls['fast_baud'] = self._combo(['115200', '230400', '460800', '500000', '921600', '1000000', '2000000'])
        self.transport_controls['serial_auto_di'] = QCheckBox('Auto DI', self)
        self.transport_controls['serial_auto_di'].toggled.connect(self._on_serial_auto_di_toggled)
        self.transport_controls['usb_interface_number'] = QLineEdit(self)
        self.transport_controls['usb_endpoint_out'] = QLineEdit(self)
        self.transport_controls['usb_endpoint_in'] = QLineEdit(self)
        form.addRow('Type', self.transport_box)
        for key in ['serial_port', 'usb_device', 'fast_baud', 'serial_auto_di', 'usb_interface_number', 'usb_endpoint_out', 'usb_endpoint_in']:
            row = self._row(LABELS[key], self.transport_controls[key])
            self.transport_controls[f'row_{key}'] = row
            form.addRow(row)
        outer.addLayout(form)

        buttons = QHBoxLayout()
        self.refresh_ports_btn = QPushButton('Refresh', self)
        self.refresh_ports_btn.clicked.connect(lambda: self._refresh_connection_candidates(initial=False))
        self.use_suggested_btn = QPushButton('Use suggested', self)
        self.use_suggested_btn.setProperty('ghost', True)
        self.use_suggested_btn.clicked.connect(self._apply_suggested_connection)
        buttons.addWidget(self.refresh_ports_btn, 1)
        buttons.addWidget(self.use_suggested_btn)
        outer.addLayout(buttons)

        self.connection_suggestion = QLabel('', self)
        self.connection_suggestion.setProperty('role', 'muted')
        self.connection_suggestion.setWordWrap(True)
        outer.addWidget(self.connection_suggestion)
        return box

    def _build_run_card(self) -> QWidget:
        box = QGroupBox('Run', self)
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        self.detect_button = QPushButton('Detect', self)
        self.detect_button.clicked.connect(self._detect_target)
        self.read_cfg_button = QPushButton('Read config', self)
        self.read_cfg_button.clicked.connect(self._read_config)
        self.flash_button = QPushButton('Flash', self)
        self.flash_button.setProperty('accent', True)
        self.flash_button.clicked.connect(self._flash)
        row1.addWidget(self.detect_button)
        row1.addWidget(self.read_cfg_button)
        row1.addWidget(self.flash_button, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.more_actions_btn = QToolButton(self)
        self.more_actions_btn.setText('More')
        self.more_actions_btn.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.more_actions_btn)
        self.action_smart_detect = QAction('Smart detect', self)
        self.action_smart_detect.triggered.connect(self._smart_detect_target)
        self.action_write_config = QAction('Apply config', self)
        self.action_write_config.triggered.connect(self._write_config)
        self.action_erase_only = QAction('Erase only', self)
        self.action_erase_only.triggered.connect(self._erase_only)
        self.action_verify_only = QAction('Verify only', self)
        self.action_verify_only.triggered.connect(self._verify_only)
        for action in [self.action_smart_detect, self.action_write_config, self.action_erase_only, self.action_verify_only]:
            more_menu.addAction(action)
        self.more_actions_btn.setMenu(more_menu)
        row2.addWidget(self.more_actions_btn)
        row2.addStretch(1)
        layout.addLayout(row2)

        hint = QLabel('Main flow: Detect, then Read config, then Flash.', self)
        hint.setProperty('role', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box


    def _build_config_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        head = self._card()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(18, 18, 18, 18)
        left = QVBoxLayout()
        cfg_title = QLabel('Configuration', self)
        cfg_title.setProperty('role', 'pill')
        self.config_hint = QLabel('', self)
        self.config_hint.setProperty('role', 'muted')
        self.config_hint.setWordWrap(True)
        self.config_profile_pill = QLabel('', self)
        self.config_profile_pill.setProperty('role', 'pill')
        left.addWidget(cfg_title)
        left.addWidget(self.config_hint)
        left.addWidget(self.config_profile_pill)
        head_layout.addLayout(left, 1)
        self.config_read_button = QPushButton('Read config', self)
        self.config_read_button.clicked.connect(self._read_config)
        self.config_apply_button = QPushButton('Apply config', self)
        self.config_apply_button.setProperty('accent', True)
        self.config_apply_button.clicked.connect(self._write_config)
        head_layout.addWidget(self.config_read_button)
        head_layout.addWidget(self.config_apply_button)
        layout.addWidget(head)

        for section_id in SECTION_ORDER:
            meta = SECTION_META[section_id]
            box = QGroupBox(meta['title'], self)
            form = QFormLayout(box)
            note = QLabel(meta['description'], self)
            note.setProperty('role', 'muted')
            note.setWordWrap(True)
            form.addRow(note)
            self.config_section_notes[section_id] = note
            for key in meta['fields']:
                if key not in self.config_controls:
                    widget = self._build_config_control(key)
                    row = self._row(LABELS[key], widget)
                    self.config_rows[key] = row
                form.addRow(self.config_rows[key])
            self.config_section_boxes[section_id] = box
            layout.addWidget(box)
        layout.addStretch(1)
        return page

    def _build_logs_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = self._card()
        head_layout = QGridLayout(head)
        head_layout.setContentsMargins(18, 18, 18, 18)
        self.flash_target = QLabel('-', self)
        self.flash_target.setProperty('role', 'title')
        self.flash_details = QLabel('-', self)
        self.flash_details.setProperty('role', 'muted')
        self.flash_details.setWordWrap(True)
        self.firmware_meta = QLabel('-', self)
        self.firmware_meta.setProperty('role', 'pill')
        self.transport_meta = QLabel('-', self)
        self.transport_meta.setProperty('role', 'pill')
        head_layout.addWidget(self.flash_target, 0, 0, 1, 2)
        head_layout.addWidget(self.flash_details, 1, 0, 1, 2)
        head_layout.addWidget(self.firmware_meta, 2, 0)
        head_layout.addWidget(self.transport_meta, 2, 1)
        layout.addWidget(head)
        layout.addSpacing(6)

        top_row = QWidget(self)
        top_row.setFixedHeight(34)
        top_row_layout = QHBoxLayout(top_row)
        top_row_layout.setContentsMargins(12, 0, 12, 0)
        top_row_layout.setSpacing(4)

        title_tab = QPushButton('Activity log', self)
        title_tab.setProperty('tabLike', True)
        title_tab.setProperty('tabActive', True)
        title_tab.setProperty('tabStatic', True)
        title_tab.setFocusPolicy(Qt.NoFocus)
        title_tab.setCursor(Qt.ArrowCursor)
        title_tab.setFixedHeight(34)
        top_row_layout.addWidget(title_tab)

        top_row_layout.addStretch(1)

        copy_btn = QPushButton('Copy all', self)
        copy_btn.setProperty('tabLike', True)
        copy_btn.setFocusPolicy(Qt.NoFocus)
        copy_btn.setFixedHeight(34)
        copy_btn.clicked.connect(self._copy_log)

        clear_btn = QPushButton('Clear', self)
        clear_btn.setProperty('tabLike', True)
        clear_btn.setFocusPolicy(Qt.NoFocus)
        clear_btn.setFixedHeight(34)
        clear_btn.clicked.connect(self._clear_log)

        top_row_layout.addWidget(copy_btn)
        top_row_layout.addWidget(clear_btn)
        layout.addWidget(top_row)

        box = QFrame(self)
        box.setProperty('logPane', True)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 12, 12, 12)
        box_layout.setSpacing(0)

        self.log_panel = LogPanel(self)
        self.log_panel.setProperty('embedded', True)
        box_layout.addWidget(self.log_panel, 1)
        layout.addWidget(box, 1)
        return page

    def _build_config_control(self, key: str) -> QWidget:
        if key.startswith('wrp') or key in {'data0', 'data1'}:
            widget = QLineEdit(self)
        elif key == 'ramx_rom_mode':
            widget = NoWheelComboBox(self)
        else:
            widget = QCheckBox(self)
        self.config_controls[key] = widget
        return widget

    def _card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty('card', True)
        return card

    def _row(self, label: str, widget: QWidget, *, label_min_width: int = 118) -> QWidget:
        row = QWidget(self)
        row.setProperty('flatRow', True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        lbl = QLabel(label, row)
        lbl.setMinimumWidth(label_min_width)
        lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(lbl, 0, Qt.AlignVCenter)
        if isinstance(widget, QCheckBox):
            widget.setProperty('flat', True)
        layout.addWidget(widget, 1, Qt.AlignVCenter)
        return row

    def _combo(self, values: list[str]) -> QComboBox:
        box = NoWheelComboBox(self)
        box.setEditable(True)
        box.setInsertPolicy(QComboBox.NoInsert)
        line = box.lineEdit()
        if line is not None:
            line.setFrame(False)
            line.setStyleSheet('background: #fcfaf7; border: none; color: #302923; padding: 0;')
        for value in values:
            if value:
                box.addItem(value, value)
        return box

    def _combo_value(self, widget: QComboBox) -> str:
        idx = widget.currentIndex()
        if idx >= 0 and widget.currentText() == widget.itemText(idx):
            data = widget.itemData(idx, Qt.UserRole)
            if data is not None and str(data).strip():
                return str(data).strip()
        return widget.currentText().strip()

    def _set_combo_value(self, widget: QComboBox, value: str) -> None:
        target = str(value or '').strip()
        for i in range(widget.count()):
            item_value = str(widget.itemData(i, Qt.UserRole) or widget.itemText(i) or '').strip()
            if item_value == target:
                widget.setCurrentIndex(i)
                return
        widget.setEditText(target)

    def _set_combo_values(self, widget: QComboBox, values) -> None:
        current = self._combo_value(widget)
        widget.blockSignals(True)
        widget.clear()
        seen = set()
        for value in values:
            if isinstance(value, dict):
                raw = str(value.get('selector') or '').strip()
                label = str(value.get('display') or raw).strip()
            else:
                raw = str(value).strip()
                label = raw
            if not raw or raw in seen:
                continue
            seen.add(raw)
            widget.addItem(label, raw)
        if current:
            if current not in seen:
                widget.addItem(current, current)
            self._set_combo_value(widget, current)
        widget.blockSignals(False)


    def _series_names(self) -> list[str]:
        out: list[str] = []
        for name in self.chipdb.chips.keys():
            s = str(name).strip().upper()
            for prefix in ('CH32V', 'CH32F', 'CH32X', 'CH32L', 'CH32M', 'CH54', 'CH55', 'CH56', 'CH57', 'CH58', 'CH59'):
                if s.startswith(prefix) and prefix not in out:
                    out.append(prefix)
                    break
        return out or ['CH32V', 'CH32F']

    def _chip_transport_choices(self, chip_name: str) -> list[str]:
        chip = self.chipdb.chips.get((chip_name or '').strip(), {})
        values = [str(x).strip() for x in (chip.get('transport_support') or []) if str(x).strip()]
        out: list[str] = []
        for value in values:
            if value not in out:
                out.append(value)
        return out or ['serial', 'usb']

    def _transport_choice_label(self, chip_name: str, value: str) -> str:
        kind = (value or '').strip()
        if kind == 'usb':
            return 'Native USB'
        return 'Serial / USB-UART'

    def _serial_auto_di_supported(self, chip_name: str) -> bool:
        return bool(self.resolver.transport_mode_meta((chip_name or '').strip(), 'serial_auto_di'))

    def _effective_connection_mode(self, resolved) -> str:
        if getattr(resolved, 'protocol_variant', '') != 'usb_native_plain':
            auto_di = bool(self.session.project.transport.serial_auto_di) and self._serial_auto_di_supported(self.session.project.chip)
            return 'USB-UART Auto DI' if auto_di else 'Serial bootloader'
        return resolved.display_connection_mode

    def _apply_transport_choices(self, chip_name: str, preferred: str | None = None) -> None:
        choices = self._chip_transport_choices(chip_name)
        current = (preferred or self._combo_value(self.transport_box) or '').strip()
        if current not in choices:
            current = choices[0]
        self.transport_box.blockSignals(True)
        self.transport_box.clear()
        for value in choices:
            self.transport_box.addItem(self._transport_choice_label(chip_name, value), value)
        self._set_combo_value(self.transport_box, current)
        self.transport_box.blockSignals(False)

    def _save_app_state(self) -> None:
        save_app_state(self.app_state)

    @contextmanager
    def _blocked_widget_signals(self, widgets: list[QWidget]):
        states = []
        for widget in widgets:
            if widget is None:
                continue
            states.append((widget, widget.blockSignals(True)))
        try:
            yield
        finally:
            for widget, was_blocked in reversed(states):
                widget.blockSignals(was_blocked)

    def _ui_signal_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = [
            self.firmware_field.line_edit,
            self.series_box,
            self.chip_box,
            self.transport_box,
        ]
        widgets.extend(widget for widget in self.transport_controls.values() if isinstance(widget, QWidget))
        widgets.extend(widget for widget in self.config_controls.values() if isinstance(widget, QWidget))
        return widgets

    def _clone_project(self, project: CHISPProject) -> CHISPProject:
        return deepcopy(project)

    def _normalize_project_path(self, path: str | None) -> str:
        raw = (path or '').strip()
        if not raw:
            return ''
        try:
            return str(Path(raw).expanduser().resolve())
        except Exception:
            return raw

    def _current_project_key(self) -> str:
        return self._normalize_project_path(self.session.last_project_path)

    def _project_key(self, path: str | None) -> str:
        return self._normalize_project_path(path)

    def _drop_project_tracking(self, path: str) -> None:
        key = self._project_key(path)
        self._project_loaded.pop(key, None)
        self._project_drafts.pop(key, None)

    def _remember_current_project_clean(self, *, previous_key: str | None = None) -> None:
        key = self._current_project_key()
        clean = self._clone_project(self.session.project)
        self._project_loaded[key] = self._clone_project(clean)
        self._project_drafts[key] = clean
        if previous_key is not None and previous_key != key:
            self._drop_project_tracking(previous_key)

    def _stage_current_project_draft(self) -> None:
        self._collect_project_from_ui(silent=True)
        self._project_drafts[self._current_project_key()] = self._clone_project(self.session.project)

    def _stage_session_project_draft(self) -> None:
        self._project_drafts[self._current_project_key()] = self._clone_project(self.session.project)

    def _dirty_groups_for_key(self, path: str | None) -> dict[str, list[str]]:
        key = self._project_key(path)
        draft = self._project_drafts.get(key)
        clean = self._project_loaded.get(key)
        if draft is None or clean is None:
            return {}
        groups: dict[str, list[str]] = {}

        def add(group: str, label: str) -> None:
            groups.setdefault(group, []).append(label)

        if draft.name != clean.name:
            add('project', 'Project name')
        if draft.firmware_path != clean.firmware_path:
            add('firmware', 'Firmware')
        if draft.family != clean.family:
            add('device', 'Series')
        if draft.chip != clean.chip:
            add('device', 'Chip')

        transport_labels = {
            'kind': 'Type',
            'serial_port': LABELS['serial_port'],
            'usb_device': LABELS['usb_device'],
            'usb_interface_number': LABELS['usb_interface_number'],
            'usb_endpoint_out': LABELS['usb_endpoint_out'],
            'usb_endpoint_in': LABELS['usb_endpoint_in'],
            'serial_auto_di': LABELS['serial_auto_di'],
        }
        for field_name, label in transport_labels.items():
            if getattr(draft.transport, field_name) != getattr(clean.transport, field_name):
                add('connection', label)

        operation_labels = {
            'verify_after_flash': 'Verify after flash',
            'trace_mode': 'Trace log',
            'fast_baud': 'Fast baud',
            'no_fast': 'Disable fast mode',
        }
        for field_name, label in operation_labels.items():
            if getattr(draft.operations, field_name) != getattr(clean.operations, field_name):
                add('options', label)

        config_labels = {
            'enable_rrp': LABELS['enable_rrp'],
            'clear_codeflash': LABELS['clear_codeflash'],
            'disable_stop_mode_rst': LABELS['disable_stop_mode_rst'],
            'disable_standby_mode_rst': LABELS['disable_standby_mode_rst'],
            'enable_soft_ctrl_iwdg': LABELS['enable_soft_ctrl_iwdg'],
            'enable_long_delay_time': 'Long power-on delay',
            'ramx_rom_mode': LABELS['ramx_rom_mode'],
            'data0': LABELS['data0'],
            'data1': LABELS['data1'],
            'wrp0': LABELS['wrp0'],
            'wrp1': LABELS['wrp1'],
            'wrp2': LABELS['wrp2'],
            'wrp3': LABELS['wrp3'],
        }
        for field_name, label in config_labels.items():
            if getattr(draft.config, field_name) != getattr(clean.config, field_name):
                add('config', label)
        return groups

    def _project_dirty_summary(self, path: str | None = None) -> str:
        groups = self._dirty_groups_for_key(self._current_project_key() if path is None else path)
        if not groups:
            return ''
        return 'Unsaved: ' + ', '.join(groups.keys())

    def _project_dirty_tooltip(self, path: str | None = None) -> str:
        groups = self._dirty_groups_for_key(self._current_project_key() if path is None else path)
        if not groups:
            return ''
        lines = []
        for group, labels in groups.items():
            lines.append(f'{group}: {", ".join(labels)}')
        return 'Unsaved changes\n' + '\n'.join(lines)

    def _project_display_name(self, path: str) -> str:
        paths = self._project_choice_paths()
        label_map = self._project_label_map(paths)
        try:
            p = Path(path)
            label = label_map.get(path, p.stem)
        except Exception:
            label = path
        if self._dirty_groups_for_key(path):
            return f'{label} *'
        return label

    def _mark_current_project_saved(self, path: str, *, previous_key: str | None = None) -> None:
        normalized_path = self._normalize_project_path(path)
        self.session.last_project_path = normalized_path
        self.app_state.last_project_path = normalized_path
        self._remember_current_project_clean(previous_key=previous_key)
        self._update_project_header()

    def _load_project_reference(self, path: str, *, discard_draft: bool) -> None:
        normalized_path = self._normalize_project_path(path)
        key = self._project_key(normalized_path)
        if discard_draft:
            self._project_drafts.pop(key, None)
        draft = None if discard_draft else self._project_drafts.get(key)
        use_draft = draft is not None and bool(self._dirty_groups_for_key(normalized_path))
        if use_draft:
            project = self._clone_project(draft)
        else:
            project = load_project(normalized_path)
        self.session.project = project
        self.session.last_project_path = normalized_path
        self.app_state.last_project_path = normalized_path
        self._save_app_state()
        self._load_project_into_ui()
        if not use_draft:
            self._remember_current_project_clean()
        self._update_project_header()

    def _load_unsaved_project_reference(self) -> None:
        self.session.last_project_path = ''
        self.app_state.last_project_path = ''
        draft = self._project_drafts.get('')
        if draft is None:
            self.session.project = CHISPProject()
            self._save_app_state()
            self._load_project_into_ui()
            self._remember_current_project_clean()
            self._update_project_header()
            return
        self.session.project = self._clone_project(draft)
        self._save_app_state()
        self._load_project_into_ui()
        self._update_project_header()

    def _on_ui_edited(self, *_args) -> None:
        if self._building:
            return
        self._stage_current_project_draft()
        self._update_project_header()

    def _bind_edit_tracking(self) -> None:
        self.firmware_field.line_edit.textChanged.connect(self._on_ui_edited)
        self.series_box.currentTextChanged.connect(self._on_ui_edited)
        self.chip_box.currentTextChanged.connect(self._on_ui_edited)
        self.transport_box.currentTextChanged.connect(self._on_ui_edited)
        for widget in self.transport_controls.values():
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_ui_edited)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._on_ui_edited)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_ui_edited)
        for widget in self.config_controls.values():
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_ui_edited)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._on_ui_edited)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_ui_edited)

    def _try_load_last_project(self) -> None:
        path = self._normalize_project_path(self.session.last_project_path)
        if not path:
            return
        try:
            self.session.project = load_project(path)
            self.session.last_project_path = path
            self.app_state.last_project_path = path
        except Exception:
            self.session.last_project_path = ''
            self.app_state.last_project_path = ''
            self._save_app_state()

    def _project_store_dir(self) -> Path:
        return default_state_path().parent / 'projects'

    def _project_choice_paths(self) -> list[Path]:
        seen = set()
        paths: list[Path] = []
        current = self._normalize_project_path(self.session.last_project_path)
        if current:
            try:
                resolved = Path(current).expanduser().resolve()
                if resolved.exists():
                    seen.add(str(resolved))
                    paths.append(resolved)
            except Exception:
                pass
        store = self._project_store_dir()
        if store.exists():
            for path in sorted(store.glob('*.chisp')):
                try:
                    resolved = path.resolve()
                except Exception:
                    continue
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                paths.append(resolved)
        return paths

    def _project_label_map(self, paths: list[Path]) -> dict[str, str]:
        counts = {}
        for path in paths:
            stem = path.stem
            counts[stem] = counts.get(stem, 0) + 1
        out = {}
        for path in paths:
            stem = path.stem
            if counts.get(stem, 0) > 1:
                out[str(path)] = f"{stem} - {path.parent.name}"
            else:
                out[str(path)] = stem
        return out

    def _refresh_project_select(self) -> None:
        current_path = self._normalize_project_path(self.session.last_project_path)
        current_name = (self.session.project.name or '').strip()
        paths = self._project_choice_paths()
        if current_path and not any(str(p) == current_path for p in paths):
            self.session.last_project_path = ''
            self.app_state.last_project_path = ''
            current_path = ''
        self.project_select.blockSignals(True)
        self.project_select.clear()
        unsaved_label = 'Unsaved project'
        if current_name and not current_path:
            unsaved_label = f'Unsaved project - {current_name}'
        if self._dirty_groups_for_key(''):
            unsaved_label += ' *'
        self.project_select.addItem(unsaved_label, '')
        unsaved_tip = self._project_dirty_tooltip('')
        if unsaved_tip:
            self.project_select.setItemData(0, unsaved_tip, Qt.ToolTipRole)
        for path in paths:
            path_str = str(path)
            row = self.project_select.count()
            self.project_select.addItem(self._project_display_name(path_str), path_str)
            tip = self._project_dirty_tooltip(path_str)
            if tip:
                self.project_select.setItemData(row, tip, Qt.ToolTipRole)
        index = 0
        if current_path:
            for i in range(self.project_select.count()):
                if str(self.project_select.itemData(i) or '') == current_path:
                    index = i
                    break
        self.project_select.setCurrentIndex(index)
        self.project_select.blockSignals(False)

    def _on_project_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        current_path = self._normalize_project_path(self.session.last_project_path)
        path = self._normalize_project_path(str(self.project_select.itemData(index) or '').strip())
        if path == current_path:
            return
        try:
            self._stage_current_project_draft()
            if not path:
                self._load_unsaved_project_reference()
                self._append_log('INFO', 'project loaded: unsaved project')
                return
            self._load_project_reference(path, discard_draft=False)
            self._append_log('INFO', f'project loaded: {path}')
        except Exception as exc:
            QMessageBox.critical(self, 'Open project', str(exc))
            self._refresh_project_select()

    def _update_project_header(self) -> None:
        self._refresh_project_select()
        opts = []
        opts.append('Verify on' if self.session.project.operations.verify_after_flash else 'Verify off')
        dirty = self._project_dirty_summary()
        if dirty:
            opts.append(dirty)
        self.project_options_summary.setText(' | '.join(opts))


    def _show_project_manager(self) -> None:
        dlg = ProjectManagerDialog(self)
        dlg.exec()
        self._update_project_header()
        self._refresh_project_select()

    def _show_project_options(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle('Project options')
        dlg.resize(420, 160)
        root = QVBoxLayout(dlg)
        form = QFormLayout()
        verify = QCheckBox(self)
        verify.setChecked(bool(self.session.project.operations.verify_after_flash))
        form.addRow('Verify after flash', verify)
        root.addLayout(form)
        buttons = QHBoxLayout()
        cancel = QPushButton('Cancel', self)
        save = QPushButton('Save', self)
        save.setProperty('accent', True)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)
        cancel.clicked.connect(dlg.reject)
        def apply() -> None:
            self.session.project.operations.verify_after_flash = verify.isChecked()
            self._stage_session_project_draft()
            self._update_project_header()
            dlg.accept()
        save.clicked.connect(apply)
        dlg.exec()

    def _show_manual(self) -> None:
        self._collect_project_from_ui(silent=True)
        resolved = self.resolver.resolve(self.session.project.chip, transport=self.session.project.transport.kind)
        guide = get_guide(self._effective_connection_mode(resolved))
        dlg = QDialog(self)
        dlg.setWindowTitle('Connection manual')
        dlg.resize(560, 420)
        root = QVBoxLayout(dlg)
        title = QLabel(guide['title'], self)
        title.setProperty('role', 'section')
        summary = QLabel(guide['summary'], self)
        summary.setWordWrap(True)
        summary.setProperty('role', 'muted')
        steps = QTextEdit(self)
        steps.setReadOnly(True)
        step_lines = []
        for i, text in enumerate(guide.get('steps') or [], start=1):
            step_lines.append(f'{i}. {text}')
        details = guide.get('details') or ''
        if details:
            step_lines += ['', details]
        steps.setPlainText('\n'.join(step_lines))
        close_btn = QPushButton('Close', self)
        close_btn.setProperty('accent', True)
        close_btn.clicked.connect(dlg.accept)
        root.addWidget(title)
        root.addWidget(summary)
        root.addWidget(steps, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        root.addLayout(row)
        dlg.exec()

    def _rebuild_chip_choices(self, series: str, *, preferred_chip: str = '') -> None:
        chips = self.resolver.chips_for_series(series)
        self.chip_box.blockSignals(True)
        self.chip_box.clear()
        self.chip_box.addItems(chips)
        if chips:
            current = preferred_chip or self.session.project.chip
            if current in chips:
                self.chip_box.setCurrentText(current)
            else:
                self.chip_box.setCurrentIndex(0)
        self.chip_box.blockSignals(False)

    def _populate_series(self) -> None:
        family = (self.session.project.family or '').strip()
        if family:
            self.series_box.blockSignals(True)
            self.series_box.setCurrentText(family)
            self.series_box.blockSignals(False)
        self._rebuild_chip_choices(self.series_box.currentText(), preferred_chip=self.session.project.chip)
        self._apply_transport_choices(self.chip_box.currentText(), preferred=self.session.project.transport.kind)

    def _on_series_changed(self, series: str) -> None:
        if self._building:
            return
        self._rebuild_chip_choices(series, preferred_chip=self.session.project.chip)
        self._apply_transport_choices(self.chip_box.currentText(), preferred=self.session.project.transport.kind)
        self._refresh_connection_candidates(initial=False)
        self._maybe_auto_apply_best_connection(force=True, log=False)
        self._sync_from_project()

    def _on_chip_changed(self, _chip: str) -> None:
        if self._building:
            return
        self._apply_transport_choices(self.chip_box.currentText(), preferred=self._combo_value(self.transport_box))
        self._refresh_connection_candidates(initial=False)
        self._maybe_auto_apply_best_connection(force=True, log=False)
        self._sync_from_project()

    def _on_transport_changed(self, _kind: str) -> None:
        if self._building:
            return
        self._refresh_connection_candidates(initial=False)
        self._maybe_auto_apply_best_connection(force=True, log=False)
        self._sync_from_project()

    def _on_serial_auto_di_toggled(self, _checked: bool) -> None:
        if self._building:
            return
        self._refresh_connection_candidates(initial=False)
        self._maybe_auto_apply_best_connection(force=True, log=False)
        self._sync_from_project()

    def _refresh_connection_candidates(self, *, initial: bool) -> None:
        if self._building:
            return
        self._collect_project_from_ui(silent=True)
        candidates = enumerate_connection_candidates(self.session.project)
        self._candidate_cache = candidates
        self._set_combo_values(self.transport_controls['serial_port'], candidates.get('serial_port_entries') or [])
        self._set_combo_values(self.transport_controls['usb_device'], candidates.get('usb_device_entries') or [])
        self._sync_usb_fields_from_selector(self._combo_value(self.transport_controls['usb_device']), overwrite=False)
        self._update_connection_suggestion(candidates)
        if not initial:
            suggestion = candidates.get('suggestion') or {}
            if suggestion.get('label'):
                self._append_log('INFO', str(suggestion.get('label')))

    def _update_connection_suggestion(self, candidates: dict) -> None:
        suggestion = candidates.get('suggestion') or {}
        label = str(suggestion.get('label') or '').strip()
        detail = str(suggestion.get('details') or '').strip()
        if label and detail:
            self.connection_suggestion.setText(f'{label}. {detail}')
        elif label:
            self.connection_suggestion.setText(label)
        else:
            self.connection_suggestion.setText('No connection suggestion available yet.')

    def _find_usb_candidate_entry(self, selector: str) -> dict | None:
        target = str(selector or '').strip().lower()
        if not target:
            return None
        for item in list((self._candidate_cache or {}).get('usb_device_entries') or []):
            if str(item.get('selector') or '').strip().lower() == target:
                return item
        return None

    def _sync_usb_fields_from_selector(self, selector: str, *, overwrite: bool) -> None:
        item = self._find_usb_candidate_entry(selector)
        if item is None:
            return

        current_intf = self.transport_controls['usb_interface_number'].text().strip()
        current_out = self.transport_controls['usb_endpoint_out'].text().strip()
        current_in = self.transport_controls['usb_endpoint_in'].text().strip()

        if overwrite or not current_intf:
            value = item.get('interface_number')
            self.transport_controls['usb_interface_number'].setText('' if value is None else str(int(value)))

        if overwrite or not current_out:
            value = item.get('endpoint_out')
            self.transport_controls['usb_endpoint_out'].setText('' if value is None else hex(int(value)))

        if overwrite or not current_in:
            value = item.get('endpoint_in')
            self.transport_controls['usb_endpoint_in'].setText('' if value is None else hex(int(value)))

    def _on_usb_device_changed(self, value: str) -> None:
        if self._building:
            return
        self._sync_usb_fields_from_selector(value, overwrite=True)
        self._on_ui_edited()

    def _apply_suggested_connection(self) -> None:
        suggestion = (self._candidate_cache or {}).get('suggestion') or {}
        selector = str(suggestion.get('selector') or '').strip()
        kind = str(suggestion.get('kind') or self._combo_value(self.transport_box) or 'serial').strip()
        if not selector:
            QMessageBox.information(self, 'Suggested connection', 'No connection suggestion is available right now.')
            return
        self._set_combo_value(self.transport_box, kind)
        is_native_usb = kind == 'usb' and (':55e0' in selector or suggestion.get('interface_number') is not None)
        is_auto_di = str(suggestion.get('transport') or '').strip() == 'USB-UART Auto DI'
        if is_native_usb:
            self.transport_controls['serial_auto_di'].setChecked(False)
            self._set_combo_value(self.transport_controls['usb_device'], selector)
            if suggestion.get('interface_number') is not None:
                self.transport_controls['usb_interface_number'].setText(str(suggestion['interface_number']))
            if suggestion.get('endpoint_out') is not None:
                self.transport_controls['usb_endpoint_out'].setText(hex(int(suggestion['endpoint_out'])))
            if suggestion.get('endpoint_in') is not None:
                self.transport_controls['usb_endpoint_in'].setText(hex(int(suggestion['endpoint_in'])))
        else:
            self._set_combo_value(self.transport_controls['serial_port'], selector)
            self.transport_controls['serial_auto_di'].setChecked(is_auto_di)
        self._sync_from_project()
        self._append_log('INFO', f'suggested connection applied: {selector}')

    def _sync_from_project(self, initial: bool = False) -> None:
        if self._building:
            return
        p = self.session.project
        if initial:
            self.firmware_field.setText(p.firmware_path)
            self.series_box.setCurrentText(p.family)
            self.chip_box.setCurrentText(p.chip)
            self._apply_transport_choices(p.chip, preferred=p.transport.kind)
            self._set_combo_value(self.transport_controls['serial_port'], p.transport.serial_port)
            self._set_combo_value(self.transport_controls['usb_device'], p.transport.usb_device)
            self.transport_controls['fast_baud'].setCurrentText(str(p.operations.fast_baud))
            self.transport_controls['serial_auto_di'].setChecked(bool(p.transport.serial_auto_di))
            self.transport_controls['usb_interface_number'].setText('' if p.transport.usb_interface_number is None else str(p.transport.usb_interface_number))
            self.transport_controls['usb_endpoint_out'].setText('' if p.transport.usb_endpoint_out is None else str(p.transport.usb_endpoint_out))
            self.transport_controls['usb_endpoint_in'].setText('' if p.transport.usb_endpoint_in is None else str(p.transport.usb_endpoint_in))
        self._collect_project_from_ui(silent=True)
        try:
            resolved = self.resolver.resolve(self.session.project.chip, transport=self.session.project.transport.kind)
        except ChispError as exc:
            self._append_log('ERROR', str(exc))
            return
        display_mode = self._effective_connection_mode(resolved)
        self.device_pill.setText(self.session.project.chip)
        self.mode_pill.setText(display_mode)
        self.connection_summary.setText(display_mode)
        self.connection_mode_title.setText(display_mode)
        self.flash_target.setText(self.session.project.chip)
        self.flash_details.setText(f'Ready for {display_mode.lower()} using the selected firmware file.')
        self.transport_meta.setText(display_mode)
        self._apply_transport_visibility(resolved)
        self._apply_config_profile(resolved)
        self._apply_connection_defaults(resolved)
        self._refresh_action_capabilities(resolved)
        self._update_config_hint(resolved)
        self._update_flash_notes(resolved)
        self._update_firmware_meta()
        self.status_label.setText(f'Ready - {self.session.project.chip}')
        self._update_project_header()

    def _apply_transport_visibility(self, resolved) -> None:
        if resolved.protocol_variant == 'usb_native_plain':
            visible = {'usb_device', 'usb_interface_number', 'usb_endpoint_out', 'usb_endpoint_in'}
        else:
            visible = {'serial_port', 'fast_baud', 'serial_auto_di'}
        for key in ['serial_port', 'usb_device', 'fast_baud', 'serial_auto_di', 'usb_interface_number', 'usb_endpoint_out', 'usb_endpoint_in']:
            row = self.transport_controls.get(f'row_{key}')
            if row is not None:
                row.setVisible(key in visible)
        auto_di_box = self.transport_controls.get('serial_auto_di')
        if auto_di_box is not None:
            auto_di_supported = self._serial_auto_di_supported(resolved.chip_name)
            auto_di_visible = resolved.protocol_variant != 'usb_native_plain'
            auto_di_box.setEnabled(auto_di_supported and auto_di_visible)
            if auto_di_visible:
                auto_di_box.setToolTip('' if auto_di_supported else 'Auto DI is not available for this chip family.')
            else:
                auto_di_box.setToolTip('Auto DI is not used for native USB bootloader targets.')
            if (not auto_di_supported or not auto_di_visible) and auto_di_box.isChecked():
                auto_di_box.setChecked(False)

    def _preferred_chip_serial_value(self, chip_name: str, key: str) -> str:
        serial_meta = self.resolver.transport_meta(chip_name, 'serial')
        family_name = str(serial_meta.get('backend_family') or '').strip()
        family = self.chipdb.families.get(family_name, {}) if family_name else {}
        for section_name in ('flash_defaults', 'flash_defaults_serial_inferred'):
            section = dict(family.get(section_name) or {})
            chip_defaults = dict(section.get(chip_name) or {})
            value = chip_defaults.get(key)
            if value is None:
                value = section.get(f'default_{key}')
            if value is not None:
                try:
                    return str(int(value))
                except Exception:
                    return ''
        for mode_key in ('serial_manual', 'serial_auto_di'):
            value = self.resolver.transport_mode_meta(chip_name, mode_key).get(key)
            if value is not None:
                try:
                    return str(int(value))
                except Exception:
                    return ''
        return ''

    def _preferred_fast_baud(self, chip_name: str) -> str:
        return self._preferred_chip_serial_value(chip_name, 'fast_baud')

    def _write_config_supported(self, resolved) -> bool:
        try:
            backend = make_backend(resolved.backend_family)
        except Exception:
            return False
        if resolved.protocol_variant == 'usb_native_plain':
            return bool(getattr(backend, 'supports_config_write_native_usb', True))
        return bool(getattr(backend, 'supports_config_write_uart_framed', True))

    def _refresh_action_capabilities(self, resolved) -> None:
        write_ok = self._write_config_supported(resolved)
        if hasattr(self, 'config_apply_button') and self.config_apply_button is not None:
            self.config_apply_button.setEnabled(write_ok)
            self.config_apply_button.setToolTip('' if write_ok else 'Apply config is not available for this chip family yet.')
        if hasattr(self, 'action_write_config') and self.action_write_config is not None:
            self.action_write_config.setEnabled(write_ok)

    def _apply_connection_defaults(self, resolved) -> None:
        preferred_fast_baud = self._preferred_fast_baud(resolved.chip_name)
        current_fast_baud = self.transport_controls['fast_baud'].currentText().strip()
        if current_fast_baud == '':
            if preferred_fast_baud:
                self.transport_controls['fast_baud'].setCurrentText(preferred_fast_baud)
            else:
                self.transport_controls['fast_baud'].setCurrentText('1000000')
        if resolved.protocol_variant == 'usb_native_plain' and not self._combo_value(self.transport_controls['usb_device']):
            common = self.resolver.transport_meta(resolved.chip_name, 'usb').get('common_usb_selectors') or []
            if common:
                self._set_combo_value(self.transport_controls['usb_device'], str(common[0]))

    def _apply_config_profile(self, resolved) -> None:
        profile = resolved.gui_profile
        visible = set(profile.get('controls_visible') or [])
        hidden = set(profile.get('controls_hidden') or [])
        option_profile = str(profile.get('option_profile') or resolved.chip_meta.get('option_profile') or self.chipdb.families.get(resolved.backend_family, {}).get('option_profile') or '').strip()
        option_meta = self.chipdb.option_profiles.get(option_profile, {}) if option_profile else {}
        active_sections = list(option_meta.get('ui_sections') or [])
        for key, row in self.config_rows.items():
            row.setVisible(key in visible and key not in hidden)
        for section_id in SECTION_ORDER:
            box = self.config_section_boxes.get(section_id)
            note = self.config_section_notes.get(section_id)
            meta = SECTION_META[section_id]
            section_fields = meta['fields']
            section_active = section_id in active_sections if active_sections else True
            section_visible = section_active and any(k in visible and k not in hidden for k in section_fields)
            if box is not None:
                box.setVisible(section_visible)
            if note is not None:
                extra = []
                for field_name in section_fields:
                    if field_name in visible and field_name not in hidden and FIELD_NOTES.get(field_name):
                        extra.append(FIELD_NOTES[field_name])
                text = meta['description']
                if extra:
                    text += ' ' + ' '.join(extra)
                note.setText(text)
        combo = self.config_controls.get('ramx_rom_mode')
        if isinstance(combo, QComboBox):
            combo.clear()
            combo.addItem('')
            seen = set()
            for value in profile.get('ramx_rom_values_observed') or []:
                text = str(value)
                if text not in seen:
                    seen.add(text)
                    combo.addItem(text)
        self.config_profile_pill.setText(PROFILE_SUMMARY.get(option_profile, 'Profile-aware editor'))

    def _update_config_hint(self, resolved) -> None:
        profile = resolved.gui_profile
        option_profile = str(profile.get('option_profile') or resolved.chip_meta.get('option_profile') or self.chipdb.families.get(resolved.backend_family, {}).get('option_profile') or '').strip()
        parts = []
        if PROFILE_SUMMARY.get(option_profile):
            parts.append(PROFILE_SUMMARY[option_profile])
        if 'ramx_rom_mode' in set(profile.get('controls_visible') or []):
            parts.append('This target exposes memory layout options.')
        if self._effective_connection_mode(resolved) == 'Native USB bootloader':
            parts.append('Read config first, change only what you need, then apply.')
        self.config_hint.setText(' '.join(parts).strip())

    def _update_flash_notes(self, resolved) -> None:
        guide = get_guide(self._effective_connection_mode(resolved))
        lines = []
        for idx, text in enumerate(guide.get('steps') or [], start=1):
            lines.append(f'{idx}. {text}')
        detail = str(guide.get('details') or '').strip()
        if detail:
            lines += ['', detail]
        if hasattr(self, 'flash_notes') and self.flash_notes is not None:
            self.flash_notes.setPlainText('\n'.join(lines))

    def _format_size_label(self, size_bytes: int) -> str:
        size = int(size_bytes)
        if size < 1024:
            return f'{size} B'
        kb = size / 1024.0
        text = f'{kb:.1f}'
        if text.endswith('.0'):
            text = text[:-2]
        return f'{text} kB'

    def _update_firmware_meta(self) -> None:
        path = self.firmware_field.text()
        if not path:
            self.firmware_meta.setText('No firmware selected')
            return
        p = Path(path)
        if not p.is_file():
            self.firmware_meta.setText('File not found')
            return
        self.firmware_meta.setText(f'{p.name} - {self._format_size_label(p.stat().st_size)}')

    def _load_project_into_ui(self) -> None:
        p = self.session.project
        self._building = True
        try:
            with self._blocked_widget_signals(self._ui_signal_widgets()):
                self.firmware_field.setText(p.firmware_path)
                self.series_box.setCurrentText(p.family)
                self._rebuild_chip_choices(p.family, preferred_chip=p.chip)
                self._apply_transport_choices(p.chip, preferred=p.transport.kind)
                self._set_combo_value(self.transport_box, p.transport.kind)
                self._set_combo_value(self.transport_controls['serial_port'], p.transport.serial_port)
                self._set_combo_value(self.transport_controls['usb_device'], p.transport.usb_device)
                self.transport_controls['fast_baud'].setCurrentText(str(p.operations.fast_baud))
                self.transport_controls['serial_auto_di'].setChecked(bool(p.transport.serial_auto_di))
                self.transport_controls['usb_interface_number'].setText('' if p.transport.usb_interface_number is None else str(p.transport.usb_interface_number))
                self.transport_controls['usb_endpoint_out'].setText('' if p.transport.usb_endpoint_out is None else str(p.transport.usb_endpoint_out))
                self.transport_controls['usb_endpoint_in'].setText('' if p.transport.usb_endpoint_in is None else str(p.transport.usb_endpoint_in))
                for key, widget in self.config_controls.items():
                    value = getattr(p.config, key)
                    if isinstance(widget, QCheckBox):
                        widget.setChecked(bool(value))
                    elif isinstance(widget, QComboBox):
                        widget.setCurrentText(str(value or ''))
                    else:
                        widget.setText(str(value))
        finally:
            self._building = False
        self._refresh_connection_candidates(initial=True)
        self._sync_from_project()

    def _collect_project_from_ui(self, *, silent: bool = False) -> None:
        p = self.session.project
        p.family = self.series_box.currentText().strip()
        p.chip = self.chip_box.currentText().strip()
        p.firmware_path = self.firmware_field.text()
        p.transport.kind = self._combo_value(self.transport_box)
        p.transport.serial_port = self._combo_value(self.transport_controls['serial_port'])
        p.transport.usb_device = self._combo_value(self.transport_controls['usb_device'])
        auto_di_requested = self.transport_controls['serial_auto_di'].isChecked() and self._serial_auto_di_supported(p.chip)
        serial_auto_di = False
        if auto_di_requested:
            try:
                resolved = self.resolver.resolve(p.chip, transport=p.transport.kind)
                serial_auto_di = getattr(resolved, 'protocol_variant', '') != 'usb_native_plain'
            except Exception:
                serial_auto_di = p.transport.kind != 'usb'
        if p.transport.kind == 'serial':
            p.transport.usb_device = ''
            p.transport.usb_interface_number = None
            p.transport.usb_endpoint_out = None
            p.transport.usb_endpoint_in = None
        elif serial_auto_di:
            p.transport.usb_device = ''
            p.transport.usb_interface_number = None
            p.transport.usb_endpoint_out = None
            p.transport.usb_endpoint_in = None
        else:
            p.transport.serial_port = ''
            p.transport.usb_interface_number = self._parse_int_or_none(self.transport_controls['usb_interface_number'].text(), quiet=silent)
            p.transport.usb_endpoint_out = self._parse_int_or_none(self.transport_controls['usb_endpoint_out'].text(), quiet=silent)
            p.transport.usb_endpoint_in = self._parse_int_or_none(self.transport_controls['usb_endpoint_in'].text(), quiet=silent)
        try:
            p.operations.fast_baud = int(self.transport_controls['fast_baud'].currentText().strip(), 0)
        except Exception:
            p.operations.fast_baud = 1000000
        p.transport.serial_auto_di = serial_auto_di
        for key, widget in self.config_controls.items():
            if isinstance(widget, QCheckBox):
                setattr(p.config, key, widget.isChecked())
            elif isinstance(widget, QComboBox):
                setattr(p.config, key, widget.currentText().strip())
            else:
                setattr(p.config, key, widget.text().strip())

    def _parse_int_or_none(self, text: str, *, quiet: bool = False):
        s = (text or '').strip()
        if not s:
            return None
        try:
            return int(s, 0)
        except Exception:
            if quiet:
                return None
            raise

    def _set_running(self, running: bool) -> None:
        widgets = [
            self.manage_project_btn,
            self.options_btn,
            self.firmware_field.line_edit,
            self.firmware_field.button,
            self.series_box,
            self.chip_box,
            self.transport_box,
            self.refresh_ports_btn,
            self.use_suggested_btn,
            self.detect_button,
            self.read_cfg_button,
            self.flash_button,
            self.more_actions_btn,
            self.manual_btn,
            getattr(self, 'config_read_button', None),
            getattr(self, 'config_apply_button', None),
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(not running)
        if not running:
            try:
                resolved = self.resolver.resolve(self.session.project.chip, transport=self.session.project.transport.kind)
                self._refresh_action_capabilities(resolved)
            except Exception:
                pass
        self.status_label.setText('Working...' if running else f'Ready - {self.session.project.chip}')

    def _append_log(self, level: str, message: str) -> None:
        self.log_panel.append_line(level, message)

    def _copy_log(self) -> None:
        text = self.log_panel.export_text(visible_only=False)
        if text.strip():
            QApplication.clipboard().setText(text)

    def _clear_log(self) -> None:
        self.log_panel.clear_entries()

    def _start_action(self, action: str) -> None:
        self._collect_project_from_ui(silent=True)
        if action in {'flash', 'verify_only'}:
            path = Path(self.session.project.firmware_path)
            if not path.is_file():
                QMessageBox.warning(self, 'Firmware', 'Select a valid firmware file first.')
                return
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return
        self.progress.setValue(0)
        self._set_running(True)
        self._append_log('INFO', f'{action} requested for {self.session.project.chip}')
        self._worker_thread = QThread(self)
        self._worker = ActionWorker(action, self.session.project)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log.connect(self._append_log)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._on_action_done)
        self._worker.failed.connect(self._on_action_failed)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.finished.connect(lambda: self._set_running(False))
        self._worker_thread.finished.connect(self._clear_worker)
        self._worker_thread.start()

    def _detect_target(self) -> None:
        self._start_action('detect')

    def _smart_detect_target(self) -> None:
        self._start_action('smart_detect')

    def _read_config(self) -> None:
        self._start_action('read_config')

    def _write_config(self) -> None:
        self._start_action('write_config')

    def _flash(self) -> None:
        self._start_action('flash')

    def _erase_only(self) -> None:
        self._start_action('erase_only')

    def _verify_only(self) -> None:
        self._start_action('verify_only')

    def _clear_worker(self) -> None:
        self._worker = None
        self._worker_thread = None

    def _on_action_done(self, action: str, result: dict) -> None:
        chip = str(result.get('chip') or self.session.project.chip)
        self.progress.setValue(100)
        if action in {'detect', 'smart_detect'}:
            self.status_label.setText(f'Detected - {chip}')
            self._apply_detect_result_to_project(result)
            self._append_log('INFO', f'{action} done: {chip}')
            self._remember_current_connection(action, notes='target detected')
        elif action == 'read_config':
            self.status_label.setText(f'Config read - {chip}')
            self._last_config_result = dict(result)
            self._apply_config_result_to_form(result)
            self._append_log('INFO', f'config read: {chip}')
            self._remember_current_connection(action, notes='config read')
        elif action == 'write_config':
            self.status_label.setText(f'Config applied - {chip}')
            self._last_config_result = dict(result)
            self._apply_config_result_to_form(result)
            self._append_log('INFO', f'config applied: {chip}')
            self._remember_current_connection(action, notes='config applied')
        elif action == 'erase_only':
            self.status_label.setText(f'Erase finished - {chip}')
            self._append_log('INFO', f'erase-only done: {chip}')
            self._remember_current_connection(action, notes='erase done')
        elif action == 'verify_only':
            self.status_label.setText(f'Verify finished - {chip}')
            self._append_log('INFO', f'verify-only done: {chip}')
            self._remember_current_connection(action, notes='verify done')
        elif action == 'flash':
            self.status_label.setText(f'Finished - {chip}')
            self._append_log('INFO', f'flash done: {chip}')
            self._remember_current_connection(action, notes='flash done')
        self._update_flash_notes(self.resolver.resolve(self.session.project.chip, transport=self.session.project.transport.kind))

    def _on_action_failed(self, action: str, message: str) -> None:
        self.status_label.setText(f'{action} failed')
        self._append_log('ERROR', message)
        QMessageBox.critical(self, f'{action} failed', message)

    def _apply_detect_result_to_project(self, result: dict) -> None:
        matched_kind = str(result.get('matched_transport_kind') or '').strip()
        matched_series = str(result.get('matched_series') or '').strip()
        matched_chip = str(result.get('matched_chip') or '').strip()
        auto_update = bool(result.get('auto_update_recommended'))
        if matched_kind and matched_kind != self._combo_value(self.transport_box):
            self._set_combo_value(self.transport_box, matched_kind)
        if matched_chip and auto_update:
            if matched_series and matched_series != self.series_box.currentText().strip():
                self.series_box.setCurrentText(matched_series)
                self._on_series_changed(matched_series)
            if matched_chip != self.chip_box.currentText().strip():
                self.chip_box.setCurrentText(matched_chip)
        matched_variant = str(result.get('matched_protocol_variant') or '').strip()
        detected_auto_di = bool(result.get('serial_auto_di')) and matched_variant != 'usb_native_plain'
        if matched_kind:
            self.transport_controls['serial_auto_di'].setChecked(detected_auto_di)
        if result.get('port'):
            self._set_combo_value(self.transport_controls['serial_port'], str(result['port']))
        if result.get('usb_selector'):
            self._set_combo_value(self.transport_controls['usb_device'], str(result['usb_selector']))
        if result.get('interface_number') is not None:
            self.transport_controls['usb_interface_number'].setText(str(result['interface_number']))
        if result.get('endpoint_out') is not None:
            self.transport_controls['usb_endpoint_out'].setText(hex(int(result['endpoint_out'])))
        if result.get('endpoint_in') is not None:
            self.transport_controls['usb_endpoint_in'].setText(hex(int(result['endpoint_in'])))
        self._refresh_connection_candidates(initial=False)
        self._sync_from_project()

    def _apply_config_result_to_form(self, result: dict) -> None:
        form_values = result.get('form_values') or {}
        if isinstance(form_values, dict):
            for field_name, value in form_values.items():
                widget = self.config_controls.get(field_name)
                if widget is None:
                    continue
                if isinstance(widget, QLineEdit):
                    widget.setText(str(value))
                elif isinstance(widget, QComboBox):
                    widget.setCurrentText(str(value))
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
        mapping = {
            'data0': 'data0_hex',
            'data1': 'data1_hex',
            'wrp0': 'wrp0_hex',
            'wrp1': 'wrp1_hex',
            'wrp2': 'wrp2_hex',
            'wrp3': 'wrp3_hex',
            'ramx_rom_mode': 'ramx_rom_mode',
            'enable_soft_ctrl_iwdg': 'enable_soft_ctrl_iwdg',
            'disable_stop_mode_rst': 'disable_stop_mode_rst',
            'disable_standby_mode_rst': 'disable_standby_mode_rst',
            'enable_long_delay_time': 'enable_long_delay_time',
        }
        for field_name, result_name in mapping.items():
            widget = self.config_controls.get(field_name)
            if widget is None or result_name not in result:
                continue
            value = result.get(result_name)
            if isinstance(widget, QLineEdit) and value:
                widget.setText(str(value))
            elif isinstance(widget, QComboBox) and value is not None:
                widget.setCurrentText(str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))

    def _remember_current_connection(self, action: str, *, notes: str = '') -> None:
        self._collect_project_from_ui(silent=True)
        saved = project_to_saved_connection(self.session.project, action=action, notes=notes)
        enrich_saved_connection_from_candidates(saved, self._candidate_cache or {})
        remember_recent_connection(self.app_state, saved)
        self._save_app_state()

    def _maybe_auto_apply_best_connection(self, *, force: bool, log: bool) -> None:
        if self._building:
            return
        self._collect_project_from_ui(silent=True)
        best = find_best_recent_connection(
            self.app_state,
            chip=self.session.project.chip,
            family=self.session.project.family,
            transport_kind=self.session.project.transport.kind,
            candidates=self._candidate_cache or {},
            serial_auto_di=bool(self.session.project.transport.serial_auto_di),
        )
        if best is None:
            return
        current = project_to_saved_connection(self.session.project, action='current')
        enrich_saved_connection_from_candidates(current, self._candidate_cache or {})
        if (
            current.device_fingerprint == best.device_fingerprint
            and current.selector == best.selector
            and current.transport_kind == best.transport_kind
            and bool(current.serial_auto_di) == bool(best.serial_auto_di)
        ):
            return
        if not force and not best.selector:
            return
        apply_saved_connection(self.session.project, best)
        self._load_project_into_ui()
        if log:
            self._append_log('INFO', f'best recent connection auto-applied: {describe_saved_connection(best)}')

