"""Workflow execution actions and slots for the main window."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Slot

from ..runtime_paths import shutdown_command
from .helpers import (
    _compute_job_overall_progress,
    _format_resume_tooltip,
    _job_has_source_config,
    _workflow_step_progress,
    format_elapsed_seconds,
)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return left == right


def _job_label(index: int, job) -> str:
    return (str(getattr(job, "name", "") or "").strip() or f"Job {index + 1}")


def _job_source_paths(job) -> set[Path]:
    from ..workflow import graph_has_multiple_sources, graph_source_nodes

    paths: set[Path] = set()
    if graph_has_multiple_sources(job):
        source_file_ids = {
            node_id
            for node_id, node_type in graph_source_nodes(job)
            if node_type == "source_files"
        }
        for entry in getattr(job, "files", []):
            raw_source = str(getattr(entry, "source_path", "") or "").strip()
            if not raw_source:
                continue
            source_id = str(getattr(entry, "graph_source_id", "") or "").strip()
            if source_id and source_id not in source_file_ids:
                continue
            paths.add(Path(raw_source))
    elif getattr(job, "files", None):
        for entry in job.files:
            raw_source = str(getattr(entry, "source_path", "") or "").strip()
            if raw_source:
                paths.add(Path(raw_source))

    has_folder_scan = bool(getattr(job, "source_mode", "") == "folder_scan")
    if not has_folder_scan:
        from ..workflow import graph_source_nodes

        has_folder_scan = any(node_type == "source_folder_scan" for _node_id, node_type in graph_source_nodes(job))

    if has_folder_scan:
        raw_folder = str(getattr(job, "source_folder", "") or "").strip()
        if raw_folder:
            source_dir = Path(raw_folder)
            pattern = str(getattr(job, "file_pattern", "") or "").strip() or "*.mp4"
            if source_dir.exists():
                for path in sorted(source_dir.glob(pattern)):
                    if path.is_file():
                        paths.add(path)
    return paths


def _job_effectively_moves_sources(settings, job) -> bool:
    from ..workflow_steps import ExecutorSupport

    if not bool(getattr(job, "move_files", False)):
        return False

    target_dir = ExecutorSupport.resolve_copy_destination(settings, job)
    if target_dir is None:
        return False

    source_paths = _job_source_paths(job)
    if source_paths:
        return any(not _same_path(source_path.parent, target_dir) for source_path in source_paths)

    raw_folder = str(getattr(job, "source_folder", "") or "").strip()
    if raw_folder:
        return not _same_path(Path(raw_folder), target_dir)
    return False


def _build_source_move_conflict_warning(settings, active_job_entries) -> str:
    sources: dict[Path, list[tuple[int, int, object, bool]]] = {}
    for order, (index, job) in enumerate(active_job_entries):
        move_sources = _job_effectively_moves_sources(settings, job)
        for source_path in _job_source_paths(job):
            sources.setdefault(source_path, []).append((order, index, job, move_sources))

    conflicts: list[str] = []
    for source_path in sorted(sources, key=lambda path: str(path).lower()):
        claims = sources[source_path]
        if len(claims) < 2:
            continue

        claims.sort(key=lambda item: item[0])
        movers = [claim for claim in claims if claim[3]]
        if len(movers) >= 2:
            first = movers[0]
            second = movers[1]
            conflicts.append(
                f"- {source_path}: {_job_label(first[1], first[2])} und {_job_label(second[1], second[2])} wollen dieselbe Quelldatei verschieben."
            )
            continue

        mover = next((claim for claim in claims if claim[3]), None)
        if mover is None:
            continue
        later_access = next((claim for claim in claims if claim[0] > mover[0]), None)
        if later_access is None:
            continue
        conflicts.append(
            f"- {source_path}: {_job_label(mover[1], mover[2])} verschiebt die Quelle, {_job_label(later_access[1], later_access[2])} greift spaeter erneut darauf zu."
        )

    if not conflicts:
        return ""

    max_items = 8
    visible_conflicts = conflicts[:max_items]
    hidden_count = max(0, len(conflicts) - max_items)
    lines = [
        "Die ausgewaehlten aktiven Workflows verwenden dieselben Quelldateien in einer kritischen Reihenfolge.",
        "",
        *visible_conflicts,
    ]
    if hidden_count:
        lines.append(f"- ... und {hidden_count} weitere Konflikt(e).")
    lines.extend(
        [
            "",
            "Bitte die betroffenen Jobs anpassen und den Start danach erneut ausloesen.",
        ]
    )
    return "\n".join(lines)


def _start_selected_workflows(self):
    from . import QMessageBox

    selected_rows = self._selected_job_rows()
    if not selected_rows:
        active_jobs = [job for job in self._workflow.jobs if job.enabled]
        if not active_jobs:
            QMessageBox.information(self, "Hinweis", "Kein aktiver Workflow vorhanden.")
            return
        self._start_workflow(active_indices={index for index, job in enumerate(self._workflow.jobs) if job.enabled})
        return
    self._start_workflow(active_indices=set(selected_rows))


def _start_all_active_workflows(self):
    self._start_workflow(active_indices={index for index, job in enumerate(self._workflow.jobs) if job.enabled})


def _start_workflow(self, *, active_indices: set[int] | None = None):
    from . import QThread, QMessageBox, WorkflowExecutor

    active_job_entries = [
        (index, job)
        for index, job in enumerate(self._workflow.jobs)
        if job.enabled and (active_indices is None or index in active_indices)
    ]
    active_jobs = [job for _index, job in active_job_entries]
    if not active_jobs:
        QMessageBox.information(self, "Hinweis", "Kein aktiver Workflow in der Auswahl vorhanden.")
        return

    if self._wf_thread and self._wf_thread.isRunning():
        return

    conflict_warning = _build_source_move_conflict_warning(self.settings, active_job_entries)
    if conflict_warning:
        QMessageBox.warning(self, "Quell-Dateikonflikt", conflict_warning)
        return

    resume_existing = False
    if any(
        self._workflow.jobs[index].enabled
        and _job_has_source_config(self._workflow.jobs[index])
        and (self._workflow.jobs[index].resume_status or self._workflow.jobs[index].step_statuses)
        for index in (active_indices or set(range(len(self._workflow.jobs))))
        if 0 <= index < len(self._workflow.jobs)
    ):
        choice = self._ask_resume_behavior()
        if choice == QMessageBox.StandardButton.Cancel:
            return
        resume_existing = choice == QMessageBox.StandardButton.Yes

    if not resume_existing:
        self._reset_status_column(rows={index for index, _job in active_job_entries})
        for job in active_jobs:
            job.resume_status = ""
            job.step_statuses = {}
            job.step_details = {}
            job.progress_pct = 0
            job.overall_progress_pct = 0
            job.current_step_key = ""
            job.transfer_status = ""
            job.transfer_progress_pct = 0
            job.run_started_at = ""
            job.run_finished_at = ""
            job.run_elapsed_seconds = 0.0
        self._workflow.last_run_started_at = ""
        self._workflow.last_run_finished_at = ""
        self._workflow.last_run_elapsed_seconds = 0.0
        self._workflow.started_job_ids = [job.id for job in active_jobs]
        self._workflow.kaderblick_publish_statuses = {}
        self._save_last_workflow()
    else:
        if not self._workflow.started_job_ids:
            self._workflow.started_job_ids = [job.id for job in active_jobs]
        self._append_log("Fortsetzen vorhandener Workflow-Sitzung …")

    self.status_label.setStyleSheet("")
    self._set_busy(True)
    self._append_log(f"\n{'═'*60}\n  ▶ Workflow {'fortgesetzt' if resume_existing else 'gestartet'}\n{'═'*60}")
    self.progress.setMaximum(1)
    self.progress.setValue(0)

    self._workflow.shutdown_after = self._shutdown_cb.isChecked()
    self._workflow.publish_kaderblick_videos = self._publish_kaderblick_cb.isChecked()
    self._wf_thread = QThread(self)
    self._wf_executor = WorkflowExecutor(
        self._workflow,
        self.settings,
        active_indices=active_indices,
        allow_reuse_existing=resume_existing,
    )
    self._wf_executor.moveToThread(self._wf_thread)

    self._wf_thread.started.connect(self._wf_executor.run)
    self._wf_executor.log_message.connect(self._append_log)
    self._wf_executor.job_status.connect(self._on_job_status)
    self._wf_executor.job_progress.connect(self._on_job_progress)
    if hasattr(self._wf_executor, "source_status"):
        self._wf_executor.source_status.connect(self._on_source_status)
    if hasattr(self._wf_executor, "source_progress"):
        self._wf_executor.source_progress.connect(self._on_source_progress)
    self._wf_executor.file_progress.connect(self._on_dl_progress)
    self._wf_executor.overall_progress.connect(self._on_overall_progress)
    self._wf_executor.phase_changed.connect(self._on_phase_changed)
    self._wf_executor.finished.connect(self._on_workflow_done)

    now_monotonic = time.monotonic()
    now_iso = _now_iso()
    self._active_run_indices = {
        index
        for index, job in enumerate(self._workflow.jobs)
        if job.enabled and (active_indices is None or index in active_indices)
    }
    # Maps orig_idx (baked into the executor's active list at start) to the
    # current position in self._workflow.jobs.  Updated in _clear_workflow when
    # the user removes a job while the workflow is still running.
    self._job_orig_to_cur: dict[int, int] = {idx: idx for idx in range(len(self._workflow.jobs))}
    self._wf_start_time = now_monotonic
    self._workflow_run_elapsed_base_seconds = float(self._workflow.last_run_elapsed_seconds or 0.0) if resume_existing else 0.0
    self._workflow_run_started_monotonic = now_monotonic
    if not resume_existing or not self._workflow.last_run_started_at:
        self._workflow.last_run_started_at = now_iso
    self._workflow.last_run_finished_at = ""
    self._job_run_started_monotonic = {}
    self._job_run_elapsed_base_seconds = {}
    for index in self._active_run_indices:
        if not (0 <= index < len(self._workflow.jobs)):
            continue
        job = self._workflow.jobs[index]
        base_seconds = float(job.run_elapsed_seconds or 0.0) if resume_existing else 0.0
        self._job_run_elapsed_base_seconds[job.id] = base_seconds
        if not resume_existing:
            job.run_started_at = ""
        job.run_finished_at = ""
        if not resume_existing:
            job.run_elapsed_seconds = 0.0

    self._wf_thread.start()
    if hasattr(self, "_duration_timer"):
        self._duration_timer.start()
    self._refresh_runtime_durations()


def _cancel_workflow(self):
    from . import QMessageBox

    if not self._wf_executor:
        return

    selected_rows = {
        row for row in self._selected_job_rows()
        if 0 <= row < len(self._workflow.jobs) and self._workflow.jobs[row].enabled
    }
    if selected_rows:
        if len(selected_rows) == 1:
            row = next(iter(selected_rows))
            prompt = f"Soll der ausgewählte Job '{self._workflow.jobs[row].name}' wirklich abgebrochen werden?"
        else:
            prompt = f"Sollen die {len(selected_rows)} ausgewählten Jobs wirklich abgebrochen werden?"
        scope_label = "ausgewählte Jobs"
        active_indices = selected_rows
    else:
        active_indices = {
            index for index, job in enumerate(self._workflow.jobs)
            if job.enabled
        }
        if not active_indices:
            QMessageBox.information(self, "Hinweis", "Kein aktiver Workflow vorhanden.")
            return
        prompt = "Sollen alle laufenden Jobs wirklich abgebrochen werden?"
        scope_label = "alle Jobs"

    choice = QMessageBox.question(
        self,
        "Abbruch bestätigen",
        prompt,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if choice != QMessageBox.StandardButton.Yes:
        return

    self._wf_executor.cancel(active_indices=active_indices)
    self._append_log(f"Abbruch angefordert ({scope_label}) …")


@Slot(int, str)
def _on_job_status(self, orig_idx: int, status: str):
    cur_idx = getattr(self, '_job_orig_to_cur', {}).get(orig_idx, orig_idx)
    if 0 <= cur_idx < self.table.rowCount():
        self._set_row_status(cur_idx, status)
    if 0 <= cur_idx < len(self._workflow.jobs):
        job = self._workflow.jobs[cur_idx]
        job.resume_status = status
        overall_pct = _compute_job_overall_progress(job, status, job.progress_pct)
        job.overall_progress_pct = overall_pct
        if _is_terminal_job_status(status, overall_pct):
            _freeze_job_duration(self, cur_idx)
        elif _is_active_job_status(status):
            _touch_job_duration(self, cur_idx)
        else:
            _pause_job_duration(self, cur_idx)
        item = self.table.item(cur_idx, 4)
        if item is not None:
            item.setToolTip(_format_resume_tooltip(job))
        self._set_row_job_progress(cur_idx, overall_pct)
        self._set_row_duration(cur_idx, job.run_elapsed_seconds)
        self._save_last_workflow()


@Slot(int, int, str)
def _on_job_progress(self, orig_idx: int, pct: int, step_key: str = ""):
    cur_idx = getattr(self, '_job_orig_to_cur', {}).get(orig_idx, orig_idx)
    if step_key and 0 <= cur_idx < len(self._workflow.jobs):
        step_statuses = self._workflow.jobs[cur_idx].step_statuses or {}

        if step_statuses.get(step_key) != "running":
            return
    if 0 <= cur_idx < self.table.rowCount():
        self._set_row_progress(cur_idx, pct)
    if 0 <= cur_idx < len(self._workflow.jobs):
        job = self._workflow.jobs[cur_idx]
        job.progress_pct = pct
        overall_pct = _compute_job_overall_progress(job, job.resume_status or job.status, pct)
        job.overall_progress_pct = overall_pct
        if _is_active_job_status(job.resume_status or job.status):
            _touch_job_duration(self, cur_idx)
        if 0 <= cur_idx < self.table.rowCount():
            self._set_row_job_progress(cur_idx, overall_pct)
            self._set_row_duration(cur_idx, job.run_elapsed_seconds)


@Slot(int, str)
def _on_source_status(self, orig_idx: int, status: str):
    cur_idx = getattr(self, '_job_orig_to_cur', {}).get(orig_idx, orig_idx)
    if 0 <= cur_idx < len(self._workflow.jobs):
        self._workflow.jobs[cur_idx].transfer_status = status


@Slot(int, int)
def _on_source_progress(self, orig_idx: int, pct: int):
    cur_idx = getattr(self, '_job_orig_to_cur', {}).get(orig_idx, orig_idx)
    if 0 <= cur_idx < len(self._workflow.jobs):
        job = self._workflow.jobs[cur_idx]
        job.transfer_progress_pct = pct
        current_step = job.current_step_key or "transfer"
        if current_step in {"", "transfer"}:
            _touch_job_duration(self, cur_idx)
            job.progress_pct = pct
            overall_pct = _compute_job_overall_progress(job, job.resume_status or job.status, pct)
            job.overall_progress_pct = overall_pct
            if 0 <= cur_idx < self.table.rowCount():
                self._set_row_progress(cur_idx, pct)
                self._set_row_job_progress(cur_idx, overall_pct)


@Slot(str, str, float, float, float)
def _on_dl_progress(self, device: str, filename: str, transferred: float, total: float, speed_bps: float):
    if total > 0:
        pct = int(transferred / total * 100)
        info = f"⬇ {device}: {filename}  {pct}%"
        if speed_bps > 0:
            speed_mb = speed_bps / 1048576
            remaining = total - transferred
            eta_s = remaining / speed_bps
            if eta_s >= 3600:
                eta_str = f"{int(eta_s // 3600)}h {int((eta_s % 3600) // 60)}min"
            elif eta_s >= 60:
                eta_str = f"{int(eta_s // 60)}min {int(eta_s % 60)}s"
            else:
                eta_str = f"{int(eta_s)}s"
            info += f"  –  {speed_mb:.1f} MB/s  ETA {eta_str}"
        self.status_label.setText(info)
    else:
        self.status_label.setText(f"⬇ {device}: {filename}")


@Slot(str)
def _on_phase_changed(self, phase: str):
    self.status_label.setText(phase)


@Slot(int, int)
def _on_overall_progress(self, done: int, total: int):
    elapsed = _effective_workflow_elapsed_seconds(self)
    actual_done, actual_total = _workflow_step_progress(
        self._workflow.jobs,
        getattr(self, "_active_run_indices", None),
    )
    display_done = actual_done if actual_total > 0 else done
    display_total = actual_total if actual_total > 0 else max(total, 1)
    self.progress.setMaximum(display_total)
    self.progress.setValue(min(display_done, display_total))
    self.status_label.setText(f"Schritt {display_done}/{display_total}  ({self._format_duration(elapsed)})")
    if hasattr(self, "duration_label"):
        self.duration_label.setText(f"Gesamtdauer: {format_elapsed_seconds(elapsed)}")


@Slot(int, int, int)
def _on_workflow_done(self, ok: int, skip: int, fail: int):
    _snapshot_runtime_durations(self)
    self._workflow.last_run_finished_at = _now_iso()
    if self._wf_thread:
        self._wf_thread.quit()
        self._wf_thread.wait()
        self._wf_thread = None
        self._wf_executor = None
    self._job_orig_to_cur = {}
    if hasattr(self, "_duration_timer"):
        self._duration_timer.stop()

    elapsed = float(self._workflow.last_run_elapsed_seconds or 0.0)
    if fail > 0:
        msg = f"❌ FEHLER: {fail} Fehler, {ok} OK, {skip} übersprungen  ({self._format_duration(elapsed)})"
        self.status_label.setStyleSheet("color: white; background: #c0392b; font-weight: bold; padding: 2px 6px;")
    else:
        msg = f"✅ Fertig: {ok} OK, {skip} übersprungen  ({self._format_duration(elapsed)})"
        self.status_label.setStyleSheet("color: white; background: #27ae60; font-weight: bold; padding: 2px 6px;")
    self._append_log(f"\n{msg}")
    self.status_label.setText(msg)
    if hasattr(self, "duration_label"):
        self.duration_label.setText(f"Gesamtdauer: {('–' if elapsed < 1.0 else format_elapsed_seconds(elapsed))}")
    self._set_busy(False)
    self._workflow.shutdown_after = self._shutdown_cb.isChecked()
    self._save_last_workflow()

    shutdown_requested = self._shutdown_cb.isChecked()
    if shutdown_requested and fail == 0:
        from ..ui.dialogs import ShutdownCountdownDialog

        dialog = ShutdownCountdownDialog(seconds=30, parent=self)
        if dialog.exec():
            cmd = shutdown_command()
            if cmd is None:
                self._append_log("\n⚠ Herunterfahren wird auf diesem Betriebssystem nicht unterstützt.")
            else:
                self._append_log("\n⏻ Rechner wird heruntergefahren …")
                subprocess.Popen(cmd)
        else:
            self._append_log("\n⚠ Herunterfahren durch Benutzer abgebrochen.")
    elif shutdown_requested and fail > 0:
        self._append_log("\n⚠ Herunterfahren übersprungen wegen Fehlern.")


def _refresh_runtime_durations(self):
    _snapshot_runtime_durations(self, persist=False)


def _snapshot_runtime_durations(self, *, persist: bool = False):
    if not hasattr(self, "_active_run_indices"):
        self._active_run_indices = set()
    for orig_idx in sorted(self._active_run_indices):
        if not (0 <= orig_idx < len(self._workflow.jobs)):
            continue
        job = self._workflow.jobs[orig_idx]
        job.run_elapsed_seconds = _effective_job_elapsed_seconds(self, job)
        if 0 <= orig_idx < self.table.rowCount():
            self._set_row_duration(orig_idx, job.run_elapsed_seconds)
            item = self.table.item(orig_idx, 4)
            if item is not None:
                item.setToolTip(_format_resume_tooltip(job))

    self._workflow.last_run_elapsed_seconds = _effective_workflow_elapsed_seconds(self)
    if hasattr(self, "duration_label"):
        self.duration_label.setText(
            f"Gesamtdauer: {('–' if self._workflow.last_run_elapsed_seconds < 1.0 else format_elapsed_seconds(self._workflow.last_run_elapsed_seconds))}"
        )

    if persist:
        self._save_last_workflow()


def _effective_job_elapsed_seconds(self, job) -> float:
    if not getattr(job, "id", ""):
        return float(getattr(job, "run_elapsed_seconds", 0.0) or 0.0)
    base = float(self._job_run_elapsed_base_seconds.get(job.id, getattr(job, "run_elapsed_seconds", 0.0) or 0.0))
    started = self._job_run_started_monotonic.get(job.id)
    if started is None or getattr(job, "run_finished_at", ""):
        return base
    return base + max(0.0, time.monotonic() - started)


def _effective_workflow_elapsed_seconds(self) -> float:
    base = float(getattr(self, "_workflow_run_elapsed_base_seconds", 0.0) or 0.0)
    started = getattr(self, "_workflow_run_started_monotonic", 0.0) or 0.0
    if not started or getattr(self._workflow, "last_run_finished_at", ""):
        return float(getattr(self._workflow, "last_run_elapsed_seconds", base) or base)
    return base + max(0.0, time.monotonic() - started)


def _touch_job_duration(self, orig_idx: int):
    if not (0 <= orig_idx < len(self._workflow.jobs)):
        return
    job = self._workflow.jobs[orig_idx]
    if getattr(job, "run_finished_at", ""):
        return
    if job.id not in self._job_run_started_monotonic:
        self._job_run_elapsed_base_seconds[job.id] = float(job.run_elapsed_seconds or 0.0)
        self._job_run_started_monotonic[job.id] = time.monotonic()
        if not job.run_started_at:
            job.run_started_at = _now_iso()
    job.run_elapsed_seconds = _effective_job_elapsed_seconds(self, job)


def _pause_job_duration(self, orig_idx: int):
    if not (0 <= orig_idx < len(self._workflow.jobs)):
        return
    job = self._workflow.jobs[orig_idx]
    if getattr(job, "run_finished_at", ""):
        return
    started = self._job_run_started_monotonic.get(job.id)
    if started is None:
        return
    base = _effective_job_elapsed_seconds(self, job)
    job.run_elapsed_seconds = base
    self._job_run_elapsed_base_seconds[job.id] = base
    self._job_run_started_monotonic.pop(job.id, None)


def _freeze_job_duration(self, orig_idx: int):
    if not (0 <= orig_idx < len(self._workflow.jobs)):
        return
    _touch_job_duration(self, orig_idx)
    job = self._workflow.jobs[orig_idx]
    base = _effective_job_elapsed_seconds(self, job)
    job.run_elapsed_seconds = base
    job.run_finished_at = _now_iso()
    self._job_run_elapsed_base_seconds[job.id] = base
    self._job_run_started_monotonic.pop(job.id, None)


def _is_terminal_job_status(status: str, overall_pct: int) -> bool:
    if status in {"Übersprungen", "Abgebrochen"}:
        return True
    if status.startswith("Fehler"):
        return True
    if status.startswith("Fertig") and overall_pct >= 100:
        return True
    return False


def _is_active_job_status(status: str) -> bool:
    active_prefixes = (
        "Läuft",
        "Herunterladen",
        "Transfer",
        "Konvertiere",
        "Zusammenführen",
        "Titelkarte",
        "Kompatibilität prüfen",
        "Deep-Scan",
        "Bereinige Altdateien",
        "Repariere",
        "YT-Version",
        "YouTube-Upload",
        "Kaderblick",
    )
    inactive_prefixes = (
        "Transfer OK",
        "Zusammenführen OK",
        "Titelkarte OK",
        "Cleanup OK",
        "Reparatur OK",
        "YT-Version OK",
        "Workflow-Zweig beendet",
        "Validierung ohne gültiges Eingangsartefakt",
        "Datei ist ",
        "Vorhandenes",
    )
    if not status:
        return False
    if status.startswith(inactive_prefixes):
        return False
    return status.startswith(active_prefixes)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
