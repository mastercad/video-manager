# MJPEG-Konverter – Grafische Oberfläche

Grafische Benutzeroberfläche für die MJPEG-Konvertierung. Bietet alle Funktionen des CLI-Tools in einer komfortablen Qt-GUI (PySide6) mit Jobliste, Profilen, GPU-Beschleunigung, Halbzeit-Zusammenführung, persistenten Einstellungen und Hintergrund-Verarbeitung. Das Erscheinungsbild folgt automatisch den System-Theme-Einstellungen.

[← Zurück zur Übersicht](../README.md) · Siehe auch: [YouTube-Credentials](youtube_credentials.md)

---

## Features

- **Jobliste** – Dateien und Ordner per Dialog hinzufügen, als Queue abarbeiten
- **Profile** – Vorkonfigurierte Einstellungen: *KI Auswertung*, *YouTube*, *Benutzerdefiniert*
- **Hardware-Encoding** – NVIDIA NVENC-Beschleunigung mit automatischer Erkennung und Fallback auf CPU
- **GPU-Diagnose** – Detaillierte Statusanzeige mit Lösungsvorschlägen bei Problemen
- **Halbzeiten zusammenführen** – Automatische Erkennung und Zusammenführung mit konfigurierbaren Titelkarten
- **Einstellungs-Dialoge** – Video, Audio und YouTube werden in separaten Dialogen konfiguriert
- **Persistente Einstellungen** – Alle Settings werden in `convert_mjpeg_settings.json` gespeichert
- **Hintergrund-Verarbeitung** – ffmpeg läuft in einem Worker-Thread, die GUI bleibt bedienbar
- **Fortschrittsanzeige** – Statusbar mit Fortschrittsbalken und ETA-Anzeige (geschätzte Restzeit)
- **Protokoll** – Scrollbares Log mit detaillierten Meldungen
- **Abbruch-Funktion** – Laufende Konvertierungen abbrechen
- **YouTube-Upload** – Automatischer Upload mit Playlist-Verwaltung

## Voraussetzungen

- Python ≥ 3.10
- `PySide6` (wird über `pip install -r requirements.txt` installiert)
- `ffmpeg` und `ffprobe` im PATH
- *Optional:* NVIDIA-GPU mit Treiber ≥ 550.54 für Hardware-Encoding (NVENC)

## Starten

```bash
python main.py
```

> **Hinweis:** Der Einstiegspunkt ist `main.py` im Projektverzeichnis. Die Applikationslogik liegt im Paket `src/`.

---

## Modulare Architektur

Die GUI ist als Python-Paket (`src/`) strukturiert:

```
utilities/
├── main.py                  ← Einstiegspunkt
├── src/
│   ├── __init__.py          ← Paket-Marker
│   ├── app.py               ← Hauptfenster (QMainWindow)
│   ├── converter.py         ← Konvertierungslogik und Job-Datenklasse
│   ├── delegates.py         ← Fortschrittsbalken in der Tabelle
│   ├── diagnostics.py       ← GPU- und System-Diagnose
│   ├── dialogs.py           ← Einstellungs- und Bearbeitungsdialoge
│   ├── encoder.py           ← Encoder-Auflösung und ffmpeg-Argumente
│   ├── ffmpeg_runner.py     ← ffmpeg-Prozesssteuerung
│   ├── merge.py             ← Halbzeiten zusammenführen
│   ├── settings.py          ← Einstellungen, Profile, Persistenz
│   ├── worker.py            ← Worker-Thread
│   └── youtube.py           ← YouTube-Upload und OAuth
├── convert_mjpeg_settings.json
└── …
```

---

## Benutzeroberfläche

### Hauptfenster

Das Hauptfenster besteht aus vier Bereichen:

```
┌─────────────────────────────────────────────────┐
│  Toolbar: [＋ Dateien] [＋ Ordner] [▶ Starten]  │
│           [■ Abbrechen] [Bearbeiten] [Entfernen]│
├─────────────────────────────────────────────────┤
│  Auftragsliste (Treeview)                       │
│  #  │ Datei           │ Ordner  │ Status │ YT   │
│  1  │ aufnahme_1.mjpg │ /pfad/  │ Wartend│      │
│  2  │ aufnahme_2.mjpg │ /pfad/  │ Fertig │      │
├─────────────────────────────────────────────────┤
│  Protokoll (scrollbares Log)                    │
│  ═══ [1/3] aufnahme_1.mjpg ═══                 │
│  Eingabe: aufnahme_1.mjpg                       │
│  Encoder: h264_nvenc (NVIDIA GPU)               │
│  ✓ Fertig: aufnahme_1.mp4 (234 MB, 45s)        │
├─────────────────────────────────────────────────┤
│  [████████████░░░░░░] 2/3  ETA 12:34  h264_nvenc│
└─────────────────────────────────────────────────┘
```

### Toolbar-Buttons

| Button | Funktion |
|--------|----------|
| **＋ Dateien** | Öffnet Dateidialog zum Auswählen von `.mjpg`/`.mjpeg`-Dateien |
| **＋ Ordner** | Fügt alle MJPEG-Dateien eines Ordners hinzu |
| **▶ Starten** | Startet die Konvertierung aller wartenden Jobs |
| **■ Abbrechen** | Bricht die laufende Verarbeitung ab |
| **Bearbeiten** | Öffnet YouTube-Metadaten für den ausgewählten Job |
| **Entfernen** | Entfernt ausgewählte Jobs aus der Liste |

### Statusleiste

Die Statusleiste am unteren Fensterrand zeigt während der Konvertierung:

- **Fortschrittsbalken** mit Prozentanzeige
- **Job-Zähler** (z. B. „2/5")
- **Geschätzte Restzeit** (ETA)
- **Verwendeter Encoder** (z. B. `h264_nvenc` oder `libx264`)

### Tastenkürzel

| Kürzel | Aktion |
|--------|--------|
| `Strg+O` | Dateien hinzufügen |
| `Strg+D` | Ordner hinzufügen |
| Doppelklick | Job bearbeiten (YouTube-Titel/Playlist) |

---

## Menü: Einstellungen

Die Einstellungen sind über das Menü **Einstellungen** erreichbar und in drei separate Dialoge aufgeteilt.

### Einstellungen → Video

Steuert die Video-Kodierung. Am oberen Rand des Dialogs befindet sich die **Profil-Auswahl** und die **GPU-Statusanzeige**.

#### Profile

| Profil | Beschreibung |
|--------|--------------|
| **KI Auswertung** | CRF 12, Preset slow – hohe Qualität für Spielanalyse mit 5–8× Zoom, auch für KI-Auswertung geeignet |
| **YouTube** | CRF 23, Preset medium – optimiert für YouTube-Upload |
| **Benutzerdefiniert** | Alle Werte frei einstellbar |

Bei Auswahl eines Profils werden die zugehörigen Felder (Encoder, CRF, Preset, Verlustfrei) automatisch gesetzt. Im Profil *Benutzerdefiniert* können alle Werte individuell angepasst werden.

#### Encoder / GPU-Beschleunigung

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Encoder** | auto | `auto` = beste verfügbare Option, `h264_nvenc` = NVIDIA GPU, `libx264` = CPU |

Bei Auswahl von `auto` wird beim Start der Konvertierung automatisch geprüft, ob NVENC verfügbar ist. Ist die GPU nicht nutzbar, erfolgt ein automatischer Fallback auf `libx264` (CPU) mit Hinweis im Protokoll.

#### GPU-Statusanzeige

Im Video-Einstellungsdialog wird der aktuelle GPU-Status angezeigt:

- 🟢 **GPU bereit** – NVENC ist verfügbar und funktionsfähig
- 🔴 **GPU nicht verfügbar** – mit Erklärung und Lösungsvorschlag im Tooltip

Die Diagnose prüft in vier Schritten:
1. Ist eine NVIDIA-GPU vorhanden? (`nvidia-smi`)
2. Ist der Treiber aktuell genug? (≥ 550.54)
3. Ist ffmpeg mit NVENC-Support kompiliert?
4. Funktioniert ein Test-Encode tatsächlich?

#### Video-Einstellungen

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Framerate (FPS)** | 25 | Framerate der Eingabedatei |
| **Ausgabeformat** | mp4 | `mp4` (H.264) oder `avi` (MJPEG) |
| **CRF (Qualität)** | 18 | 0 = verlustfrei, 18 = sehr gut, 23 = Standard, 51 = schlechteste |
| **Preset** | medium | ffmpeg-Preset (ultrafast … veryslow). Langsamer = kleinere Datei |
| **Verlustfrei** | aus | Aktiviert CRF=0 und Preset=slow für verlustfreie Kodierung |
| **Audio-Video-Sync** | aus | Korrigiert Drift durch Frame-Drops (zählt alle Frames, passt FPS an Audio-Dauer an) |
| **Überschreiben** | aus | Vorhandene Ausgabedateien überschreiben |

> **Tipp:** Für die Spielanalyse mit bis zu 8× Zoom empfiehlt sich CRF ≤ 18 oder das Profil *KI Auswertung*. Verlustfrei (CRF=0) ist bei MJPEG-Quellmaterial nicht sinnvoll, da die Frames bereits JPEG-komprimiert sind.

#### Audio-Video-Sync (Frame-Drop-Korrektur)

MJPEG-Aufnahmen können durch Frame-Drops weniger Frames enthalten als erwartet. Mit fester Framerate (z. B. 25 FPS) wird das Video dann kürzer als die Audio-Aufnahme und es entsteht eine zunehmende Desynchronisation.

Wenn **Audio-Video-Sync** aktiviert ist, wird vor der Konvertierung die gesamte MJPEG-Datei gelesen und die tatsächliche Frame-Anzahl gezählt (JPEG-SOI-Marker). Die Input-Framerate wird dann so angepasst, dass die Video-Dauer exakt der Audio-Dauer entspricht.

- Bei einer 222 GB Datei dauert der Scan ca. 10–25 Minuten (I/O-bound)
- Der Fortschritt wird im Protokoll angezeigt (alle 10%)
- Falls kein Audio vorhanden ist oder keine Abweichung erkannt wird, hat die Option keinen Effekt

> **Hinweis:** Diese Option ist standardmäßig deaktiviert, da der Frame-Scan bei sehr großen Dateien auf externen Festplatten einige Zeit dauern kann.

#### Halbzeiten zusammenführen

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Halbzeiten zusammenführen** | aus | Erkennt zusammengehörige Halbzeiten und fügt sie zu einem Video zusammen |
| **Titelkarten-Dauer** | 3 s | Dauer der Titelkarte zwischen den Halbzeiten |
| **Hintergrundfarbe** | #000000 | Hintergrund der Titelkarte |
| **Textfarbe** | #FFFFFF | Textfarbe der Titelkarte |

Wenn aktiviert, werden Dateien mit ähnlichem Namen automatisch als Halbzeiten gruppiert (z. B. `spiel_1.mjpg` und `spiel_2.mjpg`). Zwischen den Halbzeiten wird eine Titelkarte eingefügt (z. B. „1. Halbzeit", „2. Halbzeit").

### Einstellungen → Audio

Steuert die Audio-Verarbeitung:

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Audio einbinden** | ✓ | Ob die WAV-Datei eingebunden werden soll |
| **Audio verstärken** | ✓ | Wendet compand+loudnorm Filterchain an |
| **Audio-Suffix** | _(leer)_ | Suffix für alternative WAV-Dateien (z. B. `_normalized`) |
| **Audio-Bitrate** | 192k | AAC-Bitrate (96k, 128k, 192k, 256k, 320k) |
| **Compand-Punkte** | `-70/-60\|-30/-10` | Dynamische Kompressions-Kennlinie |

#### Audio-Suffix erklärt

Wenn die WAV-Datei nicht exakt den gleichen Namen wie die MJPG-Datei hat, kann ein Suffix angegeben werden:

- MJPG-Datei: `aufnahme_2026-02-07.mjpg`
- Suffix `_normalized` → sucht: `aufnahme_2026-02-07_normalized.wav`

### Einstellungen → YouTube

Steuert die Erstellung und den Upload von YouTube-Versionen:

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **YouTube-Version erstellen** | aus | Erstellt zusätzlich eine `*_youtube.mp4` |
| **CRF** | 23 | Qualität der YouTube-Version |
| **Max. Bitrate** | 8M | Maximale Bitrate |
| **Buffer-Größe** | 16M | VBV-Buffergröße |
| **Audio-Bitrate** | 128k | AAC-Bitrate der YouTube-Version |
| **YouTube hochladen** | aus | Upload auf YouTube (erfordert [API-Credentials](youtube_credentials.md)) |

---

## Jobs bearbeiten

Per Doppelklick auf einen Job oder über den Button **Bearbeiten** öffnet sich ein Dialog zur Eingabe von YouTube-Metadaten:

| Feld | Beschreibung |
|------|--------------|
| **YouTube-Titel** | Titel des Videos auf YouTube |
| **Playlist** | Name der Ziel-Playlist |

Diese Felder werden pro Job gesetzt und beim Upload verwendet.

---

## Status-Werte

Jeder Job hat einen der folgenden Status:

| Status | Bedeutung |
|--------|-----------|
| **Wartend** | Noch nicht verarbeitet |
| **Läuft** | Wird gerade konvertiert (mit Fortschrittsbalken) |
| **Fertig** | Erfolgreich konvertiert |
| **Übersprungen** | Ausgabedatei existiert bereits (und Überschreiben ist deaktiviert) |
| **Fehler** | Konvertierung fehlgeschlagen (Details im Log) |

---

## Einstellungen-Datei

Alle Einstellungen werden automatisch in `convert_mjpeg_settings.json` gespeichert (im Projektverzeichnis neben `main.py`). Die Datei wird beim Starten geladen und bei jeder Änderung aktualisiert.

Beispiel:

```json
{
  "video": {
    "fps": 25,
    "output_format": "mp4",
    "crf": 18,
    "lossless": false,
    "preset": "medium",
    "encoder": "auto",
    "profile": "Benutzerdefiniert",
    "overwrite": false,
    "audio_sync": false,
    "merge_halves": false,
    "merge_title_duration": 3,
    "merge_title_bg": "#000000",
    "merge_title_fg": "#FFFFFF"
  },
  "audio": {
    "include_audio": true,
    "amplify_audio": true,
    "audio_suffix": "",
    "audio_bitrate": "192k",
    "compand_points": "-70/-60|-30/-10"
  },
  "youtube": {
    "create_youtube": false,
    "youtube_crf": 23,
    "youtube_maxrate": "8M",
    "youtube_bufsize": "16M",
    "youtube_audio_bitrate": "128k",
    "upload_to_youtube": false
  },
  "last_directory": "/media/videos/Aufnahmen"
}
```

> Die Datei kann auch manuell bearbeitet werden. Ungültige Werte werden beim Laden ignoriert und durch Standardwerte ersetzt.

---

## Fehlerbehebung

### Allgemein

| Problem | Lösung |
|---------|--------|
| GUI startet nicht | `python3 -c "import PySide6"` testen. Falls fehlt: `pip install PySide6` |
| ffmpeg nicht gefunden | `ffmpeg -version` prüfen. Installieren: `sudo apt install ffmpeg` |
| Keine WAV gefunden | Prüfe, dass die WAV-Datei im gleichen Ordner liegt und den gleichen Dateinamen hat. Ggf. *Audio-Suffix* setzen |
| Konvertierung bricht ab | Details im Protokoll-Bereich (unten). Häufig: Zu wenig Speicherplatz oder beschädigte Eingabedatei |

### GPU / NVENC

| Problem | Lösung |
|---------|--------|
| 🔴 „Keine NVIDIA-GPU gefunden" | `nvidia-smi` im Terminal testen. NVIDIA-Treiber installieren |
| 🔴 „Treiber zu alt" | Treiber ≥ 550.54 installieren (`sudo apt install nvidia-driver-550` o. ä.) |
| 🔴 „ffmpeg ohne NVENC" | ffmpeg mit NVENC-Support installieren (z. B. `ffmpeg` aus offiziellen Quellen) |
| 🔴 „Test-Encode fehlgeschlagen" | Details im Tooltip beachten. Häufig: veraltete NVENC-API-Version im Treiber |
| Encoder fällt auf CPU zurück | Expected Behavior bei `auto`: im Protokoll erscheint ein Hinweis dazu |

> **Tipp:** Die GPU-Diagnose im Video-Einstellungsdialog zeigt exakt an, welcher Schritt fehlschlägt und was zu tun ist.
