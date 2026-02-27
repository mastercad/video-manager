"""Dialog zum Herunterladen von Videos von Raspberry Pi Kameras."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLineEdit, QPushButton, QLabel, QTextEdit, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QCheckBox, QSizePolicy, QWidget,
)

from .settings import AppSettings, DeviceSettings
from .download_worker import DownloadWorker


# =============================================================================
#  Download-Dialog
# =============================================================================

class DownloadDialog(QDialog):
    """
    Dialog zum Herunterladen von Raspberry Pi Kamera-Videos.

    Bezieht Gerätekonfiguration aus AppSettings.cameras.
    Nach dem Download sind die heruntergeladenen .mjpg-Pfade in
    ``downloaded_mjpg_files`` verfügbar.
    Wenn ``auto_convert`` in den Kamera-Einstellungen aktiv ist, schließt
    sich der Dialog nach dem Download automatisch mit Accepted.
    """

    def __init__(self, parent=None, settings: Optional[AppSettings] = None):
        super().__init__(parent)
        self.setWindowTitle("Videos herunterladen (Raspberry Pi)")
        self.resize(760, 620)
        self.setMinimumSize(600, 500)

        self._settings = settings
        self._worker: Optional[DownloadWorker] = None
        self._thread: Optional[QThread] = None
        self.downloaded_mjpg_files: list[str] = []

        self._build_ui()
        self._reload_from_settings()

    # ── UI aufbauen ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setSpacing(8)

        # ── Zielverzeichnis ──────────────────────────────────────────────
        dest_group = QGroupBox("Zielverzeichnis")
        dest_form = QFormLayout()
        dest_row = QHBoxLayout()
        self._dest_edit = QLineEdit()
        self._dest_edit.setPlaceholderText("(aus Kamera-Einstellungen übernommen)")
        dest_row.addWidget(self._dest_edit)
        dest_browse = QPushButton("Durchsuchen …")
        dest_browse.clicked.connect(self._browse_destination)
        dest_row.addWidget(dest_browse)
        dest_form.addRow("Zielordner:", dest_row)
        dest_group.setLayout(dest_form)
        main.addWidget(dest_group)

        # ── Geräte ───────────────────────────────────────────────────────
        dev_group = QGroupBox("Geräte")
        dev_layout = QVBoxLayout()

        dev_btn_row = QHBoxLayout()
        self._settings_btn = QPushButton("Kamera-Einstellungen …")
        self._settings_btn.clicked.connect(self._open_camera_settings)
        dev_btn_row.addWidget(self._settings_btn)
        dev_btn_row.addStretch()
        dev_layout.addLayout(dev_btn_row)

        self._device_table = QTableWidget(0, 5)
        self._device_table.setHorizontalHeaderLabels(
            ["", "Name", "IP", "Benutzer", "Auth"])
        hdr = self._device_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.resizeSection(0, 30)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        self._device_table.verticalHeader().setVisible(False)
        self._device_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._device_table.setSelectionMode(QTableWidget.NoSelection)
        self._device_table.setMaximumHeight(140)
        dev_layout.addWidget(self._device_table)
        dev_group.setLayout(dev_layout)
        main.addWidget(dev_group)

        # ── Optionen ─────────────────────────────────────────────────────
        opt_group = QGroupBox("Optionen")
        opt_layout = QVBoxLayout()

        self._auto_convert_chk = QCheckBox(
            "Nach dem Download automatisch konvertieren")
        self._auto_convert_chk.setToolTip(
            "Heruntergeladene .mjpg-Dateien werden nach dem Download "
            "automatisch zur Konvertierungsliste hinzugefügt und gestartet.")
        opt_layout.addWidget(self._auto_convert_chk)

        self._delete_chk = QCheckBox(
            "Quelldateien nach erfolgreichem Download löschen")
        self._delete_chk.setToolTip(
            "Löscht .mjpg und .wav auf dem Raspberry Pi, sobald der "
            "Download und die Größenprüfung erfolgreich waren.")
        opt_layout.addWidget(self._delete_chk)

        opt_group.setLayout(opt_layout)
        main.addWidget(opt_group)

        # ── Fortschritt ──────────────────────────────────────────────────
        progress_group = QGroupBox("Fortschritt")
        progress_layout = QVBoxLayout()
        self._current_label = QLabel("—")
        self._current_label.setWordWrap(True)
        progress_layout.addWidget(self._current_label)
        self._file_progress = QProgressBar()
        self._file_progress.setTextVisible(True)
        self._file_progress.setMaximumHeight(18)
        self._file_progress.setValue(0)
        self._file_progress.setFormat("%v / %m Bytes  (%p%)")
        progress_layout.addWidget(self._file_progress)
        progress_group.setLayout(progress_layout)
        main.addWidget(progress_group)

        # ── Log ─────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Monospace", 9))
        self._log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main.addWidget(self._log, stretch=1)

        # ── Schaltflächen ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("▶  Download starten")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._start_download)
        self._start_btn.setEnabled(False)
        btn_row.addWidget(self._start_btn)
        self._cancel_btn = QPushButton("■  Abbrechen")
        self._cancel_btn.clicked.connect(self._cancel_download)
        self._cancel_btn.setEnabled(False)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        self._close_btn = QPushButton("Schließen")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)
        main.addLayout(btn_row)

    # ── Einstellungen laden ───────────────────────────────────────────────────

    def _reload_from_settings(self) -> None:
        if not self._settings:
            return
        cam = self._settings.cameras
        if not self._dest_edit.text() and cam.destination:
            self._dest_edit.setText(cam.destination)
        self._auto_convert_chk.setChecked(cam.auto_convert)
        self._delete_chk.setChecked(cam.delete_after_download)
        self._populate_device_table()
        self._start_btn.setEnabled(bool(cam.devices))

    def _populate_device_table(self) -> None:
        if not self._settings:
            return
        devices = self._settings.cameras.devices
        self._device_table.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            chk = QCheckBox()
            chk.setChecked(True)
            chk_container = QWidget()
            chk_layout = QHBoxLayout(chk_container)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self._device_table.setCellWidget(row, 0, chk_container)
            self._device_table.setItem(row, 1, QTableWidgetItem(dev.name))
            self._device_table.setItem(row, 2, QTableWidgetItem(dev.ip))
            self._device_table.setItem(row, 3, QTableWidgetItem(dev.username))
            if dev.ssh_key:
                auth = f"Key: {dev.ssh_key}"
            elif dev.password:
                auth = "Passwort gesetzt"
            else:
                auth = "—"
            self._device_table.setItem(row, 4, QTableWidgetItem(auth))

    def _selected_devices(self) -> list[DeviceSettings]:
        if not self._settings:
            return []
        selected = []
        for row in range(self._device_table.rowCount()):
            container = self._device_table.cellWidget(row, 0)
            if container:
                chk = container.findChild(QCheckBox)
                if chk and chk.isChecked():
                    selected.append(self._settings.cameras.devices[row])
        return selected

    # ── Navigation ───────────────────────────────────────────────────────────

    def _browse_destination(self) -> None:
        start = self._dest_edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Zielverzeichnis wählen", start)
        if path:
            self._dest_edit.setText(path)

    def _open_camera_settings(self) -> None:
        # Import hier um Zirkel zu vermeiden
        from .dialogs import CameraSettingsDialog
        if not self._settings:
            return
        dlg = CameraSettingsDialog(self, self._settings)
        if dlg.exec():
            self._settings.save()
            self._reload_from_settings()
            self._log.append("Kamera-Einstellungen aktualisiert.")

    # ── Download ─────────────────────────────────────────────────────────────

    def _start_download(self) -> None:
        if not self._settings:
            return
        devices = self._selected_devices()
        if not devices:
            QMessageBox.information(self, "Hinweis",
                                    "Bitte mindestens ein Gerät auswählen.")
            return

        dest_override = self._dest_edit.text().strip()
        if not dest_override and not self._settings.cameras.destination:
            QMessageBox.warning(self, "Kein Zielverzeichnis",
                                "Bitte ein Zielverzeichnis angeben.")
            return

        # Einstellungen aus UI übernehmen und speichern
        cam = self._settings.cameras
        cam.auto_convert = self._auto_convert_chk.isChecked()
        cam.delete_after_download = self._delete_chk.isChecked()
        if dest_override:
            cam.destination = dest_override
        self._settings.save()

        self._log.clear()
        self.downloaded_mjpg_files.clear()
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._settings_btn.setEnabled(False)

        self._thread = QThread(self)
        self._worker = DownloadWorker(
            config=cam,
            devices=devices,
            destination_override=dest_override,
            delete_after_download=cam.delete_after_download,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log_message.connect(self._on_log)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.device_done.connect(self._on_device_done)
        self._worker.finished.connect(self._on_finished)

        self._thread.start()

    def _cancel_download(self) -> None:
        if self._worker:
            self._worker.cancel()
        self._cancel_btn.setEnabled(False)
        self._log.append("Abbruch angefordert …")

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_log(self, msg: str) -> None:
        self._log.append(msg)

    @Slot(str, str, int, int)
    def _on_file_progress(self, device: str, filename: str,
                          transferred: int, total: int) -> None:
        self._current_label.setText(f"{device} – {filename}")
        self._file_progress.setMaximum(max(total, 1))
        self._file_progress.setValue(transferred)

    @Slot(str, int)
    def _on_device_done(self, device_name: str, count: int) -> None:
        self._log.append(f"  {device_name}: {count} Aufnahme(n) abgeschlossen")

    @Slot(int, list)
    def _on_finished(self, total: int, mjpg_paths: list) -> None:
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._settings_btn.setEnabled(True)
        self._current_label.setText("—")
        self._file_progress.setValue(0)
        self._file_progress.setMaximum(100)

        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

        self.downloaded_mjpg_files = mjpg_paths

        if total == 0:
            self._log.append("\nKeine Dateien heruntergeladen.")
            return

        self._log.append(
            f"\nFertig. {total} Aufnahme(n) heruntergeladen/vorhanden.")

        if self._auto_convert_chk.isChecked() and mjpg_paths:
            self._log.append(
                f"Starte Konvertierung von {len(mjpg_paths)} Datei(en) …")
            # Kurze Pause damit der Log sichtbar wird, dann schließen
            self.accept()

    # ── Dialog schließen ──────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            reply = QMessageBox.question(
                self, "Download läuft",
                "Der Download läuft noch. Wirklich schließen?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            if self._worker:
                self._worker.cancel()
            if self._thread:
                self._thread.quit()
                self._thread.wait(5000)
        event.accept()
