"""Einstellungen, Profile und persistente Konfiguration."""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

# ─── Pfade (relativ zum Projekt-Stammverzeichnis) ────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent

SETTINGS_FILE = _BASE_DIR / "settings.json"
SESSION_FILE = _BASE_DIR / "session.json"
CLIENT_SECRET_FILE = _BASE_DIR / "client_secret.json"
TOKEN_FILE = _BASE_DIR / "youtube_token.json"


# ═════════════════════════════════════════════════════════════════
#  Profile – vordefinierte Einstellungskombinationen
# ═════════════════════════════════════════════════════════════════

PROFILES: dict[str, dict] = {
    "KI Auswertung": {
        "encoder": "auto",
        "lossless": False,
        "preset": "slow",
        "crf": 12,
        "output_format": "mp4",
    },
    "YouTube": {
        "encoder": "auto",
        "lossless": False,
        "preset": "medium",
        "crf": 23,
        "output_format": "mp4",
    },
    "Benutzerdefiniert": {},
}


# ═════════════════════════════════════════════════════════════════
#  Settings-Datenklassen
# ═════════════════════════════════════════════════════════════════

@dataclass
class VideoSettings:
    fps: int = 25
    output_format: str = "mp4"
    crf: int = 18
    lossless: bool = False
    preset: str = "medium"
    encoder: str = "auto"
    profile: str = "Benutzerdefiniert"
    overwrite: bool = False
    audio_sync: bool = False
    merge_halves: bool = False
    merge_title_duration: int = 3
    merge_title_bg: str = "#000000"
    merge_title_fg: str = "#FFFFFF"

    def apply_profile(self, profile_name: str) -> None:
        """Wendet ein Profil an (überschreibt nur die Profil-Felder)."""
        if profile_name not in PROFILES:
            return
        values = PROFILES[profile_name]
        for k, v in values.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.profile = profile_name


@dataclass
class AudioSettings:
    include_audio: bool = True
    amplify_audio: bool = True
    audio_suffix: str = ""
    audio_bitrate: str = "192k"
    compand_points: str = "-70/-60|-30/-10"


@dataclass
class YouTubeSettings:
    create_youtube: bool = False
    youtube_crf: int = 23
    youtube_maxrate: str = "8M"
    youtube_bufsize: str = "16M"
    youtube_audio_bitrate: str = "128k"
    upload_to_youtube: bool = False


# ═════════════════════════════════════════════════════════════════
#  Kamera / Raspberry Pi Download
# ═════════════════════════════════════════════════════════════════

@dataclass
class DeviceSettings:
    """Konfiguration eines einzelnen Raspberry Pi Kamera-Systems."""
    name: str = ""
    ip: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    ssh_key: str = ""          # Pfad zum privaten SSH-Key (optional)


@dataclass
class CameraSettings:
    """Globale Download-Einstellungen für alle Kameras."""
    source: str = "/home/kaderblick/camera_api/recordings"
    destination: str = ""
    delete_after_download: bool = False   # Quelldateien nach erfolgreichem DL löschen
    auto_convert: bool = True             # Nach Download automatisch konvertieren
    devices: list = field(default_factory=list)   # list[DeviceSettings]


# ═════════════════════════════════════════════════════════════════
#  Gesamt-AppSettings
# ═════════════════════════════════════════════════════════════════

@dataclass
class AppSettings:
    video: VideoSettings = field(default_factory=VideoSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)
    cameras: CameraSettings = field(default_factory=CameraSettings)
    last_directory: str = ""
    restore_session: bool = False      # Beim Start letzte Jobliste laden

    def save(self):
        SETTINGS_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls) -> "AppSettings":
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text())
                s = cls()
                for k, v in data.get("video", {}).items():
                    if hasattr(s.video, k):
                        setattr(s.video, k, v)
                for k, v in data.get("audio", {}).items():
                    if hasattr(s.audio, k):
                        setattr(s.audio, k, v)
                for k, v in data.get("youtube", {}).items():
                    if hasattr(s.youtube, k):
                        setattr(s.youtube, k, v)
                cam_data = data.get("cameras", {})
                for k, v in cam_data.items():
                    if k == "devices":
                        s.cameras.devices = [
                            DeviceSettings(**{
                                fk: fv for fk, fv in d.items()
                                if hasattr(DeviceSettings(), fk)
                            })
                            for d in (v or [])
                        ]
                    elif hasattr(s.cameras, k):
                        setattr(s.cameras, k, v)
                s.last_directory = data.get("last_directory", "")
                s.restore_session = data.get("restore_session", False)
                return s
            except Exception:
                pass
        return cls()
