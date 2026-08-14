"""Workflow-Dialog: mehrere Workflows verwalten und starten.

Ersetzt den alten mehrstufigen Workflow-Wizard durch einen einzigen,
übersichtlichen Dialog:
    • mehrere Workflows anlegen, bearbeiten oder entfernen
  • Workflow laden / speichern
  • Globale Optionen (Rechner herunterfahren)
  • Workflow starten
"""

import copy
from pathlib import Path
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QDialogButtonBox, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox, QFileDialog, QMessageBox, QSplitter, QWidget,
)

from ..settings import AppSettings
from ..workflow import (
    WORKFLOW_DIR,
    Workflow,
    WorkflowJob,
    increment_workflow_name,
    normalize_workflow_name,
)
from ..workflow.graph import graph_reachable_types
from .job_editor import JobEditorDialog


# Kompakt-Beschreibungen für die Übersichtstabelle
def _summarize_source(job: WorkflowJob) -> str:
    mode_icons = {"files": "🗃", "folder_scan": "📁", "pi_download": "📷"}
    icon = mode_icons.get(job.source_mode, "?")
    if job.source_mode == "files":
        n = len(job.files)
        return f"{icon} {n} Datei{'en' if n != 1 else ''}"
    if job.source_mode == "folder_scan":
        folder = Path(job.source_folder).name if job.source_folder else "–"
        return f"{icon} {folder}  ({job.file_pattern})"
    if job.source_mode == "pi_download":
        return f"{icon} {job.device_name or '–'}"
    return "?"


def _summarize_processing(job: WorkflowJob) -> str:
    reachable = graph_reachable_types(job)
    if not reachable or "convert" not in reachable:
        return "keine Konvertierung"
    from ..media.encoder import encoder_display_name
    enc = encoder_display_name(job.encoder)
    return f"{enc}  CRF {job.crf}  {job.output_format.upper()}"


def _summarize_audio(job: WorkflowJob) -> str:
    parts = []
    if job.merge_audio:
        parts.append("Merge")
    if job.amplify_audio:
        parts.append(f"Verstärken {job.amplify_db:+.0f} dB")
    if job.audio_sync:
        parts.append("Sync")
    return ", ".join(parts) if parts else "—"


def _summarize_youtube(job: WorkflowJob) -> str:
    reachable = graph_reachable_types(job)
    parts = []
    if "yt_version" in reachable:
        parts.append("YT-Version")
    if "youtube_upload" in reachable:
        parts.append("Upload")
    return " + ".join(parts) if parts else "—"


