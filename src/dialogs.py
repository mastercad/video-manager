"""Einstellungs-Dialoge (Video, Audio, YouTube, Job-Bearbeitung)."""

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QVBoxLayout,
    QHBoxLayout, QSpinBox, QComboBox, QCheckBox, QLineEdit, QLabel,
)

from .settings import AppSettings, PROFILES
from .encoder import (
    available_encoder_choices, resolve_encoder, encoder_display_name,
)
from .diagnostics import gpu_diagnostics
from .converter import ConvertJob


# ═════════════════════════════════════════════════════════════════
#  Video-Einstellungen (mit Profilen und Encoder-Auswahl)
# ═════════════════════════════════════════════════════════════════

class VideoSettingsDialog(QDialog):
    def __init__(self, parent, settings: AppSettings):
        super().__init__(parent)
        self.setWindowTitle("Video-Einstellungen")
        self.settings = settings
        vs = settings.video
        self._updating_profile = False  # Guard gegen Rekursion

        layout = QVBoxLayout(self)

        # ── Profil-Auswahl ─────────────────────────────────────
        profile_group = QGroupBox("Profil")
        profile_form = QFormLayout()

        self.profile_combo = QComboBox()
        for name in PROFILES:
            self.profile_combo.addItem(name)
        self.profile_combo.setCurrentText(vs.profile)
        self.profile_combo.currentTextChanged.connect(
            self._on_profile_changed)
        profile_form.addRow("Profil:", self.profile_combo)

        profile_group.setLayout(profile_form)
        layout.addWidget(profile_group)

        # ── Video-Kodierung ────────────────────────────────────
        group = QGroupBox("Video-Kodierung")
        form = QFormLayout()

        # Encoder
        self.encoder_combo = QComboBox()
        for enc_id, enc_name in available_encoder_choices():
            self.encoder_combo.addItem(enc_name, enc_id)
        idx = self.encoder_combo.findData(vs.encoder)
        if idx >= 0:
            self.encoder_combo.setCurrentIndex(idx)
        self.encoder_combo.currentIndexChanged.connect(
            self._on_setting_changed)

        resolved = resolve_encoder(vs.encoder)
        self.encoder_info = QLabel(f"→ {encoder_display_name(resolved)}")
        self.encoder_info.setEnabled(False)

        enc_row = QHBoxLayout()
        enc_row.addWidget(self.encoder_combo)
        enc_row.addWidget(self.encoder_info)
        enc_row.addStretch()
        form.addRow("Encoder:", enc_row)

        # GPU-Status
        diag = gpu_diagnostics()
        gpu_icon = "🟢" if diag.nvenc_available else "🔴"
        self.gpu_status = QLabel(f"{gpu_icon} {diag.summary}")
        self.gpu_status.setWordWrap(True)
        self.gpu_status.setToolTip("\n".join(diag.details))
        if not diag.nvenc_available:
            self.gpu_status.setStyleSheet("color: #b35900;")
        form.addRow("GPU-Status:", self.gpu_status)

        # Framerate
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setValue(vs.fps)
        form.addRow("Framerate (FPS):", self.fps_spin)

        # Ausgabeformat
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["mp4", "avi"])
        self.fmt_combo.setCurrentText(vs.output_format)
        self.fmt_combo.currentTextChanged.connect(self._on_setting_changed)
        form.addRow("Ausgabeformat:", self.fmt_combo)

        # CRF / CQ
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(vs.crf)
        self.crf_spin.valueChanged.connect(self._on_setting_changed)
        crf_row = QHBoxLayout()
        crf_row.addWidget(self.crf_spin)
        self.crf_hint = QLabel("0=verlustfrei  18=sehr gut  23=Standard")
        self.crf_hint.setEnabled(False)
        crf_row.addWidget(self.crf_hint)
        crf_row.addStretch()
        form.addRow("CRF / CQ (Qualität):", crf_row)

        # Preset
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow"])
        self.preset_combo.setCurrentText(vs.preset)
        self.preset_combo.currentTextChanged.connect(
            self._on_setting_changed)
        form.addRow("Preset:", self.preset_combo)

        # Verlustfrei
        self.lossless_cb = QCheckBox("Verlustfrei")
        self.lossless_cb.setChecked(vs.lossless)
        self.lossless_cb.stateChanged.connect(self._on_setting_changed)
        form.addRow("", self.lossless_cb)

        # Audio-Video-Sync
        self.audio_sync_cb = QCheckBox("Audio-Video-Sync (Frame-Drop-Korrektur)")
        self.audio_sync_cb.setChecked(vs.audio_sync)
        self.audio_sync_cb.setToolTip(
            "Zählt alle Frames in der MJPEG-Datei und passt die\n"
            "Framerate an die Audio-Dauer an, um Drift durch\n"
            "Frame-Drops auszugleichen.\n\n"
            "Dauert bei großen Dateien (>200 GB) einige Minuten.")
        form.addRow("", self.audio_sync_cb)

        # Überschreiben
        self.overwrite_cb = QCheckBox("Vorhandene Dateien überschreiben")
        self.overwrite_cb.setChecked(vs.overwrite)
        self.overwrite_cb.setToolTip(
            "Wenn aktiv, werden bestehende .mp4-Dateien erneut erstellt")
        form.addRow("", self.overwrite_cb)

        group.setLayout(form)
        layout.addWidget(group)

        # ── Merge-Gruppe ───────────────────────────────────────
        merge_group = QGroupBox("Halbzeiten zusammenführen")
        merge_form = QFormLayout()

        self.merge_cb = QCheckBox(
            "Dateien pro Kamera-Ordner zusammenführen")
        self.merge_cb.setChecked(vs.merge_halves)
        self.merge_cb.setToolTip(
            "Sortiert die konvertierten Dateien pro Ordner nach Name\n"
            "und fügt sie mit Titelkarten "
            "(\"1. Halbzeit\", \"2. Halbzeit\") zusammen.")
        merge_form.addRow("", self.merge_cb)

        self.title_dur_spin = QSpinBox()
        self.title_dur_spin.setRange(1, 15)
        self.title_dur_spin.setValue(vs.merge_title_duration)
        self.title_dur_spin.setSuffix(" Sekunden")
        merge_form.addRow("Titelbild-Dauer:", self.title_dur_spin)

        merge_group.setLayout(merge_form)
        layout.addWidget(merge_group)

        # ── Buttons ────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Profil-Logik ──────────────────────────────────────────

    def _on_profile_changed(self, profile_name: str):
        """Profil gewählt → Felder aktualisieren."""
        if self._updating_profile:
            return
        self._updating_profile = True
        values = PROFILES.get(profile_name, {})
        if "encoder" in values:
            idx = self.encoder_combo.findData(values["encoder"])
            if idx >= 0:
                self.encoder_combo.setCurrentIndex(idx)
        if "lossless" in values:
            self.lossless_cb.setChecked(values["lossless"])
        if "preset" in values:
            self.preset_combo.setCurrentText(values["preset"])
        if "crf" in values:
            self.crf_spin.setValue(values["crf"])
        if "output_format" in values:
            self.fmt_combo.setCurrentText(values["output_format"])
        self._update_encoder_info()
        self._updating_profile = False

    def _on_setting_changed(self):
        """Einzelne Einstellung geändert → Profil auf 'Benutzerdefiniert'."""
        if not self._updating_profile:
            self._updating_profile = True
            self.profile_combo.setCurrentText("Benutzerdefiniert")
            self._updating_profile = False
        self._update_encoder_info()

    def _update_encoder_info(self):
        """Aktualisiert die Encoder-Info-Anzeige."""
        enc_id = self.encoder_combo.currentData()
        resolved = resolve_encoder(enc_id)
        self.encoder_info.setText(f"→ {encoder_display_name(resolved)}")

    # ── Speichern ─────────────────────────────────────────────

    def _save(self):
        vs = self.settings.video
        vs.profile = self.profile_combo.currentText()
        vs.encoder = self.encoder_combo.currentData()
        vs.fps = self.fps_spin.value()
        vs.output_format = self.fmt_combo.currentText()
        vs.crf = self.crf_spin.value()
        vs.preset = self.preset_combo.currentText()
        vs.lossless = self.lossless_cb.isChecked()
        vs.audio_sync = self.audio_sync_cb.isChecked()
        vs.overwrite = self.overwrite_cb.isChecked()
        vs.merge_halves = self.merge_cb.isChecked()
        vs.merge_title_duration = self.title_dur_spin.value()
        self.settings.save()
        self.accept()


