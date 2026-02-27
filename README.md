# Fussballverein – Video Manager

Grafische Oberfläche für die MJPEG-Konvertierung und den Download von Videos von Raspberry Pi
Kamera-Systemen (Kaderblick). Bietet eine komfortable Qt-GUI (PySide6) mit Jobliste, Profilen,
GPU-Beschleunigung, Raspberry Pi Download, Halbzeit-Zusammenführung, persistenten Einstellungen
und Hintergrund-Verarbeitung.

Weiterführende Doku: [YouTube API – Credentials einrichten](docs/youtube_credentials.md)

---

## Features

- **Jobliste** – Dateien und Ordner per Dialog hinzufügen, als Queue abarbeiten
- **Profile** – Vorkonfigurierte Einstellungen: *KI Auswertung*, *YouTube*, *Benutzerdefiniert*
- **Hardware-Encoding** – NVIDIA NVENC-Beschleunigung mit automatischer Erkennung und Fallback auf CPU
- **GPU-Diagnose** – Detaillierte Statusanzeige mit Lösungsvorschlägen bei Problemen
- **Raspberry Pi Download** – Videos direkt von angebundenen Kamera-Systemen per SFTP herunterladen
- **Halbzeiten zusammenführen** – Automatische Erkennung und Zusammenführung mit Titelkarten
- **Einstellungs-Dialoge** – Video, Audio und YouTube werden in separaten Dialogen konfiguriert
- **Persistente Einstellungen** – Alle Settings werden in `settings.json` gespeichert
- **Hintergrund-Verarbeitung** – ffmpeg läuft in einem Worker-Thread, die GUI bleibt bedienbar
- **Fortschrittsanzeige** – Statusbar mit Fortschrittsbalken und ETA-Anzeige
- **Protokoll** – Scrollbares Log mit detaillierten Meldungen
- **Abbruch-Funktion** – Laufende Konvertierungen oder Downloads abbrechen
- **YouTube-Upload** – Automatischer Upload mit Playlist-Verwaltung (Playlist wird bei Bedarf angelegt)
- **Download → Konvertierung → Upload** – Durchgängige Pipeline: Pi-Downloads, Konvertierung und YouTube-Upload in einem Durchlauf

---

## Voraussetzungen

- **Python** ≥ 3.10
- **ffmpeg** und **ffprobe** (für die Video-Konvertierung)
- *Optional:* **NVIDIA-GPU** mit Treiber ≥ 550.54 für Hardware-Encoding (NVENC)
- *Optional:* SSH-Zugang zu den Raspberry Pi Kameras (für den Download)

---

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

---

## Starten

```bash
python main.py
```

---

## Projektstruktur

```
video-manager/
├── README.md                       <- Diese Datei
├── cameras.yaml                    <- Kamera-Konfiguration (Raspberry Pis)
├── settings.json                   <- Persistente GUI-Einstellungen (automatisch)├── session.json                    ← Letzte Jobliste (automatisch, für Session-Restore)├── client_secret.json              <- YouTube OAuth (manuell, nicht im Git)
├── youtube_token.json              <- YouTube OAuth-Token (automatisch, nicht im Git)
├── docs/
│   └── youtube_credentials.md     <- Doku: YouTube-API-Setup
├── main.py                         <- GUI-Einstiegspunkt
├── src/                            <- Anwendungspaket
│   ├── __init__.py
│   ├── app.py                      <- Hauptfenster (QMainWindow)
│   ├── converter.py                <- Konvertierungslogik und Job-Datenklasse
│   ├── delegates.py                <- Fortschrittsbalken in der Tabelle
│   ├── diagnostics.py              <- GPU- und System-Diagnose
│   ├── dialogs.py                  <- Einstellungs- und Bearbeitungsdialoge
│   ├── download_dialog.py          <- Dialog: Raspberry Pi Download
│   ├── download_worker.py          <- Worker-Thread: SFTP-Download
│   ├── downloader.py               <- SFTP-Download-Logik (paramiko)
│   ├── encoder.py                  <- Encoder-Auflösung und ffmpeg-Argumente
│   ├── ffmpeg_runner.py            <- ffmpeg-Prozesssteuerung
│   ├── merge.py                    <- Halbzeiten zusammenführen
│   ├── settings.py                 <- Einstellungen, Profile, Persistenz
│   ├── worker.py                   <- Worker-Thread: Konvertierung
│   └── youtube.py                  <- YouTube-Upload und OAuth
└── requirements.txt                <- Python-Abhängigkeiten
```

