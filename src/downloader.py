"""Download-Logik: Videos von Raspberry Pi Kameras via SFTP herunterladen."""

import os
import threading
from pathlib import Path
from typing import Callable, Optional

import paramiko

from .settings import DeviceSettings, CameraSettings

# Abwärtskompatibilität
DeviceConfig = DeviceSettings
DownloadConfig = CameraSettings


# ═════════════════════════════════════════════════════════════════
#  YAML-Import (Migration von cameras.yaml)
# ═════════════════════════════════════════════════════════════════

def import_from_yaml(path: str) -> CameraSettings:
    """
    Importiert Kamera-Konfiguration aus einer legacy cameras.yaml und gibt
    ein CameraSettings-Objekt zurück, das in AppSettings gespeichert werden kann.
    """
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    cfg = CameraSettings(
        destination=data.get("destination", ""),
        source=data.get("source", "/home/kaderblick/camera_api/recordings"),
    )
    for d in data.get("devices", []):
        cfg.devices.append(DeviceSettings(
            name=d.get("name", d.get("ip", "unknown")),
            ip=d["ip"],
            username=d.get("username", d.get("user", "")),
            password=d.get("password", ""),
            ssh_key=d.get("ssh_key", ""),
            port=int(d.get("port", 22)),
        ))
    return cfg


# ═════════════════════════════════════════════════════════════════
#  SSH / SFTP Verbindung
# ═════════════════════════════════════════════════════════════════

def _connect(device: DeviceSettings):
    """Baut SSH-Verbindung auf und gibt (SSHClient, SFTPClient) zurück."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    kwargs: dict = {
        "hostname": device.ip,
        "port": device.port,
        "username": device.username,
        "timeout": 30,
    }

    key_path = os.path.expanduser(device.ssh_key) if device.ssh_key else ""
    if key_path and os.path.exists(key_path):
        kwargs["key_filename"] = key_path
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    else:
        kwargs["look_for_keys"] = True
        kwargs["allow_agent"] = True

    if device.password:
        kwargs["password"] = device.password

    client.connect(**kwargs)
    sftp = client.open_sftp()
    return client, sftp


# ═════════════════════════════════════════════════════════════════
#  Hilfsfunktionen
# ═════════════════════════════════════════════════════════════════

def _remote_size(sftp, remote_path: str) -> Optional[int]:
    try:
        return sftp.stat(remote_path).st_size
    except Exception:
        return None


def _ssh_exec(device: DeviceSettings, cmd: str, timeout: int = 20) -> bool:
    """Führt einen SSH-Befehl aus; gibt True bei rc=0 zurück."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        key_path = os.path.expanduser(device.ssh_key) if device.ssh_key else ""
        kwargs: dict = {
            "hostname": device.ip,
            "port": device.port,
            "username": device.username,
            "timeout": timeout,
        }
        if key_path and os.path.exists(key_path):
            kwargs["key_filename"] = key_path
            kwargs["look_for_keys"] = False
            kwargs["allow_agent"] = False
        else:
            kwargs["look_for_keys"] = True
            kwargs["allow_agent"] = True
        if device.password:
            kwargs["password"] = device.password
        client.connect(**kwargs)
        _, _, stderr = client.exec_command(cmd, timeout=timeout)
        rc = stderr.channel.recv_exit_status()
        return rc == 0
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def delete_remote_recording(
    device: DeviceSettings,
    source_dir: str,
    base: str,
    log_cb: Callable[[str], None] = print,
) -> bool:
    """
    Löscht .mjpg und .wav einer Aufnahme auf dem Gerät.
    Gibt True zurück, wenn beide Dateien erfolgreich gelöscht wurden.
    """
    src = source_dir.rstrip("/")
    ok_mjpg = _ssh_exec(device, f"rm -f {src}/{base}.mjpg")
    ok_wav  = _ssh_exec(device, f"rm -f {src}/{base}.wav")
    if ok_mjpg and ok_wav:
        log_cb(f"  Quelldateien gelöscht: {base}")
        return True
    else:
        log_cb(f"  [Warnung] Konnte Quelldateien nicht löschen: {base}")
        return False


# ═════════════════════════════════════════════════════════════════
#  Download-Logik
# ═════════════════════════════════════════════════════════════════

