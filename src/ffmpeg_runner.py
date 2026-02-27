"""ffmpeg-Prozesssteuerung mit Fortschrittsanzeige und Abbruch.

Stellt low-level Funktionen bereit:
  - ``run_ffmpeg()`` – Prozess starten, stderr parsen, Fortschritt melden
  - ``get_duration()`` / ``get_resolution()`` – ffprobe-Helfer
  - ``find_audio()`` – zugehörige WAV-Datei suchen
  - ``estimate_duration_from_filesize()`` – Heuristik für MJPEG-Rohstreams
"""

import os
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional

_RE_TIME = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")


# ═════════════════════════════════════════════════════════════════
#  Hilfsfunktionen (ffprobe, Audio-Suche)
# ═════════════════════════════════════════════════════════════════

def find_audio(mjpg_path: Path, suffix: str = "") -> Optional[Path]:
    """Sucht die zugehörige WAV-Datei zu einer MJPEG-Datei."""
    stem = mjpg_path.stem
    parent = mjpg_path.parent
    if suffix:
        wav = parent / f"{stem}{suffix}.wav"
        if wav.exists():
            return wav
    wav = parent / f"{stem}.wav"
    if wav.exists():
        return wav
    for candidate in sorted(parent.glob(f"{stem}*.wav")):
        return candidate
    return None


def get_duration(filepath: Path) -> Optional[float]:
    """Ermittelt die Dauer einer Mediendatei via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(filepath)],
            capture_output=True, text=True, timeout=30,
        )
        val = result.stdout.strip()
        return float(val) if val and val != "N/A" else None
    except Exception:
        return None


def get_resolution(filepath: Path) -> Optional[tuple[int, int]]:
    """Ermittelt die Auflösung (width, height) eines Videos via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", str(filepath)],
            capture_output=True, text=True, timeout=30,
        )
        parts = result.stdout.strip().split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def estimate_duration_from_filesize(filepath: Path, fps: int) -> Optional[float]:
    """Schätzt die Dauer einer MJPEG-Datei anhand Dateigröße + Auflösung.

    Empirische Werte für MJPEG (~0,3 Bytes pro Pixel):
      - 1080p (1920×1080): ~620 KB/Frame
      - 4K   (3840×2160): ~2,5 MB/Frame
    """
    try:
        size = filepath.stat().st_size
        if size <= 0 or fps <= 0:
            return None

        resolution = get_resolution(filepath)
        if resolution:
            w, h = resolution
            avg_frame_bytes = int(w * h * 0.3)
        else:
            avg_frame_bytes = 120_000  # Fallback

        est_frames = size / avg_frame_bytes
        return est_frames / fps
    except Exception:
        return None


def count_frames(filepath: Path,
                 cancel_flag: Optional[threading.Event] = None,
                 log_callback=None) -> Optional[int]:
    """Zählt alle JPEG-SOI-Marker (0xFF 0xD8) in einer MJPEG-Datei.

    Liest die gesamte Datei in 64-MB-Blöcken und zählt exakt alle
    Frames.  JPEG Byte-Stuffing garantiert, dass 0xFF 0xD8 nur als
    SOI-Marker auftreten kann (innerhalb der Entropie-Daten wird
    jedes 0xFF zu 0xFF 0x00 escaped).

    Bei großen Dateien (>200 GB) auf externen HDDs kann dies
    15-25 Minuten dauern.  Fortschritt wird über log_callback
    gemeldet, Abbruch über cancel_flag.
    """
    _SOI = b'\xff\xd8'
    _CHUNK = 64 * 1024 * 1024  # 64 MB
    try:
        file_size = filepath.stat().st_size
        if file_size <= 0:
            return None

        if log_callback:
            size_gb = file_size / (1024 ** 3)
            log_callback(f"Zähle Frames ({size_gb:.1f} GB) für Audio-Sync …")

        total_soi = 0
        bytes_read = 0
        last_pct = -1
        prev_tail = b''  # letztes Byte des vorigen Chunks (Grenzfall)
        with open(filepath, 'rb') as f:
            while True:
                if cancel_flag and cancel_flag.is_set():
                    return None
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                # Grenzfall: SOI-Marker über Chunk-Grenze hinweg
                if prev_tail == b'\xff' and chunk[:1] == b'\xd8':
                    total_soi += 1
                total_soi += chunk.count(_SOI)
                prev_tail = chunk[-1:] if chunk else b''
                bytes_read += len(chunk)
                pct = int(bytes_read * 100 / file_size)
                if log_callback and pct >= last_pct + 10:
                    last_pct = pct
                    log_callback(f"  Frame-Scan: {pct}% "
                                 f"({total_soi:,} Frames bisher)")

        if total_soi < 2:
            return None

        if log_callback:
            log_callback(f"Frame-Scan abgeschlossen: "
                         f"{total_soi:,} Frames gezählt")
        return total_soi
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════
#  ffmpeg-Prozess mit Fortschritts-Parsing
# ═════════════════════════════════════════════════════════════════