---

## Benutzeroberfläche

### Hauptfenster

```
+-----------------------------------------------------------------+
|  Menü: Datei | Einstellungen                                    |
+-----------------------------------------------------------------+
|  Toolbar: [＋ Dateien] [＋ Ordner] [＋ Pi-Download]              |
|           [▶ Starten] [■ Abbrechen] [Bearbeiten] [Entfernen]   |
+-----------------------------------------------------------------+
|  Auftragsliste                                                  |
|  #  | Typ            | Beschreibung          | Status | YT-Titel|
|  1  | ⬇ Download     | Kamera1  →  /ziel/    | Wartend| Spiel1  |
|  2  | ⬇ Download     | Kamera2  →  /ziel/    | Wartend| Spiel1  |
|  3  | 🔄 Konvertieren | aufnahme_1.mjpg       | ████65%|         |
|  4  | 🔄 Konvertieren | aufnahme_2.mjpg       | Fertig | Spiel2  |
+-----------------------------------------------------------------+
|  Protokoll (scrollbares Log)                                    |
|  ⬇ Download von 2 Kamera(s)  →  /ziel/                         |
|  === [1/2] aufnahme_1.mjpg ===                                  |
|  Encoder: h264_nvenc (NVIDIA GPU)                               |
|  Fertig: aufnahme_1.mp4 (234 MB, 45s)                          |
+-----------------------------------------------------------------+
|  Statusbar  [████████░░░░] 2/3  ETA 12s                        |
+-----------------------------------------------------------------+
```

### Toolbar-Buttons

| Button | Funktion |
|--------|----------|
| **＋ Dateien** | Öffnet Dateidialog zum Auswählen von `.mjpg`/`.mjpeg`-Dateien |
| **＋ Ordner** | Fügt alle MJPEG-Dateien eines Ordners hinzu |
| **＋ Pi-Download** | Legt Download-Jobs für alle konfigurierten Kameras an |
| **▶ Starten** | Startet die gesamte Pipeline (Downloads → Konvertierung → Upload) |
| **■ Abbrechen** | Bricht laufende Verarbeitung ab |
| **Bearbeiten** | Öffnet YouTube-Metadaten für den ausgewählten Job (Download oder Konvertierung) |
| **Entfernen** | Entfernt ausgewählte Jobs aus der Liste (Mehrfachauswahl möglich) |

> **Tipp:** Jobs können auch über **Datei → Alle Jobs entfernen** komplett gelöscht werden.

### Menü

| Menü | Eintrag | Funktion |
|------|---------|----------|
| Datei | Dateien hinzufügen … (Strg+O) | MJPEG-Dateien einzeln auswählen |
| Datei | Ordner hinzufügen … (Strg+D) | Ordner mit MJPEG-Dateien hinzufügen |
| Datei | Pi-Downloads hinzufügen (Strg+P) | Download-Jobs für konfigurierte Kameras anlegen |
| Datei | Jobliste exportieren … (Strg+E) | Aktuelle Jobliste als JSON-Datei speichern |
| Datei | Jobliste importieren … (Strg+I) | Jobliste aus einer JSON-Datei laden (Einträge werden angehängt) |
| Datei | Alle Jobs entfernen | Jobliste leeren |
| Einstellungen | Video … | Video-Kodierung konfigurieren |
| Einstellungen | Audio … | Audio-Verarbeitung konfigurieren |
| Einstellungen | YouTube … | YouTube-Upload konfigurieren |
| Einstellungen | Kameras … | Raspberry Pi Geräte verwalten |
| Einstellungen | Allgemein … | Session-Wiederherstellung und allgemeine Optionen |

---

## Raspberry Pi Download

