"""Main window facade for Kaderblick — Video Manager."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow

from ..runtime_paths import asset_path
from ..workflow import Workflow
from .execution import (
    _cancel_workflow,
    _on_dl_progress,
    _on_job_progress,
    _on_job_status,
    _on_overall_progress,
    _on_phase_changed,
    _on_source_progress,
    _on_source_status,
    _on_workflow_done,
    _refresh_runtime_durations,
    _snapshot_runtime_durations,
    _start_selected_workflows,
    _start_workflow,
)
from .settings_actions import (
    _open_audio_settings,
    _open_camera_settings,
    _open_general_settings,
    _open_kaderblick_settings,
    _open_video_settings,
    _open_youtube_settings,
)
from .ui_build import (
    _append_log,
    _build_central,
    _build_menu,
    _build_statusbar,
    _build_toolbar,
    _handle_table_double_click,
    _refresh_table,
    _reset_status_column,
    _set_row_duration,
    _set_row_job_progress,
    _set_row_progress,
    _set_row_status,
    _update_count,
)
from .theme import apply_application_theme
from .workflow_actions import (
    _add_all_cameras,
    _add_job,
    _apply_add_files,
    _apply_workflow,
    _ask_resume_behavior,
    _clear_workflow,
    _duplicate_job,
    _edit_job,
    _existing_job_names,
    _has_resumeable_jobs,
    _load_workflow,
    _new_workflow,
    _open_job_workflow,
    _persist_workflow_state,
    _remove_selected,
    _resolve_job_name,
    _on_shutdown_toggled,
    _on_publish_kaderblick_toggled,
    _restore_last_workflow,
    _save_last_workflow,
    _save_workflow,
    _selected_job_row,
    _selected_job_rows,
    _sync_shutdown_checkbox,
    _sync_publish_kaderblick_checkbox,
)

if TYPE_CHECKING:
    from argparse import Namespace

    from PySide6.QtCore import QThread
    from PySide6.QtGui import QAction, QCloseEvent, QShowEvent
    from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox, QProgressBar, QTableWidget, QTextEdit

    from ..runtime.workflow_executor import WorkflowExecutor
    from ..workflow import WorkflowJob


class ConverterApp(QMainWindow):
    if TYPE_CHECKING:
        act_start: QAction
        act_cancel: QAction
        table: QTableWidget
        log_text: QTextEdit
        status_label: QLabel
        duration_label: QLabel
        progress: QProgressBar
        _shutdown_cb: QCheckBox
        _publish_kaderblick_cb: QCheckBox
        _wf_executor: WorkflowExecutor | None
        _wf_thread: QThread | None

        def _apply_add_files(self, paths: list[str]) -> None: ...
        def _apply_workflow(self, wf_path: str) -> None: ...
        def _build_menu(self) -> None: ...
        def _build_toolbar(self) -> None: ...
        def _build_central(self) -> None: ...
        def _build_statusbar(self) -> None: ...
        def _refresh_table(self) -> None: ...
        def _set_row_status(self, row: int, status: str) -> None: ...
        def _set_row_progress(self, row: int, pct: int) -> None: ...
        def _set_row_job_progress(self, row: int, pct: int) -> None: ...
        def _set_row_duration(self, row: int, seconds: float) -> None: ...
        def _reset_status_column(self, rows: set[int] | None = None) -> None: ...
        def _append_log(self, text: str) -> None: ...
        def _new_workflow(self) -> None: ...
        def _add_job(self) -> None: ...
        def _add_all_cameras(self) -> None: ...
        def _remove_selected(self) -> None: ...
        def _clear_workflow(self) -> None: ...
        def _selected_job_row(self) -> int: ...
        def _selected_job_rows(self) -> list[int]: ...
        def _handle_table_double_click(self, index) -> None: ...
        def _open_job_workflow(self, row: int | None = None) -> None: ...
        def _edit_job(self) -> None: ...
        def _duplicate_job(self) -> None: ...
        def _update_count(self) -> None: ...
        def _existing_job_names(self) -> list[str]: ...
        def _resolve_job_name(self, job: WorkflowJob, *, exclude_row: int | None = None) -> str | None: ...
        def _open_camera_settings(self) -> None: ...
        def _open_video_settings(self) -> None: ...
        def _open_audio_settings(self) -> None: ...
        def _open_youtube_settings(self) -> None: ...
        def _open_kaderblick_settings(self) -> None: ...
        def _open_general_settings(self) -> None: ...
        def _load_workflow(self) -> None: ...
        def _save_workflow(self) -> None: ...
        def _save_last_workflow(self) -> None: ...
        def _persist_workflow_state(self) -> None: ...
        def _restore_last_workflow(self) -> None: ...
        def _sync_shutdown_checkbox(self) -> None: ...
        def _sync_publish_kaderblick_checkbox(self) -> None: ...
        def _on_shutdown_toggled(self, checked: bool) -> None: ...
        def _on_publish_kaderblick_toggled(self, checked: bool) -> None: ...
        def _has_resumeable_jobs(self) -> bool: ...
        def _ask_resume_behavior(self) -> QMessageBox.StandardButton: ...
        def _start_selected_workflows(self) -> None: ...
        def _start_workflow(self, *, active_indices: set[int] | None = None) -> None: ...
        def _cancel_workflow(self) -> None: ...
        def _on_job_status(self, orig_idx: int, status: str) -> None: ...
        def _on_job_progress(self, orig_idx: int, pct: int) -> None: ...
        def _on_source_status(self, orig_idx: int, status: str) -> None: ...
        def _on_source_progress(self, orig_idx: int, pct: int) -> None: ...
        def _on_dl_progress(self, device: str, filename: str, transferred: float, total: float, speed_bps: float) -> None: ...
        def _on_phase_changed(self, phase: str) -> None: ...
        def _on_overall_progress(self, done: int, total: int) -> None: ...
        def _on_workflow_done(self, ok: int, skip: int, fail: int) -> None: ...
        def _refresh_runtime_durations(self) -> None: ...
        def _snapshot_runtime_durations(self, *, persist: bool = False) -> None: ...

    def __init__(self, cli_args: argparse.Namespace | None = None):
        super().__init__()
        from . import AppSettings

        self.setWindowTitle("Kaderblick — Video Manager")
        self.resize(960, 640)
        self.setMinimumSize(720, 460)

        icon_path = asset_path("kaderblick_workflow_appicon.svg")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.settings = AppSettings.load()

        self._workflow: Workflow = Workflow()
        self._wf_executor = None
        self._wf_thread = None
        self._wf_start_time: float = 0.0
        self._active_run_indices: set[int] = set()
        self._job_run_started_monotonic: dict[str, float] = {}
        self._job_run_elapsed_base_seconds: dict[str, float] = {}
        self._workflow_run_started_monotonic: float = 0.0
        self._workflow_run_elapsed_base_seconds: float = 0.0

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        apply_application_theme(self)
        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._refresh_runtime_durations)

        restore = self.settings.restore_last_workflow
        if cli_args and cli_args.restore_last_workflow:
            restore = True
        elif cli_args and cli_args.no_restore_last_workflow:
            restore = False
        if restore:
            self._restore_last_workflow()

        if cli_args and cli_args.add:
            self._apply_add_files(cli_args.add)

        self._cli_workflow: str | None = cli_args.workflow if cli_args else None

    def showEvent(self, event: QShowEvent):  # noqa: N802
        super().showEvent(event)
        if self._cli_workflow:
            workflow_path = self._cli_workflow
            self._cli_workflow = None
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, lambda: self._apply_workflow(workflow_path))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = int(seconds)
        if total_seconds >= 3600:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes:02d}min"
        if total_seconds >= 60:
            minutes = total_seconds // 60
            secs = total_seconds % 60
            return f"{minutes}min {secs:02d}s"
        return f"{total_seconds}s"

    def _set_busy(self, busy: bool):
        self.act_start.setEnabled(not busy)
        self.act_cancel.setEnabled(busy)

    def closeEvent(self, event: QCloseEvent):
        from . import QMessageBox

        running = self._wf_thread and self._wf_thread.isRunning()
        if running:
            if QMessageBox.question(
                self,
                "Verarbeitung läuft",
                "Workflow läuft noch. Wirklich beenden?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._wf_executor:
                self._wf_executor.cancel()
            if self._wf_thread:
                self._wf_thread.quit()
                if not self._wf_thread.wait(10_000):
                    self._wf_thread.terminate()
                    self._wf_thread.wait(2000)
            self._snapshot_runtime_durations(persist=False)
        self._save_last_workflow()
        event.accept()


ConverterApp._apply_add_files = _apply_add_files
ConverterApp._apply_workflow = _apply_workflow
ConverterApp._build_menu = _build_menu
ConverterApp._build_toolbar = _build_toolbar
ConverterApp._build_central = _build_central
ConverterApp._build_statusbar = _build_statusbar
ConverterApp._refresh_table = _refresh_table
ConverterApp._set_row_status = _set_row_status
ConverterApp._set_row_progress = _set_row_progress
ConverterApp._set_row_job_progress = _set_row_job_progress
ConverterApp._set_row_duration = _set_row_duration
ConverterApp._reset_status_column = _reset_status_column
ConverterApp._append_log = _append_log
ConverterApp._new_workflow = _new_workflow
ConverterApp._add_job = _add_job
ConverterApp._add_all_cameras = _add_all_cameras
ConverterApp._remove_selected = _remove_selected
ConverterApp._clear_workflow = _clear_workflow
ConverterApp._selected_job_row = _selected_job_row
ConverterApp._selected_job_rows = _selected_job_rows
ConverterApp._handle_table_double_click = _handle_table_double_click
ConverterApp._open_job_workflow = _open_job_workflow
ConverterApp._edit_job = _edit_job
ConverterApp._duplicate_job = _duplicate_job
ConverterApp._update_count = _update_count
ConverterApp._existing_job_names = _existing_job_names
ConverterApp._resolve_job_name = _resolve_job_name
ConverterApp._open_camera_settings = _open_camera_settings
ConverterApp._open_video_settings = _open_video_settings
ConverterApp._open_audio_settings = _open_audio_settings
ConverterApp._open_youtube_settings = _open_youtube_settings
ConverterApp._open_kaderblick_settings = _open_kaderblick_settings
ConverterApp._open_general_settings = _open_general_settings
ConverterApp._load_workflow = _load_workflow
ConverterApp._save_workflow = _save_workflow
ConverterApp._save_last_workflow = _save_last_workflow
ConverterApp._persist_workflow_state = _persist_workflow_state
ConverterApp._restore_last_workflow = _restore_last_workflow
ConverterApp._sync_shutdown_checkbox = _sync_shutdown_checkbox
ConverterApp._sync_publish_kaderblick_checkbox = _sync_publish_kaderblick_checkbox
ConverterApp._on_shutdown_toggled = _on_shutdown_toggled
ConverterApp._on_publish_kaderblick_toggled = _on_publish_kaderblick_toggled
ConverterApp._has_resumeable_jobs = _has_resumeable_jobs
ConverterApp._ask_resume_behavior = _ask_resume_behavior
ConverterApp._start_selected_workflows = _start_selected_workflows
ConverterApp._start_workflow = _start_workflow
ConverterApp._cancel_workflow = _cancel_workflow
ConverterApp._on_job_status = _on_job_status
ConverterApp._on_job_progress = _on_job_progress
ConverterApp._on_source_status = _on_source_status
ConverterApp._on_source_progress = _on_source_progress
ConverterApp._on_dl_progress = _on_dl_progress
ConverterApp._on_phase_changed = _on_phase_changed
ConverterApp._on_overall_progress = _on_overall_progress
ConverterApp._on_workflow_done = _on_workflow_done
ConverterApp._refresh_runtime_durations = _refresh_runtime_durations
ConverterApp._snapshot_runtime_durations = _snapshot_runtime_durations