class WorkflowDialog(QDialog):
    """Haupt-Dialog für Workflow-Verwaltung und -Start."""

    def __init__(self, parent, settings: AppSettings,
                 workflow: Workflow | None = None):
        super().__init__(parent)
        self.setWindowTitle("Workflow")
        self.resize(880, 560)
        self.setMinimumSize(700, 400)

        self._settings = settings
        self._workflow = workflow or Workflow()

        self._build_ui()
        self._refresh_job_table()

    @property
    def workflow(self) -> Workflow:
        return self._workflow

    # ── UI aufbauen ───────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Kopfzeile
        header = QLabel("Workflow")
        header.setFont(QFont("", 13, QFont.Bold))
        layout.addWidget(header)

        subtitle = QLabel(
            "Ein Workflow kann mehrere Aufträge enthalten. "
            "Jeder Auftrag bündelt Quelle, Verarbeitung, Audio und YouTube-Einstellungen. "
            "Download- und Kopierschritte laufen automatisch vor der Verarbeitung.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)

        # Auftrags-Tabelle
        self._job_table = QTableWidget(0, 6)
        self._job_table.setHorizontalHeaderLabels(
            ["✓", "Name", "Quelle", "Verarbeitung", "Audio", "YouTube"])
        hdr = self._job_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.resizeSection(0, 30)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.resizeSection(1, 160)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Interactive)
        hdr.resizeSection(3, 180)
        hdr.setSectionResizeMode(4, QHeaderView.Interactive)
        hdr.resizeSection(4, 130)
        hdr.setSectionResizeMode(5, QHeaderView.Interactive)
        hdr.resizeSection(5, 110)
        self._job_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._job_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._job_table.verticalHeader().setVisible(False)
        self._job_table.setAlternatingRowColors(True)
        self._job_table.doubleClicked.connect(self._edit_selected_job)
        self._job_table.itemChanged.connect(self._on_enabled_checkbox_changed)
        layout.addWidget(self._job_table, stretch=1)

        # Buttons für Aufträge
        job_btn_row = QHBoxLayout()

        add_btn = QPushButton("＋ Workflow anlegen")
        add_btn.setToolTip("Neuen Workflow anlegen")
        add_btn.clicked.connect(self._add_job)
        job_btn_row.addWidget(add_btn)

        job_btn_row.addStretch()

        edit_btn = QPushButton("Bearbeiten")
        edit_btn.clicked.connect(self._edit_selected_job)
        job_btn_row.addWidget(edit_btn)

        duplicate_btn = QPushButton("Duplizieren")
        duplicate_btn.setToolTip("Kopie des aktuellen Workflows anlegen")
        duplicate_btn.clicked.connect(self._duplicate_selected_job)
        job_btn_row.addWidget(duplicate_btn)

        remove_btn = QPushButton("Entfernen")
        remove_btn.clicked.connect(self._remove_selected_jobs)
        job_btn_row.addWidget(remove_btn)

        layout.addLayout(job_btn_row)

        # Globale Optionen + Laden/Speichern
        bottom_row = QHBoxLayout()

        wf_load_btn = QPushButton("Workflow laden …")
        wf_load_btn.clicked.connect(self._load_workflow)
        bottom_row.addWidget(wf_load_btn)

        wf_save_btn = QPushButton("Workflow speichern …")
        wf_save_btn.clicked.connect(self._save_workflow)
        bottom_row.addWidget(wf_save_btn)

        bottom_row.addStretch()

        self._publish_kaderblick_cb = QCheckBox("Kaderblick nach vollständigem Lauf veröffentlichen")
        self._publish_kaderblick_cb.setChecked(self._workflow.publish_kaderblick_videos)
        self._publish_kaderblick_cb.setToolTip(
            "Wird nur ausgeführt, wenn alle tatsächlich gestarteten Jobs erfolgreich abgeschlossen sind."
        )
        bottom_row.addWidget(self._publish_kaderblick_cb)

        self._shutdown_cb = QCheckBox("Herunterfahren: AUS")
        self._shutdown_cb.setChecked(self._workflow.shutdown_after)
        self._shutdown_cb.setText(
            "⚠ Herunterfahren: AN" if self._workflow.shutdown_after else "Herunterfahren: AUS"
        )
        self._shutdown_cb.toggled.connect(
            lambda checked: self._shutdown_cb.setText(
                "⚠ Herunterfahren: AN" if checked else "Herunterfahren: AUS"
            )
        )
        bottom_row.addWidget(self._shutdown_cb)

        layout.addLayout(bottom_row)

        # Dialog-Buttons
        dialog_buttons = QDialogButtonBox()
        self._apply_btn = dialog_buttons.addButton(
            "Übernehmen", QDialogButtonBox.AcceptRole)
        self._apply_btn.clicked.connect(self._apply_and_close)
        self._start_btn = dialog_buttons.addButton(
            "▶  Workflow starten", QDialogButtonBox.AcceptRole)
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #2d8d46; color: white; "
            "font-weight: bold; padding: 6px 18px; }")
        cancel_btn = dialog_buttons.addButton(
            "Schließen", QDialogButtonBox.RejectRole)
        dialog_buttons.accepted.connect(self._confirm_and_start)
        dialog_buttons.rejected.connect(self.reject)
        layout.addWidget(dialog_buttons)

    # ── Tabelle befüllen ──────────────────────────────────────

    def _refresh_job_table(self) -> None:
        # Signal kurz trennen, um spurius itemChanged-Feuern zu vermeiden
        self._job_table.itemChanged.disconnect(self._on_enabled_checkbox_changed)
        jobs = self._workflow.jobs
        self._job_table.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            # Aktivierungs-Checkbox
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if job.enabled else Qt.Unchecked)
            self._job_table.setItem(row, 0, chk)

            self._job_table.setItem(row, 1, QTableWidgetItem(job.name))
            self._job_table.setItem(row, 2, QTableWidgetItem(_summarize_source(job)))
            self._job_table.setItem(row, 3, QTableWidgetItem(_summarize_processing(job)))
            self._job_table.setItem(row, 4, QTableWidgetItem(_summarize_audio(job)))
            self._job_table.setItem(row, 5, QTableWidgetItem(_summarize_youtube(job)))

        self._job_table.itemChanged.connect(self._on_enabled_checkbox_changed)

    def _on_enabled_checkbox_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if item.column() == 0 and 0 <= row < len(self._workflow.jobs):
            self._workflow.jobs[row].enabled = item.checkState() == Qt.Checked
            self._workflow.save_as_last()

    def _selected_job_row(self) -> int:
        row = self._job_table.currentRow()
        if 0 <= row < len(self._workflow.jobs):
            return row
        return 0 if len(self._workflow.jobs) == 1 else -1

    def _existing_job_names(self, *, exclude_row: int | None = None) -> list[str]:
        names: list[str] = []
        for index, job in enumerate(self._workflow.jobs):
            if exclude_row is not None and index == exclude_row:
                continue
            normalized = normalize_workflow_name(job.name)
            if normalized:
                names.append(normalized)
        return names

    def _resolve_job_name(self, job: WorkflowJob, *, exclude_row: int | None = None) -> str | None:
        desired_name = normalize_workflow_name(job.name) or "Workflow"
        existing = self._existing_job_names(exclude_row=exclude_row)
        if desired_name not in existing:
            return desired_name

        incremented = increment_workflow_name(desired_name, existing)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Workflow-Name bereits vorhanden")
        box.setText(f"Es gibt bereits einen Workflow mit dem Namen '{desired_name}'.")
        box.setInformativeText(
            f"Du kannst den Namen beibehalten oder automatisch auf '{incremented}' erhöhen."
        )
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

    # ── Auftrags-Verwaltung ───────────────────────────────────

    def _add_job(self) -> None:
        dlg = JobEditorDialog(self, self._settings)
        if dlg.exec():
            resolved_name = self._resolve_job_name(dlg.result_job)
            if resolved_name is None:
                return
            dlg.result_job.name = resolved_name
            self._workflow.jobs.append(dlg.result_job)
            if len(self._workflow.jobs) == 1:
                self._workflow.name = dlg.result_job.name or self._workflow.name
            self._refresh_job_table()
            self._job_table.selectRow(len(self._workflow.jobs) - 1)
            self._workflow.save_as_last()

    def _edit_selected_job(self) -> None:
        row = self._selected_job_row()
        if not (0 <= row < len(self._workflow.jobs)):
            return
        job = self._workflow.jobs[row]
        dlg = JobEditorDialog(self, self._settings, job)
        if dlg.exec():
            resolved_name = self._resolve_job_name(dlg.result_job, exclude_row=row)
            if resolved_name is None:
                return
            dlg.result_job.name = resolved_name
            self._workflow.jobs[row] = dlg.result_job
            if row == 0:
                self._workflow.name = dlg.result_job.name or self._workflow.name
            self._refresh_job_table()
            self._job_table.selectRow(row)
            self._workflow.save_as_last()

    def _duplicate_selected_job(self) -> None:
        row = self._selected_job_row()
        if not (0 <= row < len(self._workflow.jobs)):
            return
        original = self._workflow.jobs[row]
        clone = copy.deepcopy(original)
        clone.id = uuid.uuid4().hex[:8]
        clone.name = increment_workflow_name(clone.name or "Workflow", [job.name for job in self._workflow.jobs])
        resolved_name = self._resolve_job_name(clone)
        if resolved_name is None:
            return
        clone.name = resolved_name
        self._workflow.jobs.insert(row + 1, clone)
        self._refresh_job_table()
        self._job_table.selectRow(row + 1)
        self._workflow.save_as_last()

    def _remove_selected_jobs(self) -> None:
        row = self._selected_job_row()
        if not (0 <= row < len(self._workflow.jobs)):
            return
        del self._workflow.jobs[row]
        self._refresh_job_table()
        self._workflow.save_as_last()

    # ── Workflow laden / speichern ────────────────────────────

    def _save_workflow(self) -> None:
        self._workflow.shutdown_after = self._shutdown_cb.isChecked()
        self._workflow.publish_kaderblick_videos = self._publish_kaderblick_cb.isChecked()
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        default_name = self._workflow.name or "workflow"
        path, _ = QFileDialog.getSaveFileName(
            self, "Workflow speichern",
            str(WORKFLOW_DIR / f"{default_name}.json"),
            "JSON-Dateien (*.json)")
        if path:
            p = Path(path)
            self._workflow.name = p.stem
            self._workflow.save(p)
            QMessageBox.information(
                self, "Gespeichert", f"Workflow gespeichert: {p.name}")

    def _load_workflow(self) -> None:
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Workflow laden",
            str(WORKFLOW_DIR),
            "JSON-Dateien (*.json)")
        if not path:
            return
        try:
            wf = Workflow.load(Path(path))
            self._workflow = wf
            self._shutdown_cb.setChecked(wf.shutdown_after)
            self._publish_kaderblick_cb.setChecked(wf.publish_kaderblick_videos)
            self._refresh_job_table()
        except Exception as exc:
            QMessageBox.critical(
                self, "Ladefehler",
                f"Workflow konnte nicht geladen werden:\n{exc}")

    # ── Start ─────────────────────────────────────────────────

    def _apply_and_close(self) -> None:
        self._workflow.shutdown_after = self._shutdown_cb.isChecked()
        self._workflow.publish_kaderblick_videos = self._publish_kaderblick_cb.isChecked()
        self.accept()

    def _confirm_and_start(self) -> None:
        self._workflow.shutdown_after = self._shutdown_cb.isChecked()
        self._workflow.publish_kaderblick_videos = self._publish_kaderblick_cb.isChecked()

        active_jobs = [job for job in self._workflow.jobs if job.enabled]
        if not active_jobs:
            QMessageBox.warning(
                self, "Kein aktiver Workflow",
                "Bitte zuerst einen Workflow anlegen oder aktivieren.")
            return

        summary_lines = ["<b>Aktiver Workflow:</b><ul>"]
        for job in active_jobs:
            parts = [f"<b>{job.name}</b>"]
            parts.append(_summarize_source(job))
            reachable = graph_reachable_types(job)
            if "convert" not in reachable:
                parts.append("ohne Konvertierung")
            if "youtube_upload" in reachable:
                parts.append("→ YouTube")
            summary_lines.append(f"<li>{'  ·  '.join(parts)}</li>")
        summary_lines.append("</ul>")
        if self._workflow.shutdown_after:
            summary_lines.append(
                "<b>⚠ Der Rechner wird nach Abschluss heruntergefahren!</b>")
        if self._workflow.publish_kaderblick_videos:
            summary_lines.append(
                "<b>Kaderblick wird nach erfolgreichem Abschluss aller gestarteten Jobs veröffentlicht.</b>")

        if QMessageBox.question(
                self, "Workflow starten?",
                "".join(summary_lines),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._workflow.save_as_last()
            self.accept()