Über **＋ Pi-Download** in der Toolbar oder **Datei → Pi-Downloads hinzufügen** werden
Download-Jobs für alle konfigurierten Kameras in die Jobliste eingetragen.

### Kamera-Konfiguration

Geräte werden über **Einstellungen → Kameras** verwaltet. Dort können Geräte angelegt, bearbeitet
und gelöscht werden. Optional lassen sich Geräte aus einer bestehenden `cameras.yaml` importieren.

Jedes Gerät benötigt:

| Feld | Beschreibung |
|------|-------------|
| **Name** | Anzeigename; wird als Unterordner im Zielverzeichnis verwendet |
| **IP** | IP-Adresse des Raspberry Pi |
| **Port** | SSH-Port (Standard: 22) |
| **Benutzername** | SSH-Benutzername |
| **Passwort** | SSH-Passwort (optional, wenn SSH-Key gesetzt) |
| **SSH-Key** | Pfad zum privaten SSH-Key (optional) |

Zusätzlich werden in den Kamera-Einstellungen **Quellverzeichnis** (auf den Pis) und
**Zielverzeichnis** (lokal) sowie die Option **Nach Download löschen** konfiguriert.

### Download-Workflow

1. **＋ Pi-Download** → Download-Jobs erscheinen in der Jobliste (Typ: ⬇ Download)
2. Jobs **bearbeiten** → YouTube-Titel und Playlist-Name setzen
3. **▶ Starten** → Downloads laufen, anschließend werden automatisch Konvertier-Jobs erzeugt
4. Konvertier-Jobs **erben** YouTube-Titel und Playlist vom zugehörigen Download-Job
5. Konvertierung und ggf. YouTube-Upload laufen automatisch durch

### Download-Verhalten

- Es werden nur **vollständige Aufnahmen** heruntergeladen (`.mjpg` **und** `.wav` müssen vorhanden sein)
- Bereits vorhandene Dateien werden per **Größenvergleich** geprüft und ggf. übersprungen
- Fehler bei einem Gerät unterbrechen **nicht** den Download der anderen Geräte
- Jede Kamera erhält einen eigenen Unterordner (`<Ziel>/<Kameraname>/`)

---

## Menü: Einstellungen

### Einstellungen → Video

Steuert die Video-Kodierung. Am oberen Rand des Dialogs befindet sich die **Profil-Auswahl** und die **GPU-Statusanzeige**.

#### Profile

| Profil | Beschreibung |
|--------|--------------|
| **KI Auswertung** | CRF 12, Preset slow – hohe Qualität für Spielanalyse mit 5–8x Zoom |
| **YouTube** | CRF 23, Preset medium – optimiert für YouTube-Upload |
| **Benutzerdefiniert** | Alle Werte frei einstellbar |

#### Encoder / GPU-Beschleunigung

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Encoder** | auto | `auto` = beste verfügbare Option, `h264_nvenc` = NVIDIA GPU, `libx264` = CPU |

Bei `auto` wird beim Start automatisch geprüft, ob NVENC verfügbar ist. Bei Problemen erfolgt
Fallback auf `libx264` mit Hinweis im Protokoll.

#### GPU-Statusanzeige

- 🟢 **GPU bereit** – NVENC ist verfügbar und funktionsfähig
- 🔴 **GPU nicht verfügbar** – mit Erklärung und Lösungsvorschlag im Tooltip

Die Diagnose prüft in vier Schritten: GPU vorhanden? → Treiber ≥ 550.54? → ffmpeg mit NVENC? → Test-Encode erfolgreich?

#### Video-Einstellungen

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Framerate (FPS)** | 25 | Framerate der Eingabedatei |
| **Ausgabeformat** | mp4 | `mp4` (H.264) oder `avi` (MJPEG) |
| **CRF (Qualität)** | 18 | 0=verlustfrei · 18=sehr gut · 23=Standard · 51=schlechteste |
| **Preset** | medium | ffmpeg-Preset (ultrafast … veryslow). Langsamer = kleinere Datei |
| **Verlustfrei** | aus | Aktiviert CRF=0 und Preset=slow |
| **Audio-Video-Sync** | aus | Korrigiert Drift durch Frame-Drops (zählt alle Frames, passt FPS an Audio-Dauer an) |
| **Überschreiben** | aus | Vorhandene Ausgabedateien überschreiben |

