"""GPU- und System-Diagnose für Hardware-Encoding.

Prüft ob alle Voraussetzungen für NVIDIA NVENC erfüllt sind:
  1. NVIDIA-GPU vorhanden (nvidia-smi)
  2. Treiber aktuell genug (≥ 550.54 für ffmpeg ≥ 7.x)
  3. ffmpeg mit h264_nvenc kompiliert
  4. Mini-Test-Encode funktioniert tatsächlich

Stellt verständliche Fehlermeldungen und Lösungsvorschläge bereit,
damit auch Endnutzer ohne Fachwissen das Problem nachvollziehen können.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


# Mindest-Treiberversion für ffmpeg ≥ 7.x NVENC
_MIN_DRIVER_MAJOR = 550
_MIN_DRIVER_MINOR = 54


# ═════════════════════════════════════════════════════════════════
#  Datenklasse
# ═════════════════════════════════════════════════════════════════

@dataclass
class GpuDiagnostics:
    """Ergebnis der GPU-Prüfung – für Anzeige in der GUI."""
    nvenc_available: bool         # Kann NVENC tatsächlich benutzt werden?
    gpu_name: str                 # z.B. "NVIDIA GeForce RTX 3060" oder ""
    driver_version: str           # z.B. "535.288.01" oder ""
    driver_new_enough: bool       # True wenn Treiber ≥ 550.54
    ffmpeg_has_nvenc: bool        # ffmpeg mit h264_nvenc kompiliert?
    nvenc_encode_works: bool      # Mini-Encode erfolgreich?
    summary: str                  # Einzeilige Zusammenfassung für den Nutzer
    details: list[str]            # Detaillierte Diagnose-Zeilen

    @property
    def status_icon(self) -> str:
        return "✓" if self.nvenc_available else "✗"


# ═════════════════════════════════════════════════════════════════
#  Interne Helfer
# ═════════════════════════════════════════════════════════════════

def _parse_driver_version(version_str: str) -> tuple[int, int]:
    """Parst '535.288.01' → (535, 288)."""
    parts = version_str.strip().split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 0, 0


def _get_nvidia_info() -> tuple[str, str]:
    """Gibt (GPU-Name, Treiberversion) zurück, oder ('', '') wenn kein NVIDIA."""
    if not shutil.which("nvidia-smi"):
        return "", ""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:
        pass
    return "", ""


def _ffmpeg_lists_encoder(encoder_name: str) -> bool:
    """Prüft ob ffmpeg den Encoder in der Liste hat (kompiliert)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        return encoder_name in result.stdout
    except Exception:
        return False


def encoder_test_encode(encoder_name: str) -> tuple[bool, str]:
    """Testet ob ein Encoder tatsächlich funktioniert (nicht nur gelistet ist).

    Erzeugt einen 1-Frame Mini-Encode mit ``-f lavfi`` als Eingang.
    Erkennt z.B. veraltete NVIDIA-Treiber die NVENC nicht unterstützen.

    Returns:
        (funktioniert, fehlermeldung)
    """
    try:
        # 256x256: NVENC hat eine Mindestauflösung (~145x49), 64x64 reicht nicht.
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-y",
             "-f", "lavfi", "-i", "color=black:s=256x256:d=0.04:r=25",
             "-c:v", encoder_name, "-f", "null", "-"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        # Fehlermeldung extrahieren
        stderr = result.stderr or ""
        # Typische NVENC-Fehlermeldungen
        if "nvenc API version" in stderr:
            m = re.search(r"Required: ([\d.]+)\s+Found: ([\d.]+)", stderr)
            if m:
                return False, (f"NVENC API zu alt (installiert: {m.group(2)},"
                               f" benötigt: {m.group(1)})")
            return False, "NVENC API-Version zu alt"
        if "minimum required Nvidia driver" in stderr:
            m = re.search(r"minimum required.*?(\d+\.\d+)", stderr)
            if m:
                return False, f"NVIDIA-Treiber mindestens {m.group(1)} nötig"
            return False, "NVIDIA-Treiber zu alt"
        if "Cannot load" in stderr or "not found" in stderr.lower():
            return False, "CUDA/NVENC-Bibliotheken nicht gefunden"
        # Allgemeiner Fehler
        last_lines = [l.strip() for l in stderr.strip().splitlines()
                      if l.strip()][-3:]
        return False, ("; ".join(last_lines)
                       if last_lines else "Unbekannter Fehler")
    except Exception as e:
        return False, str(e)


