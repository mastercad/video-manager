"""Haupt-GUI des MJPEG Converters (QMainWindow)."""

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QToolBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QProgressBar, QLabel,
    QFileDialog, QMessageBox, QSplitter, QAbstractItemView,
    QTreeView, QListView,
)

from .settings import AppSettings
from .converter import ConvertJob
from .worker import ConvertWorker
from .delegates import ProgressDelegate
from .dialogs import (
    VideoSettingsDialog, AudioSettingsDialog,
    YouTubeSettingsDialog, JobEditDialog,
)


class ConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MJPEG Converter")
        self.resize(960, 640)
        self.setMinimumSize(720, 460)

        self.settings = AppSettings.load()
        self.jobs: list[ConvertJob] = []
        self._worker: Optional[ConvertWorker] = None
        self._thread: Optional[QThread] = None
        self._file_start_time: float = 0.0
        self._job_start_time: float = 0.0

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

    # ── Menü ──────────────────────────────────────────────────
    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&Datei")
        a = file_menu.addAction("Dateien hinzufügen …")
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(self._add_files)
        a = file_menu.addAction("Ordner hinzufügen …")
        a.setShortcut(QKeySequence("Ctrl+D"))
        a.triggered.connect(self._add_directory)
        file_menu.addSeparator()
        file_menu.addAction("Alle Jobs entfernen", self._clear_jobs)
        file_menu.addSeparator()
        file_menu.addAction("Beenden", self.close)

        settings_menu = mb.addMenu("&Einstellungen")
        settings_menu.addAction("Video …", self._open_video_settings)
        settings_menu.addAction("Audio …", self._open_audio_settings)
        settings_menu.addAction("YouTube …", self._open_youtube_settings)

    # ── Toolbar ───────────────────────────────────────────────
    def _build_toolbar(self):
        tb = QToolBar("Aktionen")
        tb.setMovable(False)
        tb.setIconSize(tb.iconSize())
        self.addToolBar(tb)

        tb.addAction("＋ Dateien", self._add_files)
        tb.addAction("＋ Ordner", self._add_directory)
        tb.addSeparator()

        self.act_start = tb.addAction("▶  Starten", self._start_jobs)
        self.act_cancel = tb.addAction("■  Abbrechen", self._cancel_jobs)
        self.act_cancel.setEnabled(False)
        tb.addSeparator()

        tb.addAction("Bearbeiten", self._edit_job)
        tb.addAction("Entfernen", self._remove_selected)

    # ── Zentrales Widget ──────────────────────────────────────
    def _build_central(self):
        splitter = QSplitter(Qt.Vertical)

        # ── Tabelle ──────────────────────────────────────────
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["#", "Datei", "Ordner", "Status", "YouTube-Titel"])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.resizeSection(3, 160)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._edit_job)

        # Delegate für grafischen Fortschrittsbalken
        self._progress_delegate = ProgressDelegate(self.table)
        self.table.setItemDelegateForColumn(3, self._progress_delegate)

        splitter.addWidget(self.table)

        # ── Log ──────────────────────────────────────────────
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Monospace", 9))
        splitter.addWidget(self.log_text)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    # ── Statusbar ─────────────────────────────────────────────
    def _build_statusbar(self):
        self.status_label = QLabel("Bereit")
        self.statusBar().addWidget(self.status_label, 2)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setMaximumHeight(18)
        self.progress.setValue(0)
        self.progress.setFormat("Gesamt: %v/%m")
        self.statusBar().addPermanentWidget(self.progress, 1)

    # ── Tabelle aktualisieren ─────────────────────────────────
    def _refresh_table(self):
        self.table.setRowCount(len(self.jobs))
        for i, job in enumerate(self.jobs):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(
                job.source_path.name))
            self.table.setItem(i, 2, QTableWidgetItem(
                str(job.source_path.parent)))

            status_item = QTableWidgetItem(job.status)
            status_item.setData(Qt.UserRole, job.progress_pct)
            if job.status == "Fertig":
                status_item.setForeground(Qt.darkGreen)
            elif job.status == "Fehler":
                status_item.setForeground(Qt.red)
            elif job.status == "Läuft":
                status_item.setForeground(Qt.blue)
            elif job.status == "Übersprungen":
                status_item.setForeground(Qt.gray)
            self.table.setItem(i, 3, status_item)

            self.table.setItem(i, 4, QTableWidgetItem(
                job.youtube_title or ""))

    # ── Log ───────────────────────────────────────────────────
    @Slot(str)
    def _append_log(self, msg: str):
        self.log_text.append(msg)

    # ── Datei-Aktionen ────────────────────────────────────────
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
            self.status_label.setText(
                f"{len(self.jobs)} Auftrag/Aufträge")

    def _add_directory(self):
        init_dir = self.settings.last_directory or str(Path.home())
        dlg = QFileDialog(self, "Ordner mit MJPEG-Dateien", init_dir)
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        # Mehrfachauswahl über die Liste ermöglichen
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
            self.status_label.setText(
                f"{len(self.jobs)} Auftrag/Aufträge")

    def _remove_selected(self):
        rows = sorted(
            {idx.row() for idx in self.table.selectedIndexes()},
            reverse=True)
        for r in rows:
            if 0 <= r < len(self.jobs):
                del self.jobs[r]
        self._refresh_table()
        self.status_label.setText(
            f"{len(self.jobs)} Auftrag/Aufträge")

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

    # ── Einstellungs-Dialoge ──────────────────────────────────
    def _open_video_settings(self):
        dlg = VideoSettingsDialog(self, self.settings)
        dlg.exec()

    def _open_audio_settings(self):
        AudioSettingsDialog(self, self.settings).exec()

    def _open_youtube_settings(self):
        YouTubeSettingsDialog(self, self.settings).exec()

    # ── Worker ────────────────────────────────────────────────
    def _start_jobs(self):
        pending = [j for j in self.jobs
                   if j.status in ("Wartend", "Fehler")]
        if not pending:
            QMessageBox.information(
                self, "Hinweis", "Keine wartenden Aufträge.")
            return

        self.act_start.setEnabled(False)
        self.act_cancel.setEnabled(True)
        self.progress.setMaximum(len(pending))
        self.progress.setValue(0)

        self._thread = QThread()
        self._worker = ConvertWorker(pending, self.settings)
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

        # Nur die Status-Zelle in der Tabelle aktualisieren
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

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Formatiert Sekunden als 'Xh Ymm' / 'Xmin Ys' / 'Xs'."""
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
        """Findet die Tabellenzeile für einen Job aus der pending-Liste."""
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

    def _cancel_jobs(self):
        if self._worker:
            self._worker.cancel()
        self._append_log("Abbruch angefordert …")

    @Slot(int, int, int)
    def _on_worker_done(self, ok: int, skip: int, fail: int):
        msg = (f"Fertig: {ok} erfolgreich, {skip} übersprungen, "
               f"{fail} fehlgeschlagen")
        self._append_log(f"\n{msg}")
        self.status_label.setText(msg)
        self.act_start.setEnabled(True)
        self.act_cancel.setEnabled(False)

        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

        self._refresh_table()

    # ── Beenden ───────────────────────────────────────────────
    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            if QMessageBox.question(
                    self, "Konvertierung läuft",
                    "Eine Konvertierung läuft noch. Wirklich beenden?",
                    QMessageBox.Yes | QMessageBox.No
            ) != QMessageBox.Yes:
                event.ignore()
                return
            if self._worker:
                self._worker.cancel()
            if self._thread:
                self._thread.quit()
                if not self._thread.wait(10_000):
                    self._thread.terminate()
                    self._thread.wait(2000)
        event.accept()
