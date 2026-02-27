"""Hardware-Encoder-Auflösung und ffmpeg-Encoder-Argumente.

Unterstützte Encoder:
  - libx264    (CPU, Standard – immer verfügbar)
  - h264_nvenc (NVIDIA GPU – nur wenn NVENC-fähige GPU + passender Treiber)

Zuständigkeit dieses Moduls:
  - Welcher Encoder soll benutzt werden? (``resolve_encoder``)
  - Welche ffmpeg-Argumente braucht er? (``build_encoder_args``)
  - Welche Auswahl bietet die GUI an? (``available_encoder_choices``)

Die eigentliche Diagnose (GPU vorhanden? Treiber okay? Test-Encode?)
liegt in ``diagnostics.py``.
"""

import subprocess
from functools import lru_cache

from .diagnostics import encoder_test_encode, gpu_diagnostics

# Mapping libx264-Presets → NVENC-Presets (p1=schnellstes, p7=bestes)
_X264_TO_NVENC_PRESET: dict[str, str] = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p3",
    "faster": "p4",
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
    "slower": "p7",
    "veryslow": "p7",
}


# ═════════════════════════════════════════════════════════════════
#  Encoder-Erkennung
# ═════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def detect_hw_encoders() -> list[str]:
    """Erkennt verfügbare Hardware-Encoder über ``ffmpeg -encoders``
    und verifiziert jeden per Mini-Encode.

    Ergebnis wird gecacht, da sich die Hardware zur Laufzeit nicht ändert.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.splitlines()
    except Exception:
        return []

    hw_encoders: list[str] = []
    for line in lines:
        for name in ("h264_nvenc", "hevc_nvenc",
                     "h264_vaapi", "hevc_vaapi"):
            if name in line:
                works, _ = encoder_test_encode(name)
                if works:
                    hw_encoders.append(name)
    return hw_encoders


# ═════════════════════════════════════════════════════════════════
#  Encoder-Auflösung und Argumente
# ═════════════════════════════════════════════════════════════════

def resolve_encoder(encoder_setting: str,
                    log_callback=None) -> str:
    """Löst ``'auto'`` zum besten verfügbaren Encoder auf.

    Priorität: h264_nvenc > libx264.
    Bei direkter Angabe (z.B. ``'h264_nvenc'``) wird geprüft ob er
    verfügbar ist – falls nicht, Fallback auf ``'libx264'`` mit Warnung.
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    if encoder_setting == "auto":
        available = detect_hw_encoders()
        if "h264_nvenc" in available:
            return "h264_nvenc"
        return "libx264"

    # Explizit gewählter Encoder – prüfen ob er funktioniert
    if encoder_setting in ("h264_nvenc", "hevc_nvenc",
                           "h264_vaapi", "hevc_vaapi"):
        available = detect_hw_encoders()
        if encoder_setting not in available:
            diag = gpu_diagnostics()
            log(f"⚠ {encoder_setting} nicht verfügbar: {diag.summary}")
            log(f"  Fallback auf libx264 (CPU)")
            return "libx264"

    return encoder_setting


def build_encoder_args(encoder: str, preset: str, crf: int,
                       lossless: bool, fps: float) -> list[str]:
    """Erstellt die ffmpeg-Encoder-Argumente für den gewählten Encoder.

    Args:
        encoder: Aufgelöster Encoder-Name.
        preset: libx264-Style Preset (wird für NVENC automatisch gemappt).
        crf: Qualitätswert (CRF für x264, CQ für NVENC).
        lossless: Verlustfreie Kodierung.
        fps: Ausgabe-Framerate (kann gebrochene Werte enthalten bei Sync).

    Returns:
        Liste der ffmpeg-Argumente.
    """
    fps_str = f"{fps:.6f}" if isinstance(fps, float) else str(fps)
    if encoder == "h264_nvenc":
        nvenc_preset = _X264_TO_NVENC_PRESET.get(preset, "p5")
        args = ["-c:v", "h264_nvenc"]
        if lossless:
            args += ["-preset", nvenc_preset, "-tune", "lossless"]
        else:
            args += ["-preset", nvenc_preset, "-tune", "hq",
                     "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
        args += ["-pix_fmt", "yuv420p", "-r", fps_str]
        return args

    # Fallback: libx264
    p = "slow" if lossless else preset
    c = 0 if lossless else crf
    return ["-c:v", "libx264", "-preset", p, "-crf", str(c),
            "-pix_fmt", "yuv420p", "-r", fps_str]


# ═════════════════════════════════════════════════════════════════
#  GUI-Helfer
# ═════════════════════════════════════════════════════════════════

def encoder_display_name(encoder_id: str) -> str:
    """Menschenlesbarer Name für einen Encoder."""
    return {
        "auto": "Automatisch (NVENC falls verfügbar)",
        "h264_nvenc": "NVIDIA NVENC (GPU)",
        "libx264": "libx264 (CPU)",
    }.get(encoder_id, encoder_id)


def available_encoder_choices() -> list[tuple[str, str]]:
    """Gibt ``[(id, display_name), ...]`` für die Encoder-Auswahl zurück.

    Enthält immer ``'auto'`` und ``'libx264'``.
    ``'h264_nvenc'`` nur wenn tatsächlich verfügbar.
    """
    choices = [
        ("auto", encoder_display_name("auto")),
        ("libx264", encoder_display_name("libx264")),
    ]
    if "h264_nvenc" in detect_hw_encoders():
        choices.insert(1, ("h264_nvenc", encoder_display_name("h264_nvenc")))
    return choices
