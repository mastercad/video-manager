"""Kaderblick-Integration: Videos zu einem Spiel eintragen.

Voraussetzung: Das Video muss bereits auf YouTube hochgeladen sein,
da der YouTube-Link im Payload hinterlegt wird.

API-Endpunkte (Base-URL aus KaderblickSettings)
──────────────────────────────────────────────
GET  /api/video-types           → verfügbare Video-Typen
GET  /api/cameras               → verfügbare Kameras
GET  /videos/{game_id}          → vorhandene Videos zu einem Spiel
POST /videos/save/{game_id}     → Video anlegen

Duplikat-Erkennung
──────────────────
Vor jedem POST wird GET /videos/{game_id} abgerufen.
Ein Video gilt als Duplikat wenn seine youtubeId mit der des
gerade hochgeladenen Videos übereinstimmt.

Ergänzende Persistenz: data/integration_state.json speichert
alle erfolgreich angelegten Einträge (keyed by YouTube-Video-ID).
"""

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..settings import AppSettings
from ..media.ffmpeg_runner import get_duration
from .state_store import load_section, save_section

# ─────────────────────────────────────────────────────────────────
#  Upload-Registry
# ─────────────────────────────────────────────────────────────────

class KaderblickRegistry:
    """Speichert erfolgreich angelegte Kaderblick-Video-Einträge.

    Keyed by YouTube-Video-ID.
    Eintrag: { "kaderblick_id": int, "game_id": str,
               "name": str, "posted_at": "ISO-8601" }
    """

    def __init__(self, path: Path | None = None):
        self._path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None:
            self._data = load_section("kaderblick_uploads")
            return
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self) -> None:
        if self._path is None:
            save_section("kaderblick_uploads", self._data)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8")

    def already_posted(self, youtube_video_id: str) -> Optional[int]:
        """Gibt die Kaderblick-Video-ID zurück wenn bereits gepostet, sonst None."""
        entry = self._data.get(youtube_video_id)
        if entry:
            return entry.get("kaderblick_id")
        return None

    def record(self, youtube_video_id: str, kaderblick_id: int,
               game_id: str, name: str) -> None:
        self._data[youtube_video_id] = {
            "kaderblick_id": kaderblick_id,
            "game_id": game_id,
            "name": name,
            "posted_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def clear(self, youtube_video_id: str) -> None:
        self._data.pop(youtube_video_id, None)
        self._save()


# Modul-globale Registry-Instanz (wird beim ersten Aufruf angelegt)
_registry: Optional[KaderblickRegistry] = None


def _get_registry() -> KaderblickRegistry:
    global _registry
    if _registry is None:
        _registry = KaderblickRegistry()
    return _registry


def get_recorded_kaderblick_id(youtube_video_id: str) -> Optional[int]:
    if not youtube_video_id:
        return None
    return _get_registry().already_posted(youtube_video_id)


def clear_recorded_kaderblick_id(youtube_video_id: str) -> None:
    if not youtube_video_id:
        return
    _get_registry().clear(youtube_video_id)


# ─────────────────────────────────────────────────────────────────
#  HTTP-Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────

def _ssl_ctx(url: str):
    """SSL-Kontext nur für https://-URLs – http:// läuft ohne SSL."""
    import ssl
    if url.lower().startswith("https://"):
        return ssl.create_default_context()
    return None

def _headers(kb) -> dict:
    """Baut die HTTP-Header je nach auth_mode."""
    base = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://kaderblick.de",
        "Referer": "https://kaderblick.de/",
    }
    if kb.auth_mode == "bearer":
        base["Authorization"] = f"Bearer {kb.bearer_token}"
    else:  # jwt
        base["Cookie"] = f"jwt_token={kb.jwt_token}"
    return base


def _refresh_jwt(kb) -> bool:
    """Versucht den jwt_token via jwt_refresh_token zu erneuern.

    Gibt True zurück wenn erfolgreich und aktualisiert kb.jwt_token.
    """
    if not kb.jwt_refresh_token:
        return False
    try:
        url = kb.base_url.rstrip("/") + "/api/token/refresh"
        payload = json.dumps({"refresh_token": kb.jwt_refresh_token}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "Accept": "*/*",
                     "Origin": "https://kaderblick.de",
                     "Referer": "https://kaderblick.de/"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx(url)) as resp:
            data = json.loads(resp.read().decode())
        new_token = data.get("token") or data.get("jwt_token") or data.get("access_token")
        if new_token:
            kb.jwt_token = new_token
            return True
    except Exception:
        pass
    return False


def _http_error_details(exc: urllib.error.HTTPError) -> str:
    """Liest den HTTP-Fehlertext, damit API-Meldungen im Log sichtbar bleiben."""
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    if body:
        return f"HTTP {exc.code}: {exc.reason} | Body: {body[:400]}"
    return f"HTTP {exc.code}: {exc.reason}"