def download_device(
    device: DeviceSettings,
    config: CameraSettings,
    log_cb: Callable[[str], None] = print,
    progress_cb: Optional[Callable[[str, str, int, int], None]] = None,
    cancel_flag: Optional[threading.Event] = None,
    destination_override: str = "",
    delete_after_download: bool = False,
) -> list:
    """
    Lädt alle vollständigen Aufnahmen (.mjpg + .wav) von einem Gerät herunter.

    Rückgabe: Liste von (local_dir: str, base: str, mjpg_path: str)
    für jede erfolgreich vorhandene/heruntergeladene Aufnahme.

    progress_cb(device_name, filename, transferred_bytes, total_bytes)
    """
    dest_root = Path(destination_override or config.destination)
    dest_root.mkdir(parents=True, exist_ok=True)

    local_dir = dest_root / device.name
    local_dir.mkdir(parents=True, exist_ok=True)

    log_cb(f"Verbinde mit {device.name} ({device.ip}:{device.port}) ...")
    try:
        client, sftp = _connect(device)
    except Exception as exc:
        log_cb(f"[Fehler] Verbindung zu {device.name} fehlgeschlagen: {exc}")
        return []

    results: list = []
    try:
        try:
            remote_files = sftp.listdir(config.source)
        except Exception as exc:
            log_cb(f"[Fehler] Kann {config.source} auf {device.name} nicht auflisten: {exc}")
            return []

        mjpgs = {os.path.splitext(f)[0] for f in remote_files if f.lower().endswith(".mjpg")}
        wavs  = {os.path.splitext(f)[0] for f in remote_files if f.lower().endswith(".wav")}
        bases = sorted(mjpgs & wavs)
        log_cb(f"{device.name}: {len(bases)} vollstaendige Aufnahme(n) gefunden")

        for base in bases:
            if cancel_flag and cancel_flag.is_set():
                log_cb("Abgebrochen.")
                break

            src_dir = config.source.rstrip("/")
            remote_mjpg = f"{src_dir}/{base}.mjpg"
            remote_wav  = f"{src_dir}/{base}.wav"
            local_mjpg  = local_dir / (base + ".mjpg")
            local_wav   = local_dir / (base + ".wav")

            # Bereits vorhanden? -> Groessen-Vergleich
            r_mjpg_size = _remote_size(sftp, remote_mjpg)
            r_wav_size  = _remote_size(sftp, remote_wav)
            already_ok = (
                local_mjpg.exists() and local_wav.exists()
                and r_mjpg_size is not None and r_wav_size is not None
                and local_mjpg.stat().st_size == r_mjpg_size
                and local_wav.stat().st_size  == r_wav_size
            )
            if already_ok:
                log_cb(f"Ueberspringe {base} (bereits vorhanden, gleiche Groesse)")
                results.append((str(local_dir), base, str(local_mjpg)))
                if delete_after_download:
                    delete_remote_recording(device, config.source, base, log_cb)
                continue

            log_cb(f"Lade herunter: {base} ...")

            def _progress(transferred: int, total: int, _dev=device.name, _fn=""):
                if progress_cb and total > 0:
                    progress_cb(_dev, _fn, transferred, total)

            try:
                sftp.get(
                    remote_mjpg, str(local_mjpg),
                    callback=lambda t, tot, _fn=base + ".mjpg": _progress(t, tot, _fn=_fn),
                )
                if cancel_flag and cancel_flag.is_set():
                    local_mjpg.unlink(missing_ok=True)
                    log_cb("Abgebrochen.")
                    break

                sftp.get(
                    remote_wav, str(local_wav),
                    callback=lambda t, tot, _fn=base + ".wav": _progress(t, tot, _fn=_fn),
                )

                results.append((str(local_dir), base, str(local_mjpg)))
                log_cb(f"  -> {base} fertig")

                if delete_after_download:
                    delete_remote_recording(device, config.source, base, log_cb)

            except Exception as exc:
                log_cb(f"[Fehler] Download von {base}: {exc}")
                for p in (local_mjpg, local_wav):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass

    finally:
        try:
            sftp.close()
            client.close()
        except Exception:
            pass

    log_cb(f"{device.name}: {len(results)} Aufnahme(n) heruntergeladen/vorhanden")
    return results
