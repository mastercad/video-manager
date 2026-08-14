"""Workflow-Executor mit konservativer Pipeline-Ausführung."""

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from ...settings import AppSettings
from ...workflow import Workflow
from ...workflow_steps import (
    ConvertStep,
    ExecutorSupport,
    MergeGroupStep,
    OutputStepStack,
    TransferStep,
)
from .pipeline import WorkflowExecutorPipelineMixin
from .support import WorkflowExecutorSupportMixin


class _JobCancelFlag:
    def __init__(self, executor: "WorkflowExecutor", orig_idx: int):
        self._executor = executor
        self._orig_idx = orig_idx

    def is_set(self) -> bool:
        return self._executor._cancel.is_set() or self._orig_idx in self._executor._cancelled_indices

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        if timeout is not None and timeout <= 0:
            return self.is_set()

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.is_set():
                return True
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.is_set()
                wait_time = min(0.05, remaining)
            else:
                wait_time = 0.05
            self._executor._cancel.wait(wait_time)


class WorkflowExecutor(WorkflowExecutorPipelineMixin, WorkflowExecutorSupportMixin, QObject):
    """Führt einen Workflow zweiphasig aus."""

    log_message = Signal(str)
    job_status = Signal(int, str)
    job_progress = Signal(int, int, str)
    overall_progress = Signal(int, int)
    file_progress = Signal(str, str, float, float, float)
    phase_changed = Signal(str)
    finished = Signal(int, int, int)

    source_status = Signal(int, str)
    source_progress = Signal(int, int)
    convert_progress = Signal(int, int)

    def __init__(
        self,
        workflow: Workflow,
        settings: AppSettings,
        *,
        active_indices: set[int] | None = None,
        allow_reuse_existing: bool = True,
    ):
        super().__init__()
        from . import download_device, run_concat, run_convert, run_youtube_convert

        self._workflow = workflow
        self._settings = settings
        self._active_indices = set(active_indices or set())
        self._allow_reuse_existing = allow_reuse_existing
        self._cancel = threading.Event()
        self._owner_thread_id = threading.get_ident()
        self._pipeline_owner_thread_id = self._owner_thread_id
        self._convert_func = run_convert
        self._concat_func = run_concat
        self._youtube_convert_func = run_youtube_convert
        self._download_func = download_device
        self._support = ExecutorSupport()
        self._transfer_step = TransferStep()
        self._convert_step = ConvertStep()
        self._merge_step = MergeGroupStep()
        self._output_step_stack = OutputStepStack()
        self._transfer_fail = 0
        self._cancelled_indices: set[int] = set()
        self._job_cancel_flags: dict[int, _JobCancelFlag] = {}

    def cancel(self, active_indices: set[int] | None = None) -> None:
        if active_indices is None:
            self._cancel.set()
            indices = set(self._active_indices) if self._active_indices else {
                index for index, job in enumerate(self._workflow.jobs) if job.enabled
            }
        else:
            indices = {index for index in active_indices if 0 <= index < len(self._workflow.jobs)}
        for orig_idx in indices:
            self._request_job_cancel(orig_idx)

    def _request_job_cancel(self, orig_idx: int) -> None:
        if not (0 <= orig_idx < len(self._workflow.jobs)):
            return
        if orig_idx in self._cancelled_indices:
            return
        self._cancelled_indices.add(orig_idx)
        self._job_cancel_flags.setdefault(orig_idx, _JobCancelFlag(self, orig_idx))
        self._mark_job_cancelled(orig_idx)

    def _is_job_cancelled(self, orig_idx: int) -> bool:
        return self._cancel.is_set() or orig_idx in self._cancelled_indices

    def _cancel_flag_for_job(self, orig_idx: int):
        return self._job_cancel_flags.setdefault(orig_idx, _JobCancelFlag(self, orig_idx))

    def _mark_job_cancelled(self, orig_idx: int) -> None:
        if not (0 <= orig_idx < len(self._workflow.jobs)):
            return
        job = self._workflow.jobs[orig_idx]

        # Nicht beeinflussen wenn der Job bereits in einem terminalen Zustand ist
        # (Fertig, Fehler, Übersprungen, bereits abgebrochen) oder noch nie gestartet wurde.
        resume = str(getattr(job, "resume_status", "") or "")
        step_statuses = job.step_statuses if isinstance(job.step_statuses, dict) else {}
        has_started = bool(step_statuses) or bool(resume)
        is_terminal = (
            resume.startswith("Fertig")
            or resume.startswith("Fehler")
            or resume == "Übersprungen"
            or resume.lower().endswith("abgebrochen")
        )
        if not has_started or is_terminal:
            return

        step_key = str(getattr(job, "current_step_key", "") or "")
        if step_key and str(step_statuses.get(step_key, "") or "") == "running":
            self._set_step_status(job, step_key, "cancelled")
            self._set_step_detail(job, step_key, "Durch Benutzer abgebrochen")
            self._set_job_status(orig_idx, f"{_step_label(step_key)} abgebrochen")
        else:
            self._set_job_status(orig_idx, "Job abgebrochen")
        self.job_progress.emit(orig_idx, 0, step_key)

    def _handle_direct_files(self, job):
        return self._transfer_step._files_step.execute(self, 0, job)

    def _scan_folder(self, job):
        return self._transfer_step._folder_scan_step.execute(self, 0, job)

    def _download_from_pi(self, orig_idx, job):
        return self._transfer_step._pi_download_step.execute(self, orig_idx, job)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @Slot()
    def run(self) -> None:
        active = [
            (index, job)
            for index, job in enumerate(self._workflow.jobs)
            if job.enabled and (not self._active_indices or index in self._active_indices)
        ]
        if not active:
            self.finished.emit(0, 0, 0)
            return

        ok, skip, fail = self._run_pipelined(active)
        if self._cancel.is_set():
            self.log_message.emit("Phase 1 abgebrochen.")
            self.finished.emit(0, 0, 0)
            return

        publish_fail = self._publish_completed_kaderblick_games(active, fail)
        total_fail = fail + publish_fail
        icon = "✅" if total_fail == 0 else "❌"
        self.log_message.emit(f"\n{icon} Fertig: {ok} OK, {skip} übersprungen, {total_fail} Fehler")
        self.finished.emit(ok, skip, total_fail)


def _step_label(step_key: str) -> str:
    labels = {
        "transfer": "Transfer",
        "convert": "Konvertierung",
        "merge": "Zusammenführen",
        "titlecard": "Titelkarte",
        "validate_surface": "Kompatibilität prüfen",
        "validate_deep": "Deep-Scan",
        "cleanup": "Cleanup",
        "repair": "Reparatur",
        "yt_version": "YT-Version",
        "stop": "Workflow-Zweig",
        "youtube_upload": "YouTube-Upload",
        "kaderblick": "Kaderblick",
    }
    return labels.get(step_key, "Job")
