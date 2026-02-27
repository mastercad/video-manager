"""QThread-Worker für den Download von Raspberry Pi Videos."""

import threading

from PySide6.QtCore import QObject, Signal, Slot

from .downloader import download_device
from .settings import DeviceSettings, CameraSettings


class DownloadWorker(QObject):
    """
    Läuft in einem QThread und lädt Videos von allen konfigurierten
    Raspberry Pi Geräten herunter.

    Signale
    -------
    log_message(str)
        Log-Ausgabe für die GUI.
    file_progress(device_name, filename, transferred, total)
        Fortschritt der aktuellen Datei (Bytes).
    device_done(device_name, count)
        Gerät fertig, count = Anzahl der heruntergeladenen Aufnahmen.
    finished(total_count, mjpg_paths)
        Alle Geräte abgearbeitet.
        mjpg_paths = Liste der heruntergeladenen .mjpg-Dateien (für Auto-Konvertierung).
    """

    log_message   = Signal(str)
    file_progress = Signal(str, str, int, int)   # (device, filename, transferred, total)
    device_done   = Signal(str, int)              # (device_name, downloaded_count)
    finished      = Signal(int, list)            # (total_count, list[str] mjpg_paths)

    def __init__(
        self,
        config: CameraSettings,
        devices: list | None = None,
        destination_override: str = "",
        delete_after_download: bool = False,
    ):
        super().__init__()
        self._config = config
        self._devices: list[DeviceSettings] = (
            devices if devices is not None else config.devices
        )
        self._dest_override = destination_override
        self._delete = delete_after_download
        self._cancel = threading.Event()

    # ── Steuerung ──────────────────────────────────────────────

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ── Hauptroutine ───────────────────────────────────────────

    @Slot()
    def run(self) -> None:
        all_mjpg_paths: list[tuple[str, str]] = []  # (device_name, path)

        for device in self._devices:
            if self._cancel.is_set():
                self.log_message.emit("Download abgebrochen.")
                break

            results = download_device(
                device=device,
                config=self._config,
                log_cb=self.log_message.emit,
                progress_cb=self._on_progress,
                cancel_flag=self._cancel,
                destination_override=self._dest_override,
                delete_after_download=self._delete,
            )
            # results: list of (local_dir, base, mjpg_path)
            for r in results:
                all_mjpg_paths.append((device.name, r[2]))
            self.device_done.emit(device.name, len(results))

        self.finished.emit(len(all_mjpg_paths), all_mjpg_paths)

    # ── Intern ─────────────────────────────────────────────────

    def _on_progress(
        self,
        device_name: str,
        filename: str,
        transferred: int,
        total: int,
    ) -> None:
        self.file_progress.emit(device_name, filename, transferred, total)