def run_ffmpeg(cmd: list, duration: Optional[float] = None,
               cancel_flag: Optional[threading.Event] = None,
               log_callback=None,
               progress_callback=None) -> int:
    """Führt ffmpeg als Popen aus mit Fortschrittsanzeige und Abbruch.

    Liest den Fortschritt aus der stderr-Statuszeile (``time=HH:MM:SS.xx``),
    weil ``-progress pipe:1`` bei MJPEG-Rohdaten nur N/A liefert.

    Args:
        cmd: ffmpeg-Kommandozeile.
        duration: Geschätzte Gesamtdauer in Sekunden (für %-Berechnung).
        cancel_flag: threading.Event – wenn gesetzt, wird der Prozess abgebrochen.
        log_callback: Callable für Log-Nachrichten.
        progress_callback: Callable(percent: int) für Fortschritt 0–100.

    Returns:
        Exit-Code des Prozesses (``-1`` bei Abbruch).
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # stderr im Binary-Mode lesen weil ffmpeg \r ohne \n benutzt
        text=False,
        # Eigene Prozessgruppe damit wir den ganzen ffmpeg-Baum killen können
        preexec_fn=os.setsid,
    )

    cancelled = False

    # Watcher-Thread: überwacht das cancel_flag unabhängig von der
    # Read-Schleife und killt den Prozess sofort.
    def _cancel_watcher():
        nonlocal cancelled
        if not cancel_flag:
            return
        while proc.poll() is None:
            if cancel_flag.wait(timeout=0.25):
                cancelled = True
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                return

    watcher = threading.Thread(target=_cancel_watcher, daemon=True)
    watcher.start()

    last_pct = -1
    stderr_tail: list[str] = []  # letzte Fehlerzeilen für Logging

    try:
        # ffmpeg schreibt Statusupdates auf stderr mit \r (kein \n).
        # Wir lesen block-weise in einen Puffer und splitten bei \r oder \n.
        buf = b""
        while True:
            chunk = proc.stderr.read(512)
            if not chunk:
                break
            buf += chunk
            while b"\r" in buf or b"\n" in buf:
                idx_r = buf.find(b"\r")
                idx_n = buf.find(b"\n")
                if idx_r == -1:
                    idx = idx_n
                elif idx_n == -1:
                    idx = idx_r
                else:
                    idx = min(idx_r, idx_n)

                line_bytes = buf[:idx]
                # \r\n als ein Trenner behandeln
                if idx < len(buf) - 1 and buf[idx:idx + 2] == b"\r\n":
                    buf = buf[idx + 2:]
                else:
                    buf = buf[idx + 1:]

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # Statuszeile mit time= parsen
                m = _RE_TIME.search(line)
                if m and duration and duration > 0:
                    h, mi, s, frac = m.groups()
                    current_secs = (int(h) * 3600 + int(mi) * 60
                                    + int(s) + int(frac) / (10 ** len(frac)))
                    pct = min(99, int(current_secs / duration * 100))
                    if pct != last_pct:
                        last_pct = pct
                        if progress_callback:
                            progress_callback(pct)

                # Nicht-Statuszeilen für Fehlerlog merken
                if "time=" not in line and "frame=" not in line:
                    stderr_tail.append(line)
                    if len(stderr_tail) > 10:
                        stderr_tail.pop(0)

        proc.wait()
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.wait()

    watcher.join(timeout=2)

    if cancelled:
        return -1

    # Erfolg → 100 %
    if proc.returncode == 0 and progress_callback:
        progress_callback(100)

    # Bei Fehler stderr ausgeben
    if proc.returncode != 0 and log_callback and stderr_tail:
        for err_line in stderr_tail[-5:]:
            log_callback(f"  {err_line}")

    return proc.returncode