def _get(url: str, kb, timeout: int = 15) -> dict:
    """GET-Request, bei abgelaufenem JWT einmalig Token-Refresh versuchen."""
    try:
        req = urllib.request.Request(url, headers=_headers(kb), method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(url)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and kb.auth_mode == "jwt" and _refresh_jwt(kb):
            req = urllib.request.Request(url, headers=_headers(kb), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(url)) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc2:
                raise RuntimeError(f"GET {url} fehlgeschlagen (nach Token-Refresh): {exc2}") from exc2
        raise RuntimeError(f"GET {url} fehlgeschlagen: {_http_error_details(exc)}") from exc
    except Exception as exc:
        raise RuntimeError(f"GET {url} fehlgeschlagen: {exc}") from exc


class _PostPreservingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Folgt Redirects (301/302/307/308) ohne POST → GET zu ändern."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = urllib.request.Request(
            newurl,
            data=req.data,
            headers=dict(req.headers),
            method=req.get_method(),
        )
        return new_req


def _post(url: str, kb, payload: dict, timeout: int = 15) -> dict:
    """POST-Request, bei abgelaufenem JWT einmalig Token-Refresh versuchen.

    Verwendet einen eigenen Redirect-Handler damit POST bei HTTP→HTTPS-
    Weiterleitungen nicht zu GET wird (urllib-Standardverhalten würde
    sonst einen 405 Method Not Allowed auslösen).
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _do_post(u: str) -> dict:
        opener = urllib.request.build_opener(_PostPreservingRedirectHandler)
        req = urllib.request.Request(u, data=data, headers=_headers(kb), method="POST")
        ctx = _ssl_ctx(u)
        if ctx:
            import ssl
            opener.add_handler(urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8").strip()
            return json.loads(body) if body else {}

    try:
        return _do_post(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and kb.auth_mode == "jwt" and _refresh_jwt(kb):
            try:
                return _do_post(url)
            except Exception as exc2:
                raise RuntimeError(f"POST {url} fehlgeschlagen (nach Token-Refresh): {exc2}") from exc2
        raise RuntimeError(f"POST {url} fehlgeschlagen: {_http_error_details(exc)}") from exc
    except Exception as exc:
        raise RuntimeError(f"POST {url} fehlgeschlagen: {exc}") from exc


# ─────────────────────────────────────────────────────────────────
#  API-Aufrufe
# ─────────────────────────────────────────────────────────────────

def fetch_video_types(kb) -> list[dict]:
    """Ruft /api/video-types ab. Gibt Liste von {id, name} zurück."""
    data = _get(f"{kb.base_url.rstrip('/')}/api/video-types", kb)
    if isinstance(data, list):
        result = data
    else:
        result = None
        for key in ("videoTypes", "data", "items", "types", "content"):
            if key in data and isinstance(data[key], list):
                result = data[key]
                break
        if result is None:
            raise RuntimeError(
                f"Unbekanntes Antwortformat von /api/video-types.\n"
                f"Keys: {list(data.keys())}\n"
                f"Antwort: {str(data)[:400]}")
    if not result:
        raise RuntimeError(
            f"API /api/video-types lieferte eine leere Liste.\n"
            f"Rohantwort: {str(data)[:400]}")
    return result


def fetch_cameras(kb) -> list[dict]:
    """Ruft /api/cameras ab. Gibt Liste von {id, name} zurück."""
    data = _get(f"{kb.base_url.rstrip('/')}/api/cameras", kb)
    if isinstance(data, list):
        result = data
    else:
        result = None
        for key in ("cameras", "data", "items", "content"):
            if key in data and isinstance(data[key], list):
                result = data[key]
                break
        if result is None:
            raise RuntimeError(
                f"Unbekanntes Antwortformat von /api/cameras.\n"
                f"Keys: {list(data.keys())}\n"
                f"Antwort: {str(data)[:400]}")
    if not result:
        raise RuntimeError(
            f"API /api/cameras lieferte eine leere Liste.\n"
            f"Rohantwort: {str(data)[:400]}")
    return result


def fetch_game_videos(kb, game_id: str) -> list[dict]:
    """Ruft /videos/{game_id} ab. Gibt die videos-Liste zurück."""
    data = _get(f"{kb.base_url.rstrip('/')}/videos/{game_id}", kb)
    return data.get("videos", [])


def post_video(kb, game_id: str, payload: dict) -> dict:
    """Postet ein neues Video zu einem Spiel. Gibt die API-Antwort zurück."""
    return _post(f"{kb.base_url.rstrip('/')}/videos/save/{game_id}", kb, payload)


def publish_game_videos(kb, game_id: str) -> dict:
    """Meldet Kaderblick, dass alle gestarteten Videos des Spiels vorliegen."""
    return _post(f"{kb.base_url.rstrip('/')}/games/{game_id}/videos/publish", kb, {})


# ─────────────────────────────────────────────────────────────────
#  Videolänge ermitteln
# ─────────────────────────────────────────────────────────────────

def get_video_duration_seconds(file_path: Path) -> int:
    """Ermittelt die Videolänge via ffprobe. Gibt 0 zurück wenn nicht möglich."""
    try:
        duration = get_duration(file_path)
        if duration and duration > 0:
            return int(duration)
    except Exception:
        pass
    return 0


# ─────────────────────────────────────────────────────────────────
#  Haupt-Upload-Funktion
# ─────────────────────────────────────────────────────────────────

def post_to_kaderblick(
    *,
    settings: AppSettings,
    game_id: str,
    video_name: str,
    youtube_video_id: str,
    youtube_url: str,
    file_path: Path,
    output_file_path: Optional[Path],
    game_start_seconds: int,
    video_type_id: int,
    camera_id: int,
    sort_index: int,
    log_callback,
) -> bool:
    """Trägt ein Video auf Kaderblick ein.

    Wird nur ausgeführt wenn:
    - settings.kaderblick.bearer_token gesetzt ist
    - game_id nicht leer ist
    - youtube_video_id nicht leer ist (Upload muss erfolgt sein)

    Duplikate werden verhindert durch:
    1. Lokale Registry (integration_state.json / kaderblick_uploads)
    2. Serverabfrage GET /videos/{game_id} → Abgleich via youtubeId

    Gibt True zurück wenn erfolgreich (oder bereits vorhanden), False bei Fehler.
    """
    kb = settings.kaderblick
    active_token = kb.jwt_token if kb.auth_mode == "jwt" else kb.bearer_token
    if not active_token:
        mode_label = "JWT-Token" if kb.auth_mode == "jwt" else "Bearer-Token"
        log_callback(f"⚠ Kaderblick: Kein {mode_label} konfiguriert – übersprungen")
        return False
    if not game_id:
        log_callback("⚠ Kaderblick: Keine Spiel-ID angegeben – übersprungen")
        return False
    if not youtube_video_id:
        log_callback("⚠ Kaderblick: Kein YouTube-Video-ID – übersprungen")
        return False
    if not video_type_id:
        log_callback(
            "⚠ Kaderblick: Kein Video-Typ gesetzt – der API-POST würde mit "
            "'VideoType nicht gefunden' scheitern")
        return False

    registry = _get_registry()

    # 1. Lokale Registry prüfen
    existing_kb_id = registry.already_posted(youtube_video_id)
    if existing_kb_id is not None:
        log_callback(
            f"  ℹ Kaderblick: Video bereits eingetragen "
            f"(ID {existing_kb_id}) – übersprungen")
        return True

    base = kb.base_url.rstrip("/")

    # 2. Serverabfrage auf Duplikat + aktuellen Sort-Index ermitteln
    server_sort_max = 0
    try:
        game_videos = fetch_game_videos(kb, game_id)
        for v in game_videos:
            if v.get("youtubeId") == youtube_video_id:
                log_callback(
                    f"  ℹ Kaderblick: Video '{video_name}' bereits auf Server "
                    f"vorhanden (ID {v.get('id')}) – Registry aktualisiert")
                registry.record(youtube_video_id, v["id"], game_id, video_name)
                return True
            try:
                server_sort_max = max(server_sort_max, int(v.get("sort", 0)))
            except (TypeError, ValueError):
                pass
    except RuntimeError as exc:
        log_callback(f"  ⚠ Kaderblick: Duplikatprüfung fehlgeschlagen: {exc}")

    # Sort-Index: immer hinter alle bereits vorhandenen Server-Einträge hängen
    effective_sort = server_sort_max + 1

    # 3. Videolänge ermitteln (bevorzugt aus konvertierter Datei)
    probe_path = output_file_path if (output_file_path and output_file_path.exists()) else file_path
    duration = get_video_duration_seconds(probe_path)

    # 4. Payload zusammenstellen
    payload: dict = {
        "video_id": "",
        "name": video_name,
        "url": youtube_url,
        "filePath": file_path.name,
        "gameStart": str(game_start_seconds),
        "length": str(duration),
        "sort": str(effective_sort),
        "videoType": str(video_type_id) if video_type_id else "",
        "camera": str(camera_id) if camera_id else "",
    }

    # 5. POST
    log_callback(
        f"  📤 Kaderblick: Trage '{video_name}' für Spiel {game_id} "
        f"(Sort-Index {effective_sort}) ein …")
    try:
        response = post_video(kb, game_id, payload)
    except RuntimeError as exc:
        log_callback(f"  ❌ Kaderblick: {exc}")
        return False

    if not response.get("success"):
        log_callback(f"  ❌ Kaderblick: API Fehler: {response}")
        return False

    video_data = response.get("video", {})
    new_id = video_data.get("id")
    registry.record(youtube_video_id, new_id, game_id, video_name)
    log_callback(f"  ✅ Kaderblick: Video eingetragen (ID {new_id})")
    return True
