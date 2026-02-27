"""Worker-Thread für die Konvertierung im Hintergrund."""

import threading
from datetime import datetime

from PySide6.QtCore import QObject, Signal, Slot

from .settings import AppSettings
from .converter import ConvertJob, run_convert
from .merge import merge_halves
from .youtube import get_youtube_service, upload_to_youtube


class ConvertWorker(QObject):
    """Läuft in einem QThread und verarbeitet die Jobliste."""

    log_message = Signal(str)
    job_updated = Signal()
    progress = Signal(int, int)          # (done, total)
    file_progress = Signal(int, int)     # (job_index, percent)
    finished = Signal(int, int, int)     # (ok, skip, fail)

    def __init__(self, jobs: list[ConvertJob], settings: AppSettings):
        super().__init__()
        self._jobs = jobs
        self._settings = settings
        self._cancel_event = threading.Event()

    def cancel(self):
        """Setzt das Cancel-Flag – run_ffmpeg killt den Prozess."""
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @Slot()
    def run(self):
        done = 0
        total = len(self._jobs)
        yt_service = None

        # YouTube-Service einmalig am Anfang erstellen
        if self._settings.youtube.upload_to_youtube:
            self.log_message.emit("YouTube-Anmeldung …")
            yt_service = get_youtube_service(
                log_callback=self.log_message.emit)
            if not yt_service:
                self.log_message.emit(
                    "⚠ YouTube-Upload deaktiviert (Anmeldung fehlgeschlagen)")

        for job_idx, job in enumerate(self._jobs):
            if self.cancelled:
                self.log_message.emit("Abgebrochen.")
                break

            job.status = "Läuft"
            job.progress_pct = 0
            self.file_progress.emit(job_idx, 0)
            self.job_updated.emit()

            start_str = datetime.now().strftime("%H:%M:%S")
            self.log_message.emit(
                f"\n═══ [{done + 1}/{total}] {job.source_path.name} ═══")
            self.log_message.emit(f"Gestartet: {start_str}")

            def _progress_cb(pct, _idx=job_idx, _job=job):
                _job.progress_pct = pct
                self.file_progress.emit(_idx, pct)

            ok = run_convert(
                job, self._settings,
                cancel_flag=self._cancel_event,
                log_callback=self.log_message.emit,
                progress_callback=_progress_cb)

            if self.cancelled:
                self.log_message.emit("Abgebrochen.")
                break

            # YouTube-Upload nach erfolgreicher Konvertierung
            if ok and yt_service and job.status == "Fertig":
                upload_to_youtube(
                    job, self._settings, yt_service,
                    log_callback=self.log_message.emit)

            done += 1
            self.progress.emit(done, total)
            self.job_updated.emit()

        ok_count = sum(1 for j in self._jobs if j.status == "Fertig")
        skip_count = sum(1 for j in self._jobs if j.status == "Übersprungen")
        fail_count = sum(1 for j in self._jobs if j.status == "Fehler")

        # ── Optional: Halbzeiten zusammenführen ──
        if (self._settings.video.merge_halves
                and not self.cancelled
                and ok_count >= 2):
            self.log_message.emit(
                "\n═══════════════════════════════════════")
            self.log_message.emit("  Halbzeiten zusammenführen …")
            self.log_message.emit(
                "═══════════════════════════════════════")
            merge_halves(
                self._jobs, self._settings,
                cancel_flag=self._cancel_event,
                log_callback=self.log_message.emit)

        self.finished.emit(ok_count, skip_count, fail_count)
