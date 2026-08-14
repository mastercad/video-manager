"""Workflow management actions for the main window."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from ..workflow import FileEntry, Workflow, WorkflowJob
from .helpers import _job_has_source_config, _repair_restored_workflow


def _apply_add_files(self, paths: list[str]):
    files: list[FileEntry] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            files.append(FileEntry(source_path=str(path)))
        elif path.is_dir():
            for file in sorted(path.rglob("*")):
                if file.is_file():
                    files.append(FileEntry(source_path=str(file)))
        else:
            print(f"[WARN] --add: Nicht gefunden: {path}")
    if files:
        self._workflow.jobs = [WorkflowJob(name=self._workflow.name or "Workflow", source_mode="files", files=files)]
        self._refresh_table()
        self._update_count()
        self._append_log(f"CLI: {len(files)} Datei(en) aus --add in aktuellen Workflow geladen")


def _apply_workflow(self, wf_path: str):
    path = Path(wf_path)
    if not path.exists():
        self._append_log(f"[FEHLER] --workflow: Datei nicht gefunden: {path}")
        return
    try:
        workflow = Workflow.load(path)
    except Exception as exc:
        self._append_log(f"[FEHLER] --workflow: Laden fehlgeschlagen: {exc}")
        return
    self._workflow = workflow
    self._sync_shutdown_checkbox()
    self._sync_publish_kaderblick_checkbox()
    self._refresh_table()
    self._update_count()
    self._append_log(f"CLI: Workflow geladen aus {path.name}")
    self._start_workflow()


def _new_workflow(self):
    from . import JobWorkflowDialog
    from ..ui.job_editor import JobEditorDialog

    new_job = JobEditorDialog._create_default_job(self.settings)
    new_job.name = "Neuer Workflow"
    dialog = JobWorkflowDialog(self, new_job, allow_edit=True, settings=self.settings, allow_wizard_shortcut=False)
    if dialog.exec() and dialog.changed:
        resolved_name = self._resolve_job_name(new_job)
        if resolved_name is None:
            return
        new_job.name = resolved_name
        self._workflow.jobs.append(new_job)
        if not self._workflow.name:
            self._workflow.name = resolved_name or "workflow"
        self._refresh_table()
        self._update_count()
        self._persist_workflow_state()


def _add_job(self):
    self._new_workflow()


def _add_all_cameras(self):
    self._new_workflow()


def _remove_selected(self):
    self._clear_workflow()


def _clear_workflow(self):
    from . import QMessageBox

    row = self._selected_job_row()
    if 0 <= row < len(self._workflow.jobs):
        if QMessageBox.question(
            self,
            "Bestätigung",
            "Ausgewählten Workflow aus dem Fenster entfernen?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            del self._workflow.jobs[row]
            # Keep the executor's orig_idx → current-index mapping in sync so
            # that status/progress signals still reach the correct row/job after
            # the deletion shifts higher indices down by one.
            if hasattr(self, "_job_orig_to_cur") and self._job_orig_to_cur:
                self._job_orig_to_cur = {
                    orig: (cur - 1 if cur > row else cur)
                    for orig, cur in self._job_orig_to_cur.items()
                    if cur != row
                }
            if hasattr(self, "_active_run_indices"):
                self._active_run_indices = {
                    idx - (1 if idx > row else 0)
                    for idx in self._active_run_indices
                    if idx != row
                }
            if not self._workflow.jobs:
                self._workflow.name = ""
            self._refresh_table()
            self._update_count()
            self._persist_workflow_state()


def _selected_job_row(self) -> int:
    row = self.table.currentRow()
    if 0 <= row < len(self._workflow.jobs):
        return row
    return 0 if len(self._workflow.jobs) == 1 else -1


def _selected_job_rows(self) -> list[int]:
    rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
    return [row for row in rows if 0 <= row < len(self._workflow.jobs)]


def _open_job_workflow(self, row: int | None = None):
    from . import JobWorkflowDialog

    row = self._selected_job_row() if row is None else row
    if not (0 <= row < len(self._workflow.jobs)):
        return
    job = self._workflow.jobs[row]
    dialog = JobWorkflowDialog(self, job, allow_edit=True, settings=self.settings)
    if dialog.exec():
        if dialog.changed:
            self._refresh_table()
            self._persist_workflow_state()
        if dialog.edit_requested:
            self._edit_job(row)


def _edit_job(self, row: int | None = None):
    from ..ui.job_editor import JobEditorDialog

    row = self._selected_job_row() if row is None else row
    if not (0 <= row < len(self._workflow.jobs)):
        self._new_workflow()
        return
    dialog = JobEditorDialog(self, self.settings, job=self._workflow.jobs[row])
    if dialog.exec():
        resolved_name = self._resolve_job_name(dialog.result_job, exclude_row=row)
        if resolved_name is None:
            return
        dialog.result_job.name = resolved_name
        self._workflow.jobs[row] = dialog.result_job
        if row == 0:
            self._workflow.name = dialog.result_job.name or self._workflow.name
        self._refresh_table()
        self._persist_workflow_state()


def _duplicate_job(self):
    from ..workflow import increment_workflow_name

    row = self._selected_job_row()
    if not (0 <= row < len(self._workflow.jobs)):
        return
    clone = copy.deepcopy(self._workflow.jobs[row])
    clone.id = uuid.uuid4().hex[:8]
    clone.name = increment_workflow_name(clone.name or "Workflow", [job.name for job in self._workflow.jobs])
    resolved_name = self._resolve_job_name(clone)
    if resolved_name is None:
        return
    clone.name = resolved_name
    self._workflow.jobs.insert(row + 1, clone)
    self._refresh_table()
    self.table.selectRow(row + 1)
    self._update_count()
    self._persist_workflow_state()


def _existing_job_names(self, *, exclude_row: int | None = None) -> list[str]:
    from ..workflow import normalize_workflow_name

    names: list[str] = []
    for index, job in enumerate(self._workflow.jobs):
        if exclude_row is not None and index == exclude_row:
            continue
        normalized = normalize_workflow_name(job.name)
        if normalized:
            names.append(normalized)
    return names


def _resolve_job_name(self, job: WorkflowJob, *, exclude_row: int | None = None) -> str | None:
    from . import QMessageBox
    from ..workflow import increment_workflow_name, normalize_workflow_name

    desired_name = normalize_workflow_name(job.name) or "Workflow"
    existing = self._existing_job_names(exclude_row=exclude_row)
    if desired_name not in existing:
        return desired_name

    incremented = increment_workflow_name(desired_name, existing)
    box = QMessageBox(self)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Workflow-Name bereits vorhanden")
    box.setText(f"Es gibt bereits einen Workflow mit dem Namen '{desired_name}'.")
    box.setInformativeText(f"Du kannst den Namen beibehalten oder automatisch auf '{incremented}' erhöhen.")
    keep_button = box.addButton("Namen behalten", QMessageBox.ButtonRole.AcceptRole)
    increment_button = box.addButton("Inkrementieren", QMessageBox.ButtonRole.ActionRole)
    cancel_button = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(increment_button)
    box.exec()

    clicked = box.clickedButton()
    if clicked is increment_button:
        return incremented
    if clicked is keep_button:
        return desired_name
    if clicked is cancel_button:
        return None
    return None


def _load_workflow(self):
    from . import QMessageBox, WORKFLOW_DIR

    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    path, _ = QFileDialog.getOpenFileName(self, "Workflow laden", str(WORKFLOW_DIR), "JSON-Dateien (*.json);;Alle Dateien (*)")
    if path:
        try:
            self._workflow = Workflow.load(Path(path))
            self._sync_shutdown_checkbox()
            self._sync_publish_kaderblick_checkbox()
            self._refresh_table()
            self._update_count()
            self._append_log(f"Workflow geladen: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Fehler beim Laden", str(exc))


def _save_workflow(self):
    from . import QMessageBox, WORKFLOW_DIR

    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    path, _ = QFileDialog.getSaveFileName(self, "Workflow speichern", str(WORKFLOW_DIR / "workflow.json"), "JSON-Dateien (*.json);;Alle Dateien (*)")
    if path:
        try:
            self._workflow.save(Path(path))
            self._append_log(f"Workflow gespeichert: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Fehler beim Speichern", str(exc))


def _save_last_workflow(self):
    try:
        snapshot = getattr(self, "_snapshot_runtime_durations", None)
        if callable(snapshot):
            snapshot(persist=False)
        self._workflow.save_as_last()
    except Exception:
        pass


def _persist_workflow_state(self):
    self._save_last_workflow()


def _restore_last_workflow(self):
    restored = Workflow.load_last()
    if restored is not None:
        try:
            restored, repaired, dropped = _repair_restored_workflow(restored, None)
            self._workflow = restored
            self._sync_shutdown_checkbox()
            self._sync_publish_kaderblick_checkbox()
            self._refresh_table()
            self._update_count()
            message = "Letzter Workflow wiederhergestellt"
            if repaired:
                message += " | Workflow aus letzter Sicherung repariert"
            if dropped:
                message += " | Resume-Status ohne gültige Konfiguration verworfen"
            self._append_log(message)
        except Exception:
            pass


def _sync_shutdown_checkbox(self):
    checked = bool(getattr(self._workflow, "shutdown_after", False))
    self._shutdown_cb.blockSignals(True)
    self._shutdown_cb.setChecked(checked)
    self._shutdown_cb.blockSignals(False)
    self._shutdown_cb.setText("⚠ Herunterfahren: AN" if checked else "Herunterfahren: AUS")


def _sync_publish_kaderblick_checkbox(self):
    checked = bool(getattr(self._workflow, "publish_kaderblick_videos", False))
    self._publish_kaderblick_cb.blockSignals(True)
    self._publish_kaderblick_cb.setChecked(checked)
    self._publish_kaderblick_cb.blockSignals(False)
    self._publish_kaderblick_cb.setText("Kaderblick-Publish: AN" if checked else "Kaderblick-Publish: AUS")


def _on_shutdown_toggled(self, checked: bool):
    self._shutdown_cb.setText("⚠ Herunterfahren: AN" if checked else "Herunterfahren: AUS")
    self._workflow.shutdown_after = bool(checked)
    self._save_last_workflow()


def _on_publish_kaderblick_toggled(self, checked: bool):
    self._publish_kaderblick_cb.setText("Kaderblick-Publish: AN" if checked else "Kaderblick-Publish: AUS")
    self._workflow.publish_kaderblick_videos = bool(checked)
    self._save_last_workflow()


def _has_resumeable_jobs(self) -> bool:
    return any(job.enabled and _job_has_source_config(job) and (job.resume_status or job.step_statuses) for job in self._workflow.jobs)


def _ask_resume_behavior(self):
    from . import QMessageBox

    box = QMessageBox(self)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Workflow fortsetzen?")
    box.setText(
        "Es gibt gespeicherte Fortschrittsdaten eines vorherigen Laufs.\n"
        "Soll der Workflow fortgesetzt oder neu gestartet werden?"
    )
    continue_button = box.addButton("Fortsetzen", QMessageBox.ButtonRole.AcceptRole)
    restart_button = box.addButton("Neu starten", QMessageBox.ButtonRole.DestructiveRole)
    cancel_button = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(continue_button)
    box.exec()

    clicked = box.clickedButton()
    if clicked is continue_button:
        return QMessageBox.StandardButton.Yes
    if clicked is restart_button:
        return QMessageBox.StandardButton.No
    if clicked is cancel_button:
        return QMessageBox.StandardButton.Cancel
    return QMessageBox.StandardButton.Cancel