> **Tipp:** Für Spielanalyse mit bis zu 8x Zoom empfiehlt sich CRF ≤ 18 oder das Profil *KI Auswertung*.

#### Audio-Video-Sync (Frame-Drop-Korrektur)

MJPEG-Aufnahmen können durch Frame-Drops weniger Frames enthalten als erwartet. Mit fester Framerate
entsteht eine zunehmende Desynchronisation mit der Audio-Spur. Bei aktiviertem **Audio-Video-Sync**
wird die MJPEG-Datei vorab komplett gelesen, alle JPEG-SOI-Marker gezählt und die Input-Framerate
so angepasst, dass Video-Dauer = Audio-Dauer.

- Bei einer 222 GB-Datei dauert der Scan ca. 10–25 Minuten (I/O-bound)
- Fortschritt wird im Protokoll angezeigt (alle 10%)
- Hat keinen Effekt, wenn kein Audio vorhanden oder keine Abweichung erkannt wird

#### Halbzeiten zusammenführen

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Halbzeiten zusammenführen** | aus | Erkennt zusammengehörige Halbzeiten und fügt sie zusammen |
| **Titelkarten-Dauer** | 3 s | Dauer der Titelkarte zwischen den Halbzeiten |
| **Hintergrundfarbe** | #000000 | Hintergrund der Titelkarte |
| **Textfarbe** | #FFFFFF | Textfarbe der Titelkarte |

### Einstellungen → Audio

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **Audio einbinden** | an | Ob die WAV-Datei eingebunden werden soll |
| **Audio verstärken** | an | Wendet compand+loudnorm Filterchain an |
| **Audio-Suffix** | _(leer)_ | Suffix für alternative WAV-Dateien (z. B. `_normalized`) |
| **Audio-Bitrate** | 192k | AAC-Bitrate (96k, 128k, 192k, 256k, 320k) |
| **Compand-Punkte** | `-70/-60\|-30/-10` | Dynamische Kompressions-Kennlinie |

Wenn die WAV-Datei einen abweichenden Namen hat: MJPG `aufnahme.mjpg` + Suffix `_norm` → sucht `aufnahme_norm.wav`.

### Einstellungen → YouTube

| Einstellung | Standard | Beschreibung |
|-------------|----------|--------------|
| **YouTube-Version erstellen** | aus | Erstellt zusätzlich eine `*_youtube.mp4` |
| **CRF** | 23 | Qualität der YouTube-Version |
| **Max. Bitrate** | 8M | Maximale Bitrate |
| **Buffer-Größe** | 16M | VBV-Buffergröße |
| **Audio-Bitrate** | 128k | AAC-Bitrate der YouTube-Version |
| **YouTube hochladen** | aus | Upload auf YouTube (erfordert [API-Credentials](docs/youtube_credentials.md)) |

---

## Jobs bearbeiten

Per Doppelklick auf einen Job oder über **Bearbeiten** öffnet sich ein Dialog zur Eingabe von
YouTube-Metadaten. Beide Job-Typen (Download und Konvertierung) können bearbeitet werden.

| Feld | Beschreibung |
|------|-------------|
| **YouTube-Titel** | Titel des Videos auf YouTube (max. 100 Zeichen). Bei Download-Jobs wird der Titel auf alle daraus erzeugten Konvertier-Jobs übertragen. |
| **Playlist** | **Name** der YouTube-Playlist (nicht die ID). Die App sucht automatisch nach einer existierenden Playlist mit diesem Namen. Wird keine gefunden, wird sie als *nicht gelistet* neu angelegt. |

> **Hinweis:** Bei Download-Jobs fungieren die Metadaten als Vorlage – alle automatisch erzeugten
> Konvertier-Jobs erben YouTube-Titel und Playlist-Name vom jeweiligen Download-Job.

---

## Jobliste importieren / exportieren

Die gesamte Jobliste kann als JSON-Datei gespeichert und wieder geladen werden:

- **Datei → Jobliste exportieren … (Strg+E)** – Speichert alle aktuellen Jobs in eine `.json`-Datei
- **Datei → Jobliste importieren … (Strg+I)** – Lädt Jobs aus einer `.json`-Datei und hängt sie an die bestehende Liste an

So lassen sich vorbereitete Joblisten teilen oder für wiederkehrende Aufgaben wiederverwenden.

---

## Session wiederherstellen

Beim Beenden der App wird die aktuelle Jobliste automatisch als `session.json` gespeichert.
Unter **Einstellungen → Allgemein** kann die Option **„Letzte Jobliste beim Start wiederherstellen"**
aktiviert werden. Dann wird beim nächsten Programmstart die gespeicherte Jobliste automatisch geladen.

| Datei | Beschreibung |
|-------|-------------|
| `session.json` | Wird beim Beenden automatisch geschrieben; enthält die Jobliste als JSON |

> **Tipp:** Auch ohne aktivierte Option bleibt `session.json` erhalten und kann jederzeit manuell
> über **Datei → Jobliste importieren** geladen werden.

---

## Status-Werte

| Status | Bedeutung |
|--------|-----------|
| **Wartend** | Noch nicht verarbeitet |
| **Herunterladen** | Download vom Raspberry Pi läuft |
| **Heruntergeladen** | Download abgeschlossen, Konvertierung folgt |
| **Läuft** | Wird gerade konvertiert (mit Fortschrittsbalken) |
| **Fertig** | Erfolgreich verarbeitet |
| **Übersprungen** | Ausgabedatei existiert bereits (Überschreiben deaktiviert) |
| **Fehler** | Verarbeitung fehlgeschlagen (Details im Log) |

---

## Einstellungen-Datei `settings.json`

Alle Einstellungen werden automatisch in `settings.json` gespeichert und beim Starten geladen.
Die Datei kann manuell bearbeitet werden – ungültige Werte werden durch Standardwerte ersetzt.

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
  "last_directory": "/media/videos/Aufnahmen",
  "restore_session": false
}
```

---

## Fehlerbehebung

### Allgemein

| Problem | Lösung |
|---------|--------|
| GUI startet nicht | `python3 -c "import PySide6"` testen; ggf. `pip install -r requirements.txt` |
| ffmpeg nicht gefunden | `ffmpeg -version` prüfen; installieren: `sudo apt install ffmpeg` |
| Keine WAV gefunden | WAV muss im gleichen Ordner liegen und gleichen Dateinamen haben; ggf. *Audio-Suffix* setzen |
| Konvertierung bricht ab | Details im Protokoll; häufig: zu wenig Speicherplatz oder beschädigte Eingabedatei |

### GPU / NVENC

| Problem | Lösung |
|---------|--------|
| 🔴 Keine NVIDIA-GPU gefunden | `nvidia-smi` im Terminal testen; NVIDIA-Treiber installieren |
| 🔴 Treiber zu alt | Treiber ≥ 550.54 installieren (`sudo apt install nvidia-driver-550`) |
| 🔴 ffmpeg ohne NVENC | ffmpeg mit NVENC-Support installieren |
| 🔴 Test-Encode fehlgeschlagen | Tooltip beachten; häufig: veraltete NVENC-API-Version |
| Encoder fällt auf CPU zurück | Expected Behavior bei `auto`; Hinweis erscheint im Protokoll |

### Raspberry Pi Download

| Problem | Lösung |
|---------|--------|
| Verbindung fehlgeschlagen | IP und Port in `cameras.yaml` prüfen; SSH-Zugang testen: `ssh user@ip` |
| Authentifizierungsfehler | Benutzername/Passwort prüfen oder `ssh_key` in `cameras.yaml` eintragen |
| Keine Aufnahmen gefunden | `source`-Pfad prüfen; auf jedem Pi muss je Aufnahme `.mjpg` + `.wav` vorhanden sein |
| Download bricht ab | Netzwerkverbindung prüfen; partiell heruntergeladene Dateien werden automatisch gelöscht |
| `cameras.yaml` nicht gefunden | Datei muss im Projektverzeichnis liegen oder im Dialog manuell auswählen |
