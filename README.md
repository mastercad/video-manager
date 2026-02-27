# Fussballverein – Video Manager


## Übersicht

| Tool | Beschreibung | Oberfläche |
|------|-------------|------------|
| [main.py → src/](docs/convert_mjpeg_gui.md) | Grafische Oberfläche für die Videokonvertierung | Qt-GUI (PySide6) |

**Zusätzliche Dokumentation:**

- [Video Manager](docs/video_manager.md)
- [YouTube API – Credentials einrichten](docs/youtube_credentials.md)

## Voraussetzungen

- **Python** ≥ 3.10
- **ffmpeg** und **ffprobe** (für die Video-Konvertierung)
- *Optional:* **NVIDIA-GPU** mit Treiber ≥ 550.54 für Hardware-Encoding (NVENC) in der GUI

## Installation

```bash
# 1. Virtual Environment erstellen (falls noch nicht vorhanden)
python3 -m venv .venv
source .venv/bin/activate

# 2. Abhängigkeiten installieren
pip install -r requirements.txt
```

### ffmpeg installieren

```bash
# Debian / Ubuntu
sudo apt install ffmpeg

# Arch
sudo pacman -S ffmpeg
```

## Schnellstart

```bash
# Video konvertieren (GUI)
python main.py
```

## Projektstruktur

```
utilities/
├── README.md                       ← Diese Datei
├── docs/
│   ├── convert_mjpeg_gui.md        ← Doku: GUI-Konverter
│   └── youtube_credentials.md      ← Doku: YouTube-API-Setup
├── main.py                         ← GUI-Einstiegspunkt
├── src/                            ← GUI-Paket (modulare Architektur)
│   ├── __init__.py
│   ├── app.py                      ← Hauptfenster
│   ├── converter.py                ← Konvertierungslogik
│   ├── delegates.py                ← Fortschrittsbalken
│   ├── diagnostics.py              ← GPU-/System-Diagnose
│   ├── dialogs.py                  ← Einstellungsdialoge
│   ├── encoder.py                  ← Encoder-Auflösung
│   ├── ffmpeg_runner.py            ← ffmpeg-Steuerung
│   ├── merge.py                    ← Halbzeiten zusammenführen
│   ├── settings.py                 ← Einstellungen & Profile
│   ├── worker.py                   ← Hintergrund-Worker
│   └── youtube.py                  ← YouTube-Upload
└── requirements.txt                ← Python-Abhängigkeiten
```
