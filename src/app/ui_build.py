"""UI construction and table rendering for the main window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..ui.delegates import ProgressDelegate
from .helpers import _format_resume_tooltip, _summarize_pipeline, _summarize_source, format_elapsed_seconds
from .theme import BrandWordmarkWidget


_ROLE_STEP_PROGRESS = int(Qt.ItemDataRole.UserRole)
_ROLE_JOB_PROGRESS = _ROLE_STEP_PROGRESS + 1


def _build_menu(self: QMainWindow):
    mb = self.menuBar()

    file_menu = mb.addMenu("&Datei")
    self.act_duplicate = file_menu.addAction("Workflow duplizieren")
    self.act_duplicate.setShortcut(QKeySequence("Ctrl+D"))
    self.act_duplicate.triggered.connect(self._duplicate_job)
    file_menu.addSeparator()
    action = file_menu.addAction("Workflow laden …")
    action.setShortcut(QKeySequence("Ctrl+I"))
    action.triggered.connect(self._load_workflow)
    action = file_menu.addAction("Workflow speichern …")
    action.setShortcut(QKeySequence("Ctrl+E"))
    action.triggered.connect(self._save_workflow)
    file_menu.addSeparator()
    file_menu.addAction("Workflow leeren", self._clear_workflow)
    file_menu.addSeparator()
    file_menu.addAction("Beenden", self.close)

    settings_menu = mb.addMenu("&Einstellungen")
    settings_menu.addAction("Video …", self._open_video_settings)
    settings_menu.addAction("Audio …", self._open_audio_settings)
    settings_menu.addAction("YouTube …", self._open_youtube_settings)
    settings_menu.addAction("Kaderblick …", self._open_kaderblick_settings)
    settings_menu.addAction("Kameras …", self._open_camera_settings)
    settings_menu.addSeparator()
    settings_menu.addAction("Allgemein …", self._open_general_settings)


def _build_toolbar(self: QMainWindow):
    tb = QToolBar("Aktionen")
    tb.setObjectName("mainToolbar")
    tb.setMovable(False)
    self.addToolBar(tb)

    self._brand_wordmark = BrandWordmarkWidget(parent=self)
    tb.addWidget(self._brand_wordmark)
    tb.addSeparator()

    tb.addAction("＋ Neuer Workflow", self._new_workflow)
    tb.addSeparator()
    tb.addAction("Bearbeiten", self._edit_job)
    tb.addAction("Kopieren", self._duplicate_job)
    tb.addAction("Workflow", self._open_job_workflow)
    tb.addAction("Entfernen", self._clear_workflow)
    tb.addSeparator()

    self.act_start = tb.addAction("▶  Starten", self._start_selected_workflows)
    self.act_cancel = tb.addAction("■  Abbrechen", self._cancel_workflow)
    self.act_cancel.setEnabled(False)
    tb.addSeparator()

    tb.addAction("Laden", self._load_workflow)
    tb.addAction("Speichern", self._save_workflow)
    tb.addSeparator()

    self._publish_kaderblick_cb = QCheckBox("Kaderblick-Publish: AUS")
    self._publish_kaderblick_cb.setObjectName("publishKaderblickToggle")
    self._publish_kaderblick_cb.setToolTip(
        "Meldet Kaderblick erst nach erfolgreichem Abschluss aller gestarteten Jobs, dass alle Videos vorliegen."
    )
    self._publish_kaderblick_cb.toggled.connect(self._on_publish_kaderblick_toggled)
    tb.addWidget(self._publish_kaderblick_cb)
    tb.addSeparator()

    self._shutdown_cb = QCheckBox("Herunterfahren: AUS")
    self._shutdown_cb.setObjectName("shutdownToggle")
    self._shutdown_cb.setToolTip(
        "Wenn aktiviert, wird der Rechner nach einem vollständig erfolgreichen Workflow-Lauf heruntergefahren."
    )
    self._shutdown_cb.toggled.connect(self._on_shutdown_toggled)
    tb.addWidget(self._shutdown_cb)


def _build_central(self: QMainWindow):
    container = QWidget()
    outer_layout = QVBoxLayout(container)
    outer_layout.setContentsMargins(20, 18, 20, 16)
    outer_layout.setSpacing(16)

    splitter = QSplitter(Qt.Vertical)
    splitter.setChildrenCollapsible(False)

    self.table = QTableWidget(0, 7)
    self.table.setHorizontalHeaderLabels(["#", "Name", "Quelle", "Pipeline", "Status", "Job", "Dauer"])
    header = self.table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.Stretch)
    header.setSectionResizeMode(2, QHeaderView.Interactive)
    header.setSectionResizeMode(3, QHeaderView.Interactive)
    header.setSectionResizeMode(4, QHeaderView.Interactive)
    header.setSectionResizeMode(5, QHeaderView.Interactive)
    header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
    header.resizeSection(2, 180)
    header.resizeSection(3, 160)
    header.resizeSection(4, 260)
    header.resizeSection(5, 120)
    header.resizeSection(6, 90)
    self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
    self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    self.table.verticalHeader().setVisible(False)
    self.table.setAlternatingRowColors(True)
    self.table.doubleClicked.connect(self._handle_table_double_click)

    self._step_progress_delegate = ProgressDelegate(self.table, progress_role=_ROLE_STEP_PROGRESS)
    self._job_progress_delegate = ProgressDelegate(self.table, progress_role=_ROLE_JOB_PROGRESS)
    self.table.setItemDelegateForColumn(4, self._step_progress_delegate)
    self.table.setItemDelegateForColumn(5, self._job_progress_delegate)

    table_frame = QFrame()
    table_frame.setObjectName("cardSurface")
    table_layout = QVBoxLayout(table_frame)
    table_layout.setContentsMargins(12, 12, 12, 12)
    table_layout.addWidget(self.table)
    splitter.addWidget(table_frame)

    self.log_text = QTextEdit()
    self.log_text.setReadOnly(True)
    self.log_text.setFont(QFont("Monospace", 9))

    log_frame = QFrame()
    log_frame.setObjectName("cardSurface")
    log_layout = QVBoxLayout(log_frame)
    log_layout.setContentsMargins(12, 12, 12, 12)
    log_layout.addWidget(self.log_text)
    splitter.addWidget(log_frame)

    splitter.setStretchFactor(0, 3)
    splitter.setStretchFactor(1, 1)
    outer_layout.addWidget(splitter)
    self.setCentralWidget(container)


def _build_statusbar(self: QMainWindow):
    self.status_label = QLabel("Bereit")
    self.statusBar().addWidget(self.status_label, 2)

    self.duration_label = QLabel("Gesamtdauer: –")
    self.statusBar().addPermanentWidget(self.duration_label)

    self.progress = QProgressBar()
    self.progress.setTextVisible(True)
    self.progress.setMaximumHeight(18)
    self.progress.setValue(0)
    self.progress.setFormat("Gesamt: %v/%m")
    self.statusBar().addPermanentWidget(self.progress, 1)


def _refresh_table(self):
    jobs = self._workflow.jobs
    self.table.setRowCount(len(jobs))
    for row, job in enumerate(jobs):
        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.table.setItem(row, 1, QTableWidgetItem(job.name or "–"))
        self.table.setItem(row, 2, QTableWidgetItem(_summarize_source(job)))
        pipeline_item = QTableWidgetItem(_summarize_pipeline(job))
        pipeline_item.setToolTip("Doppelklick für Workflow-Editor")
        self.table.setItem(row, 3, pipeline_item)
        _resume = job.resume_status or ""
        _status_text = "Ausstehend" if _resume.endswith("\u2026") else (_resume or "Wartend")
        status_item = QTableWidgetItem(_status_text)
        status_item.setData(_ROLE_STEP_PROGRESS, job.progress_pct)
        status_item.setToolTip(_format_resume_tooltip(job))
        self.table.setItem(row, 4, status_item)

        job_item = QTableWidgetItem(f"{job.overall_progress_pct}%")
        job_item.setData(_ROLE_JOB_PROGRESS, job.overall_progress_pct)
        job_item.setToolTip("Doppelklick für Workflow-Editor")
        self.table.setItem(row, 5, job_item)
        duration_item = QTableWidgetItem(_format_elapsed_cell(job.run_elapsed_seconds))
        duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 6, duration_item)

    if hasattr(self, "duration_label"):
        total_seconds = float(getattr(self._workflow, "last_run_elapsed_seconds", 0.0) or 0.0)
        self.duration_label.setText(f"Gesamtdauer: {_format_elapsed_cell(total_seconds)}")


def _set_row_status(self, row: int, status: str):
    item = self.table.item(row, 4)
    if item is None:
        item = QTableWidgetItem()
        self.table.setItem(row, 4, item)
    item.setText(status)
    active_prefixes = (
        "Läuft",
        "Herunterladen",
        "Transfer",
        "Konvertiere",
        "Zusammenführen",
        "Titelkarte",
        "YT-Version",
        "YouTube-Upload",
        "Kaderblick",
    )
    if status == "Fertig":
        item.setForeground(Qt.darkGreen)
    elif "Fehler" in status:
        item.setForeground(Qt.red)
    elif status == "Übersprungen":
        item.setForeground(Qt.gray)
    elif status.startswith(active_prefixes):
        item.setForeground(Qt.blue)
    else:
        item.setForeground(Qt.black)
    self.table.viewport().update()


def _set_row_progress(self, row: int, pct: int):
    item = self.table.item(row, 4)
    if item is None:
        item = QTableWidgetItem()
        self.table.setItem(row, 4, item)
    item.setData(_ROLE_STEP_PROGRESS, pct)
    self.table.viewport().update()


def _set_row_job_progress(self, row: int, pct: int):
    item = self.table.item(row, 5)
    if item is None:
        item = QTableWidgetItem()
        self.table.setItem(row, 5, item)
    item.setText(f"{pct}%")
    item.setData(_ROLE_JOB_PROGRESS, pct)
    self.table.viewport().update()


def _set_row_duration(self, row: int, seconds: float):
    item = self.table.item(row, 6)
    if item is None:
        item = QTableWidgetItem()
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 6, item)
    item.setText(_format_elapsed_cell(seconds))


def _format_elapsed_cell(seconds: float) -> str:
    if seconds and seconds >= 1.0:
        return format_elapsed_seconds(seconds)
    return "–"


def _reset_status_column(self, rows: set[int] | None = None):
    all_rows = rows if rows is not None else set(range(self.table.rowCount()))
    for row in all_rows:
        status_item = self.table.item(row, 4)
        if status_item is None:
            status_item = QTableWidgetItem()
            self.table.setItem(row, 4, status_item)
        status_item.setText("Wartend")
        status_item.setData(_ROLE_STEP_PROGRESS, 0)
        status_item.setForeground(Qt.black)

        job_item = self.table.item(row, 5)
        if job_item is None:
            job_item = QTableWidgetItem()
            self.table.setItem(row, 5, job_item)
        job_item.setText("0%")
        job_item.setData(_ROLE_JOB_PROGRESS, 0)

        duration_item = self.table.item(row, 6)
        if duration_item is None:
            duration_item = QTableWidgetItem()
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, duration_item)
        duration_item.setText("–")
    self.table.viewport().update()


def _update_count(self):
    self.status_label.setStyleSheet("")
    count = len(self._workflow.jobs)
    self.status_label.setText(f"{count} Workflow{'s' if count != 1 else ''} geladen" if count else "Bereit")


def _handle_table_double_click(self, index):
    self._open_job_workflow(index.row())


@Slot(str)
def _append_log(self, msg: str):
    self.log_text.append(msg)