# ═════════════════════════════════════════════════════════════════
#  Hauptfunktion
# ═════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def gpu_diagnostics() -> GpuDiagnostics:
    """Führt eine vollständige GPU-Diagnose durch.

    Prüft schrittweise:
      1. Ist eine NVIDIA-GPU vorhanden? (nvidia-smi)
      2. Ist der Treiber aktuell genug?
      3. Ist ffmpeg mit h264_nvenc kompiliert?
      4. Funktioniert ein Test-Encode tatsächlich?

    Returns:
        ``GpuDiagnostics`` mit allen Ergebnissen und lesbarer Zusammenfassung.
    """
    details: list[str] = []

    # 1. NVIDIA-GPU und Treiber prüfen
    gpu_name, driver_version = _get_nvidia_info()
    if gpu_name:
        details.append(f"✓ GPU erkannt: {gpu_name}")
        details.append(f"  Treiber: {driver_version}")
    else:
        details.append(
            "✗ Keine NVIDIA-GPU erkannt (nvidia-smi nicht verfügbar)")
        return GpuDiagnostics(
            nvenc_available=False, gpu_name="", driver_version="",
            driver_new_enough=False, ffmpeg_has_nvenc=False,
            nvenc_encode_works=False,
            summary="Keine NVIDIA-GPU erkannt – CPU-Encoding wird verwendet",
            details=details,
        )

    # 2. Treiberversion prüfen
    major, minor = _parse_driver_version(driver_version)
    driver_ok = (major > _MIN_DRIVER_MAJOR
                 or (major == _MIN_DRIVER_MAJOR
                     and minor >= _MIN_DRIVER_MINOR))
    if driver_ok:
        details.append(
            f"✓ Treiber ist aktuell genug "
            f"(≥ {_MIN_DRIVER_MAJOR}.{_MIN_DRIVER_MINOR})")
    else:
        details.append(
            f"✗ Treiber zu alt: {driver_version} "
            f"(mindestens {_MIN_DRIVER_MAJOR}.{_MIN_DRIVER_MINOR} nötig)")
        details.append(
            "  → sudo apt update && sudo apt install nvidia-driver-560")

    # 3. ffmpeg mit NVENC kompiliert?
    ffmpeg_has_nvenc = _ffmpeg_lists_encoder("h264_nvenc")
    if ffmpeg_has_nvenc:
        details.append("✓ ffmpeg kennt h264_nvenc")
    else:
        details.append("✗ ffmpeg ohne NVENC-Unterstützung kompiliert")
        details.append(
            "  → sudo apt install ffmpeg  (oder Snap/Flatpak-Version)")

    # 4. Tatsächlicher Test-Encode
    if ffmpeg_has_nvenc:
        works, error_msg = encoder_test_encode("h264_nvenc")
        if works:
            details.append("✓ Test-Encode mit h264_nvenc erfolgreich")
        else:
            details.append(f"✗ Test-Encode fehlgeschlagen: {error_msg}")
    else:
        works = False
        error_msg = "ffmpeg ohne NVENC"

    nvenc_available = bool(gpu_name and ffmpeg_has_nvenc and works)

    if nvenc_available:
        summary = f"GPU-Encoding verfügbar ({gpu_name})"
    elif gpu_name and not driver_ok:
        summary = (
            f"GPU vorhanden ({gpu_name}), aber Treiber zu alt "
            f"({driver_version} → mindestens "
            f"{_MIN_DRIVER_MAJOR}.{_MIN_DRIVER_MINOR} nötig)")
    elif gpu_name and not ffmpeg_has_nvenc:
        summary = (
            f"GPU vorhanden ({gpu_name}), "
            f"aber ffmpeg ohne NVENC kompiliert")
    elif gpu_name and not works:
        summary = (
            f"GPU vorhanden ({gpu_name}), "
            f"NVENC nicht nutzbar: {error_msg}")
    else:
        summary = (
            "Keine NVIDIA-GPU erkannt – CPU-Encoding wird verwendet")

    return GpuDiagnostics(
        nvenc_available=nvenc_available,
        gpu_name=gpu_name,
        driver_version=driver_version,
        driver_new_enough=driver_ok,
        ffmpeg_has_nvenc=ffmpeg_has_nvenc,
        nvenc_encode_works=works,
        summary=summary,
        details=details,
    )
