"""Halbzeiten zusammenführen mit Titelkarten."""

import tempfile
import threading
from pathlib import Path
from typing import Optional

from .settings import AppSettings
from .ffmpeg_runner import run_ffmpeg, get_duration, get_resolution


# ═════════════════════════════════════════════════════════════════
#  Hilfsfunktionen
# ═════════════════════════════════════════════════════════════════

def _get_video_dimensions(filepath: Path) -> tuple[int, int]:
    """Gibt (width, height) eines Videos zurück, Fallback (1920, 1080)."""
    res = get_resolution(filepath)
    return res if res else (1920, 1080)


def _make_half_labels(count: int) -> list[str]:
    """Erzeugt Beschriftungen für die Halbzeiten/Teile."""
    if count == 2:
        return ["1. Halbzeit", "2. Halbzeit"]
    elif count == 3:
        return ["1. Halbzeit", "2. Halbzeit", "Verlängerung"]
    else:
        return [f"{i + 1}. Teil" for i in range(count)]


def _generate_title_card(output_path: Path, text: str,
                         duration: int, width: int, height: int,
                         fps: int, bg_color: str = "#000000",
                         fg_color: str = "#FFFFFF",
                         cancel_flag: Optional[threading.Event] = None,
                         log_callback=None) -> bool:
    """Erzeugt ein kurzes Titelbild-Video (einfarbiger Hintergrund + Text)."""
    fontsize = max(48, min(height // 8, 200))

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi",
        "-i", (
            f"color=c={bg_color}:size={width}x{height}"
            f":duration={duration}:rate={fps}"
        ),
        # Stille als Audio
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(duration),
        "-vf", (
            f"drawtext=text='{text}'"
            f":fontsize={fontsize}"
            f":fontcolor={fg_color}"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
            f":font=Sans"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(output_path),
    ]

    rc = run_ffmpeg(cmd, duration=float(duration),
                    cancel_flag=cancel_flag, log_callback=log_callback)
    return rc == 0


# ═════════════════════════════════════════════════════════════════
#  Merge-Logik
# ═════════════════════════════════════════════════════════════════

def merge_halves(jobs: list, settings: AppSettings,
                 cancel_flag: Optional[threading.Event] = None,
                 log_callback=None,
                 progress_callback=None) -> list[Path]:
    """Gruppiert fertige Jobs nach Ordner und merged sie mit Titelkarten.

    Returns:
        Liste der erzeugten Merge-Dateien (Pfade).
    """
    vs = settings.video

    def log(msg: str):
        if log_callback:
            log_callback(msg)

    # Nur erfolgreich konvertierte MP4-Jobs
    finished = [j for j in jobs if j.status == "Fertig"
                and j.output_path and j.output_path.exists()
                and j.output_path.suffix.lower() == ".mp4"]

    if not finished:
        log("Merge: Keine fertigen MP4-Dateien zum Zusammenführen.")
        return []

    # Nach Quell-Ordner gruppieren
    groups: dict[Path, list] = {}
    for job in finished:
        folder = job.source_path.parent
        groups.setdefault(folder, []).append(job)

    # Jede Gruppe nach Dateiname (≈ Zeitstempel) sortieren
    for folder in groups:
        groups[folder].sort(key=lambda j: j.source_path.name)

    merged_files: list[Path] = []
    group_idx = 0
    total_groups = len(groups)

    for folder, group_jobs in groups.items():
        if cancel_flag and cancel_flag.is_set():
            break

        if len(group_jobs) < 2:
            log(f"Merge: {folder.name} – nur {len(group_jobs)} Datei, "
                f"übersprungen")
            group_idx += 1
            continue

        folder_name = folder.name
        merge_name = f"{folder_name}_komplett.mp4"
        merge_path = group_jobs[0].output_path.parent / merge_name

        if merge_path.exists() and not vs.overwrite:
            log(f"Merge: {merge_name} existiert bereits, übersprungen")
            merged_files.append(merge_path)
            group_idx += 1
            continue

        log(f"\n══ Merge: {folder_name} ({len(group_jobs)} Teile) ══")

        # Auflösung + FPS vom ersten Video übernehmen
        first_mp4 = group_jobs[0].output_path
        w, h = _get_video_dimensions(first_mp4)
        fps = vs.fps

        # Titelkarten erzeugen + Concat-Liste aufbauen
        tmpdir = Path(tempfile.mkdtemp(prefix="merge_"))
        concat_parts: list[Path] = []
        half_labels = _make_half_labels(len(group_jobs))

        for i, job in enumerate(group_jobs):
            if cancel_flag and cancel_flag.is_set():
                break

            label = half_labels[i]
            title_path = tmpdir / f"title_{i:02d}.mp4"

            log(f"  Erstelle Titelkarte: \"{label}\"")
            ok = _generate_title_card(
                title_path, label,
                duration=vs.merge_title_duration,
                width=w, height=h, fps=fps,
                bg_color=vs.merge_title_bg,
                fg_color=vs.merge_title_fg,
                cancel_flag=cancel_flag,
                log_callback=log_callback)

            if not ok:
                log("  FEHLER: Titelkarte konnte nicht erstellt werden")
                continue

            concat_parts.append(title_path)
            concat_parts.append(job.output_path)

        if cancel_flag and cancel_flag.is_set():
            break

        if len(concat_parts) < 2:
            log("  Merge abgebrochen: zu wenig Teile")
            group_idx += 1
            continue

        # Concat-Datei schreiben
        concat_file = tmpdir / "concat.txt"
        with open(concat_file, "w") as f:
            for part in concat_parts:
                escaped = str(part).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        # Zusammenführen via ffmpeg concat demuxer (stream copy)
        log(f"  Zusammenführen → {merge_name}")

        total_dur = 0.0
        for part in concat_parts:
            d = get_duration(part)
            if d:
                total_dur += d

        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(merge_path),
        ]

        rc = run_ffmpeg(cmd, duration=total_dur if total_dur > 0 else None,
                        cancel_flag=cancel_flag,
                        log_callback=log_callback,
                        progress_callback=progress_callback)

        # Temporäre Titelkarten aufräumen
        for tmp_file in tmpdir.iterdir():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        try:
            tmpdir.rmdir()
        except Exception:
            pass

        if rc == 0 and merge_path.exists():
            size_mb = merge_path.stat().st_size / (1024 * 1024)
            dur = get_duration(merge_path)
            dur_str = f", {dur:.0f}s" if dur else ""
            log(f"  ✓ Merge fertig: {merge_name} ({size_mb:.0f} MB{dur_str})")
            merged_files.append(merge_path)
        elif rc == -1:
            log("  Merge abgebrochen")
            if merge_path.exists():
                merge_path.unlink()
        else:
            log(f"  FEHLER beim Merge (exit {rc})")

        group_idx += 1
        if progress_callback:
            progress_callback(int(group_idx / total_groups * 100))

    return merged_files