# ═════════════════════════════════════════════════════════════════
#  Audio-Einstellungen
# ═════════════════════════════════════════════════════════════════

class AudioSettingsDialog(QDialog):
    def __init__(self, parent, settings: AppSettings):
        super().__init__(parent)
        self.setWindowTitle("Audio-Einstellungen")
        self.settings = settings
        aus = settings.audio

        layout = QVBoxLayout(self)

        group = QGroupBox("Audio")
        form = QFormLayout()

        self.include_cb = QCheckBox("Audio einbinden")
        self.include_cb.setChecked(aus.include_audio)
        form.addRow("", self.include_cb)

        self.amplify_cb = QCheckBox("Audio verstärken (compand + loudnorm)")
        self.amplify_cb.setChecked(aus.amplify_audio)
        form.addRow("", self.amplify_cb)

        self.suffix_edit = QLineEdit(aus.audio_suffix)
        self.suffix_edit.setPlaceholderText('z.B. "_normalized"')
        form.addRow("Audio-Suffix:", self.suffix_edit)

        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["96k", "128k", "192k", "256k", "320k"])
        self.bitrate_combo.setCurrentText(aus.audio_bitrate)
        form.addRow("Audio-Bitrate:", self.bitrate_combo)

        self.compand_edit = QLineEdit(aus.compand_points)
        form.addRow("Compand-Punkte:", self.compand_edit)

        group.setLayout(form)
        layout.addWidget(group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        aus = self.settings.audio
        aus.include_audio = self.include_cb.isChecked()
        aus.amplify_audio = self.amplify_cb.isChecked()
        aus.audio_suffix = self.suffix_edit.text()
        aus.audio_bitrate = self.bitrate_combo.currentText()
        aus.compand_points = self.compand_edit.text()
        self.settings.save()
        self.accept()


# ═════════════════════════════════════════════════════════════════
#  YouTube-Einstellungen
# ═════════════════════════════════════════════════════════════════

class YouTubeSettingsDialog(QDialog):
    def __init__(self, parent, settings: AppSettings):
        super().__init__(parent)
        self.setWindowTitle("YouTube-Einstellungen")
        self.settings = settings
        yt = settings.youtube

        layout = QVBoxLayout(self)

        # ── Konvertierung ────────────────────────────────────
        g1 = QGroupBox("YouTube-Konvertierung")
        f1 = QFormLayout()

        self.create_cb = QCheckBox("YouTube-optimierte Version erstellen")
        self.create_cb.setChecked(yt.create_youtube)
        f1.addRow("", self.create_cb)

        self.yt_crf_spin = QSpinBox()
        self.yt_crf_spin.setRange(0, 51)
        self.yt_crf_spin.setValue(yt.youtube_crf)
        f1.addRow("CRF:", self.yt_crf_spin)

        self.maxrate_combo = QComboBox()
        self.maxrate_combo.addItems(
            ["4M", "6M", "8M", "12M", "16M", "20M"])
        self.maxrate_combo.setCurrentText(yt.youtube_maxrate)
        f1.addRow("Max. Bitrate:", self.maxrate_combo)

        self.bufsize_combo = QComboBox()
        self.bufsize_combo.addItems(
            ["8M", "12M", "16M", "24M", "32M"])
        self.bufsize_combo.setCurrentText(yt.youtube_bufsize)
        f1.addRow("Buffer-Größe:", self.bufsize_combo)

        self.yt_abr_combo = QComboBox()
        self.yt_abr_combo.addItems(["96k", "128k", "192k", "256k"])
        self.yt_abr_combo.setCurrentText(yt.youtube_audio_bitrate)
        f1.addRow("Audio-Bitrate:", self.yt_abr_combo)

        g1.setLayout(f1)
        layout.addWidget(g1)

        # ── Upload ───────────────────────────────────────────
        g2 = QGroupBox("YouTube-Upload")
        f2 = QFormLayout()

        self.upload_cb = QCheckBox("Videos auf YouTube hochladen")
        self.upload_cb.setChecked(yt.upload_to_youtube)
        f2.addRow("", self.upload_cb)

        hint = QLabel(
            "(Titel und Playlist werden pro Job in der Jobliste gesetzt)")
        hint.setEnabled(False)
        f2.addRow("", hint)

        g2.setLayout(f2)
        layout.addWidget(g2)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        yt = self.settings.youtube
        yt.create_youtube = self.create_cb.isChecked()
        yt.youtube_crf = self.yt_crf_spin.value()
        yt.youtube_maxrate = self.maxrate_combo.currentText()
        yt.youtube_bufsize = self.bufsize_combo.currentText()
        yt.youtube_audio_bitrate = self.yt_abr_combo.currentText()
        yt.upload_to_youtube = self.upload_cb.isChecked()
        self.settings.save()
        self.accept()


# ═════════════════════════════════════════════════════════════════
#  Job bearbeiten
# ═════════════════════════════════════════════════════════════════

class JobEditDialog(QDialog):
    def __init__(self, parent, job: ConvertJob):
        super().__init__(parent)
        self.setWindowTitle("Job bearbeiten")
        self.job = job

        layout = QVBoxLayout(self)

        group = QGroupBox("YouTube-Metadaten")
        form = QFormLayout()

        file_label = QLabel(job.source_path.name)
        file_label.setStyleSheet("color: palette(link);")
        form.addRow("Datei:", file_label)

        self.title_edit = QLineEdit(job.youtube_title)
        form.addRow("YouTube-Titel:", self.title_edit)

        self.playlist_edit = QLineEdit(job.youtube_playlist)
        form.addRow("Playlist:", self.playlist_edit)

        group.setLayout(form)
        layout.addWidget(group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        self.job.youtube_title = self.title_edit.text()
        self.job.youtube_playlist = self.playlist_edit.text()
        self.accept()
