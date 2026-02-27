"""Haupt-GUI des MJPEG Converters (QMainWindow)."""

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtGui import QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QToolBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QProgressBar, QLabel,
    QFileDialog, QMessageBox, QSplitter, QAbstractItemView,
    QTreeView, QListView,
)

from .settings import AppSettings, SESSION_FILE
from .converter import ConvertJob, save_jobs, load_jobs
from .worker import ConvertWorker
from .download_worker import DownloadWorker
from .delegates import ProgressDelegate
from .dialogs import (
    VideoSettingsDialog, AudioSettingsDialog,
    YouTubeSettingsDialog, JobEditDialog,
    CameraSettingsDialog,
)
from .download_dialog import DownloadDialog


class ConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MJPEG Converter")
        self.resize(960, 640)
        self.setMinimumSize(720, 460)

        # Icon setzen (Fenster + Taskleiste)
        _icon_path = Path(__file__).resolve().parent.parent / "assets" / "icon.svg"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

        self.settings = AppSettings.load()
        self.jobs: list[ConvertJob] = []
        self._worker: Optional[ConvertWorker] = None
        self._thread: Optional[QThread] = None
        self._dl_worker: Optional[DownloadWorker] = None
        self._dl_thread: Optional[QThread] = None
        self._file_start_time: float = 0.0
        self._job_start_time: float = 0.0

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # Session wiederherstellen
        if self.settings.restore_session:
            self._restore_session()

    # ══════════════════════════════════════════════════════════
    #  Menü
    # ══════════════════════════════════════════════════════════
    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&Datei")
        a = file_menu.addAction("Dateien hinzufügen …")
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(self._add_files)
        a = file_menu.addAction("Ordner hinzufügen …")
        a.setShortcut(QKeySequence("Ctrl+D"))
        a.triggered.connect(self._add_directory)
        a = file_menu.addAction("Pi-Download hinzufügen")
        a.setShortcut(QKeySequence("Ctrl+P"))
        a.triggered.connect(self._add_download_jobs)
        file_menu.addSeparator()
        a = file_menu.addAction("Jobliste exportieren …")
        a.setShortcut(QKeySequence("Ctrl+E"))
        a.triggered.connect(self._export_jobs)
        a = file_menu.addAction("Jobliste importieren …")
        a.setShortcut(QKeySequence("Ctrl+I"))
        a.triggered.connect(self._import_jobs)
        file_menu.addSeparator()
        file_menu.addAction("Alle Jobs entfernen", self._clear_jobs)
        file_menu.addSeparator()
        file_menu.addAction("Beenden", self.close)

        settings_menu = mb.addMenu("&Einstellungen")
        settings_menu.addAction("Video …", self._open_video_settings)
        settings_menu.addAction("Audio …", self._open_audio_settings)
        settings_menu.addAction("YouTube …", self._open_youtube_settings)
        settings_menu.addAction("Kameras …", self._open_camera_settings)
        settings_menu.addSeparator()
        settings_menu.addAction("Allgemein …", self._open_general_settings)

    # ══════════════════════════════════════════════════════════
    #  Toolbar
    # ══════════════════════════════════════════════════════════
    def _build_toolbar(self):
        tb = QToolBar("Aktionen")
        tb.setMovable(False)
        tb.setIconSize(tb.iconSize())
        self.addToolBar(tb)

        tb.addAction("＋ Dateien", self._add_files)
        tb.addAction("＋ Ordner", self._add_directory)
        tb.addAction("＋ Pi-Download", self._add_download_jobs)
        tb.addSeparator()

        self.act_start = tb.addAction("▶  Starten", self._start_jobs)
        self.act_cancel = tb.addAction("■  Abbrechen", self._cancel_all)
        self.act_cancel.setEnabled(False)
        tb.addSeparator()

        tb.addAction("Bearbeiten", self._edit_job)
        tb.addAction("Entfernen", self._remove_selected)

    # ══════════════════════════════════════════════════════════
    #  Zentrales Widget
    # ══════════════════════════════════════════════════════════
    def _build_central(self):
        splitter = QSplitter(Qt.Vertical)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["#", "Typ", "Beschreibung", "Status", "YouTube-Titel"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.resizeSection(3, 160)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._edit_job)

        self._progress_delegate = ProgressDelegate(self.table)
        self.table.setItemDelegateForColumn(3, self._progress_delegate)

        splitter.addWidget(self.table)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Monospace", 9))
        splitter.addWidget(self.log_text)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    # ══════════════════════════════════════════════════════════
    #  Statusbar
    # ══════════════════════════════════════════════════════════
    def _build_statusbar(self):
        self.status_label = QLabel("Bereit")
        self.statusBar().addWidget(self.status_label, 2)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setMaximumHeight(18)
        self.progress.setValue(0)
        self.progress.setFormat("Gesamt: %v/%m")
        self.statusBar().addPermanentWidget(self.progress, 1)

    # ══════════════════════════════════════════════════════════
    #  Tabelle
    # ══════════════════════════════════════════════════════════
    def _refresh_table(self):
        self.table.setRowCount(len(self.jobs))
        for i, job in enumerate(self.jobs):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))

            if job.job_type == "download":
                self.table.setItem(i, 1, QTableWidgetItem("⬇ Download"))
                desc = f"{job.device_name}  →  {job.source_path}"
            else:
                self.table.setItem(i, 1, QTableWidgetItem("🔄 Konvertieren"))
                desc = f"{job.source_path.name}   ({job.source_path.parent})"

            self.table.setItem(i, 2, QTableWidgetItem(desc))

            status_item = QTableWidgetItem(job.status)
            status_item.setData(Qt.UserRole, job.progress_pct)
            if job.status == "Fertig":
                status_item.setForeground(Qt.darkGreen)
            elif "Fehler" in job.status:
                status_item.setForeground(Qt.red)
            elif job.status in ("Läuft", "Herunterladen"):
                status_item.setForeground(Qt.blue)
            elif job.status == "Übersprungen":
                status_item.setForeground(Qt.gray)
            self.table.setItem(i, 3, status_item)

            self.table.setItem(i, 4, QTableWidgetItem(
                job.youtube_title or ""))

    # ══════════════════════════════════════════════════════════
    #  Log
    # ══════════════════════════════════════════════════════════
    @Slot(str)
    def _append_log(self, msg: str):
        self.log_text.append(msg)

    # ══════════════════════════════════════════════════════════
    #  Jobliste befüllen
    # ══════════════════════════════════════════════════════════
    def _add_files(self):
        init_dir = self.settings.last_directory or str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self, "MJPEG-Dateien auswählen", init_dir,
            "MJPEG-Dateien (*.mjpg *.mjpeg);;Alle Dateien (*)")
        if files:
            self.settings.last_directory = str(Path(files[0]).parent)
            self.settings.save()
            for f in files:
                self.jobs.append(ConvertJob(source_path=Path(f)))
            self._refresh_table()
            self._update_count()

    def _add_directory(self):
        init_dir = self.settings.last_directory or str(Path.home())
        dlg = QFileDialog(self, "Ordner mit MJPEG-Dateien", init_dir)
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        tree = dlg.findChild(QTreeView)
        if tree:
            tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        list_view = dlg.findChild(QListView)
        if list_view:
            list_view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        if not dlg.exec():
            return
        dirs = dlg.selectedFiles()
        if not dirs:
            return
        self.settings.last_directory = str(Path(dirs[0]).parent)
        self.settings.save()
        total_added = 0
        empty_dirs = []
        for d in dirs:
            dp = Path(d)
            found = sorted(dp.glob("*.mjpg")) + sorted(dp.glob("*.mjpeg"))
            if not found:
                empty_dirs.append(str(dp))
                continue
            for f in found:
                self.jobs.append(ConvertJob(source_path=f))
            total_added += len(found)
        if empty_dirs:
            QMessageBox.information(
                self, "Hinweis",
                "Keine .mjpg/.mjpeg-Dateien in:\n"
                + "\n".join(empty_dirs))
        if total_added:
            self._refresh_table()
            self._update_count()

    def _add_download_jobs(self):
        """Fügt je einen Download-Eintrag pro konfigurierter Kamera zur
        Jobliste hinzu. Das eigentliche Herunterladen passiert beim Start."""
        cam = self.settings.cameras
        if not cam.devices:
            QMessageBox.warning(
                self, "Keine Kameras",
                "Es sind keine Kameras konfiguriert.\n"
                "Bitte zuerst unter Einstellungen → Kameras Geräte anlegen.")
            return
        if not cam.destination:
            QMessageBox.warning(
                self, "Kein Zielverzeichnis",
                "In den Kamera-Einstellungen ist kein Zielverzeichnis angegeben.")
            return
        # Keine doppelten Download-Jobs für dieselben Geräte
        existing = {j.device_name for j in self.jobs
                    if j.job_type == "download" and j.status == "Wartend"}
        added = 0
        for dev in cam.devices:
            if dev.name not in existing:
                self.jobs.append(ConvertJob(
                    source_path=Path(cam.destination),
                    job_type="download",
                    device_name=dev.name,
                ))
                added += 1
        if added:
            self._refresh_table()
            self._update_count()
            self._append_log(
                f"{added} Download-Auftrag/Aufträge für Pi-Kameras hinzugefügt\n"
                f"Die heruntergeladenen Dateien werden nach dem Download "
                f"automatisch als Konvertier-Aufträge angelegt und verarbeitet.")
        else:
            QMessageBox.information(
                self, "Hinweis",
                "Download-Aufträge für alle Kameras sind bereits vorhanden.")

    def _remove_selected(self):
        rows = sorted(
            {idx.row() for idx in self.table.selectedIndexes()},
            reverse=True)
        for r in rows:
            if 0 <= r < len(self.jobs):
                del self.jobs[r]
        self._refresh_table()
        self._update_count()

    def _clear_jobs(self):
        if self.jobs:
            if QMessageBox.question(
                    self, "Bestätigung", "Alle Aufträge entfernen?",
                    QMessageBox.Yes | QMessageBox.No
            ) == QMessageBox.Yes:
                self.jobs.clear()
                self._refresh_table()
                self.status_label.setText("Bereit")

    def _edit_job(self):
        rows = sorted(
            {idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            return
        idx = rows[0]
        if 0 <= idx < len(self.jobs):
            dlg = JobEditDialog(self, self.jobs[idx])
            dlg.exec()
            self._refresh_table()

    def _update_count(self):
        dl = sum(1 for j in self.jobs if j.job_type == "download")
        cv = sum(1 for j in self.jobs if j.job_type == "convert")
        parts = []
        if dl:
            parts.append(f"{dl} Download(s)")
        if cv:
            parts.append(f"{cv} Konvertierung(en)")
        self.status_label.setText(", ".join(parts) if parts else "Bereit")

    # ══════════════════════════════════════════════════════════
    #  Einstellungs-Dialoge
    # ══════════════════════════════════════════════════════════
    def _open_camera_settings(self):
        dlg = CameraSettingsDialog(self, self.settings)
        if dlg.exec():
            self.settings.save()

    def _open_video_settings(self):
        dlg = VideoSettingsDialog(self, self.settings)
        dlg.exec()

    def _open_audio_settings(self):
        AudioSettingsDialog(self, self.settings).exec()

    def _open_youtube_settings(self):
        YouTubeSettingsDialog(self, self.settings).exec()

    def _open_general_settings(self):
        from .dialogs import GeneralSettingsDialog
        GeneralSettingsDialog(self, self.settings).exec()

    # ══════════════════════════════════════════════════════════
    #  Jobliste Import / Export
    # ══════════════════════════════════════════════════════════
    def _export_jobs(self):
        if not self.jobs:
            QMessageBox.information(
                self, "Hinweis", "Die Jobliste ist leer.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Jobliste exportieren",
            str(Path(self.settings.last_directory or Path.home())
                / "jobliste.json"),
            "JSON-Dateien (*.json);;Alle Dateien (*)")
        if path:
            try:
                save_jobs(self.jobs, Path(path))
                self._append_log(
                    f"Jobliste exportiert: {path}  ({len(self.jobs)} Jobs)")
            except Exception as e:
                QMessageBox.critical(
                    self, "Export-Fehler", str(e))

    def _import_jobs(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Jobliste importieren",
            self.settings.last_directory or str(Path.home()),
            "JSON-Dateien (*.json);;Alle Dateien (*)")
        if path:
            try:
                imported = load_jobs(Path(path))
                self.jobs.extend(imported)
                self._refresh_table()
                self._update_count()
                self._append_log(
                    f"Jobliste importiert: {path}  ({len(imported)} Jobs)")
            except Exception as e:
                QMessageBox.critical(
                    self, "Import-Fehler",
                    f"Die Datei konnte nicht geladen werden:\n{e}")

    # ══════════════════════════════════════════════════════════
    #  Session speichern / wiederherstellen
    # ══════════════════════════════════════════════════════════
    def _save_session(self):
        """Speichert die aktuelle Jobliste als session.json."""
        try:
            save_jobs(self.jobs, SESSION_FILE)
        except Exception:
            pass   # Stillschweigend ignorieren

    def _restore_session(self):
        """Lädt die letzte Jobliste aus session.json."""
        if SESSION_FILE.exists():
            try:
                self.jobs = load_jobs(SESSION_FILE)
                self._refresh_table()
                self._update_count()
                self._append_log(
                    f"Session wiederhergestellt: {len(self.jobs)} Job(s)")
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════
    #  Pipeline: ▶ Starten
    #
    #  1. Download-Jobs vorhanden? → DownloadWorker → fertig? weiter zu 2.
    #  2. Konvertier-Jobs vorhanden? → ConvertWorker → fertig.
    #
    #  Alles im selben Fenster, eine Jobliste, ein Start-Button.
    # ══════════════════════════════════════════════════════════
    def _start_jobs(self):
        pending_dl = [j for j in self.jobs
                      if j.job_type == "download"
                      and j.status in ("Wartend", "Fehler")]
        pending_cv = [j for j in self.jobs
                      if j.job_type == "convert"
                      and j.status in ("Wartend", "Fehler")]

        if not pending_dl and not pending_cv:
            QMessageBox.information(
                self, "Hinweis", "Keine wartenden Aufträge.")
            return

        self._set_busy(True)

        if pending_dl:
            self._run_downloads(pending_dl)
        else:
            self._run_converts(pending_cv)

    # ── Phase 1: Downloads ────────────────────────────────────
    def _run_downloads(self, dl_jobs: list[ConvertJob]):
        cam = self.settings.cameras

        # Geräte anhand der Job-Device-Namen auflösen
        dev_by_name = {d.name: d for d in cam.devices}
        devices = []
        for job in dl_jobs:
            dev = dev_by_name.get(job.device_name)
            if dev:
                devices.append(dev)
                job.status = "Wartend"
            else:
                job.status = "Fehler"
                job.error_msg = f"Gerät '{job.device_name}' nicht in Einstellungen"
        self._refresh_table()

        if not devices:
            self._append_log("Keine gültigen Geräte – Downloads übersprungen.")
            self._after_downloads()
            return

        self._append_log(
            f"\n{'═'*60}"
            f"\n  ⬇ Download von {len(devices)} Kamera(s)"
            f"  →  {cam.destination}"
            f"\n{'═'*60}")

        # Alle Download-Jobs auf "Herunterladen" setzen
        for job in dl_jobs:
            if job.status != "Fehler":
                job.status = "Herunterladen"
        self._refresh_table()

        self._dl_thread = QThread(self)
        self._dl_worker = DownloadWorker(
            config=cam,
            devices=devices,
            delete_after_download=cam.delete_after_download,
        )
        self._dl_worker.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.log_message.connect(self._append_log)
        self._dl_worker.file_progress.connect(self._on_dl_file_progress)
        self._dl_worker.device_done.connect(self._on_device_done)
        self._dl_worker.finished.connect(self._on_all_downloads_done)
        self._dl_thread.start()

    @Slot(str, str, int, int)
    def _on_dl_file_progress(self, device: str, filename: str,
                             transferred: int, total: int):
        if total > 0:
            pct = int(transferred / total * 100)
            self.status_label.setText(
                f"⬇ {device}: {filename}  {pct} %")
        else:
            self.status_label.setText(f"⬇ {device}: {filename}")

    @Slot(str, int)
    def _on_device_done(self, device_name: str, count: int):
        """Aktualisiert den Download-Job für dieses Gerät."""
        for job in self.jobs:
            if (job.job_type == "download"
                    and job.device_name == device_name
                    and job.status == "Herunterladen"):
                job.status = "Heruntergeladen"
                break
        self._refresh_table()

    @Slot(int, list)
    def _on_all_downloads_done(self, total: int, mjpg_paths: list):
        self._dl_thread.quit()
        self._dl_thread.wait()
        self._dl_thread = None
        self._dl_worker = None

        # Download-Jobs abschließen
        for job in self.jobs:
            if job.job_type == "download" and job.status in (
                    "Herunterladen", "Heruntergeladen"):
                job.status = "Fertig"

        self._append_log(f"\n⬇ Downloads abgeschlossen: {total} Aufnahme(n)")

        # Metadaten von Download-Jobs indexieren (device_name → Job)
        dl_meta = {}
        for job in self.jobs:
            if job.job_type == "download":
                dl_meta[job.device_name] = job

        # Konvertier-Jobs aus heruntergeladenen Dateien erzeugen
        # mjpg_paths ist list[tuple[str, str]] → (device_name, path)
        if mjpg_paths:
            existing = {str(j.source_path) for j in self.jobs
                        if j.job_type == "convert"}
            added = 0
            for device_name, path in mjpg_paths:
                if path not in existing:
                    parent_job = dl_meta.get(device_name)
                    new_job = ConvertJob(source_path=Path(path))
                    if parent_job:
                        new_job.youtube_title = parent_job.youtube_title
                        new_job.youtube_playlist = parent_job.youtube_playlist
                    self.jobs.append(new_job)
                    added += 1
            if added:
                self._append_log(
                    f"   {added} Konvertier-Auftrag/Aufträge aus Download erzeugt")
        self._refresh_table()

        # Weiter zur Konvertierung
        self._after_downloads()

    def _after_downloads(self):
        """Prüft ob Konvertier-Jobs anstehen und startet sie."""
        pending_cv = [j for j in self.jobs
                      if j.job_type == "convert"
                      and j.status in ("Wartend", "Fehler")]
        if pending_cv:
            self._run_converts(pending_cv)
        else:
            self._append_log("Keine Dateien zum Konvertieren.")
            self._set_busy(False)

    # ── Phase 2: Konvertierung ────────────────────────────────
    def _run_converts(self, cv_jobs: list[ConvertJob]):
        self._append_log(
            f"\n{'═'*60}"
            f"\n  🔄 Konvertierung von {len(cv_jobs)} Datei(en)"
            f"\n{'═'*60}")

        self.progress.setMaximum(len(cv_jobs))
        self.progress.setValue(0)

        self._thread = QThread()
        self._worker = ConvertWorker(cv_jobs, self.settings)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.log_message.connect(self._append_log)
        self._worker.job_updated.connect(self._refresh_table)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.finished.connect(self._on_worker_done)

        self._job_start_time = time.monotonic()
        self._file_start_time = time.monotonic()
        self._thread.start()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int):
        self.progress.setValue(done)
        self._file_start_time = time.monotonic()
        self.status_label.setText(f"Konvertiere {done}/{total} …")

    @Slot(int, int)
    def _on_file_progress(self, job_index: int, pct: int):
        elapsed = time.monotonic() - self._file_start_time
        eta_str = ""
        if pct > 0 and elapsed > 2:
            total_est = elapsed / (pct / 100.0)
            remaining = max(0, total_est - elapsed)
            eta_str = (
                f" – Verstrichen: {self._format_duration(elapsed)},"
                f" Rest: ~{self._format_duration(remaining)}")

        done_jobs = self.progress.value()
        total_jobs = self.progress.maximum()
        self.status_label.setText(
            f"Konvertiere {done_jobs}/{total_jobs}"
            f" – Datei {pct}%{eta_str}")

        row = self._find_job_row(job_index)
        if row is not None:
            item = self.table.item(row, 3)
            if item is not None:
                if pct < 100:
                    item.setText(f"Läuft ({pct}%)")
                else:
                    item.setText("Läuft (100%)")
                item.setData(Qt.UserRole, pct)
                self.table.viewport().update()

    @Slot(int, int, int)
    def _on_worker_done(self, ok: int, skip: int, fail: int):
        msg = (f"Fertig: {ok} erfolgreich, {skip} übersprungen, "
               f"{fail} fehlgeschlagen")
        self._append_log(f"\n{msg}")
        self.status_label.setText(msg)
        self._set_busy(False)

        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

        self._refresh_table()

    # ══════════════════════════════════════════════════════════
    #  Hilfsmethoden
    # ══════════════════════════════════════════════════════════
    @staticmethod
    def _format_duration(seconds: float) -> str:
        s = int(seconds)
        if s >= 3600:
            h = s // 3600
            m = (s % 3600) // 60
            return f"{h}h {m:02d}min"
        elif s >= 60:
            m = s // 60
            sec = s % 60
            return f"{m}min {sec:02d}s"
        else:
            return f"{s}s"

    def _find_job_row(self, pending_index: int) -> Optional[int]:
        if not hasattr(self, '_worker') or not self._worker:
            return None
        try:
            job = self._worker._jobs[pending_index]
        except IndexError:
            return None
        for row, j in enumerate(self.jobs):
            if j is job:
                return row
        return None

    def _cancel_all(self):
        if self._dl_worker:
            self._dl_worker.cancel()
        if self._worker:
            self._worker.cancel()
        self._append_log("Abbruch angefordert …")

    def _set_busy(self, busy: bool):
        self.act_start.setEnabled(not busy)
        self.act_cancel.setEnabled(busy)

    # ══════════════════════════════════════════════════════════
    #  Beenden
    # ══════════════════════════════════════════════════════════
    def closeEvent(self, event):
        running = ((self._thread and self._thread.isRunning())
                   or (self._dl_thread and self._dl_thread.isRunning()))
        if running:
            if QMessageBox.question(
                    self, "Verarbeitung läuft",
                    "Download oder Konvertierung läuft noch. Wirklich beenden?",
                    QMessageBox.Yes | QMessageBox.No
            ) != QMessageBox.Yes:
                event.ignore()
                return
            self._cancel_all()
            for t in (self._thread, self._dl_thread):
                if t:
                    t.quit()
                    if not t.wait(10_000):
                        t.terminate()
                        t.wait(2000)
        # Session immer speichern (für Restore beim nächsten Start)
        self._save_session()
        event.accept()
