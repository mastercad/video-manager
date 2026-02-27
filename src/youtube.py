"""YouTube-Upload: Authentifizierung, Upload und Playlist-Management."""

from pathlib import Path
from typing import Optional

from .settings import CLIENT_SECRET_FILE, TOKEN_FILE, AppSettings

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# YouTube-API-Abhängigkeiten (optional)
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    YOUTUBE_AVAILABLE = True
except ImportError:
    YOUTUBE_AVAILABLE = False


# ═════════════════════════════════════════════════════════════════
#  Authentifizierung
# ═════════════════════════════════════════════════════════════════

def get_youtube_service(log_callback=None):
    """Erstellt einen authentifizierten YouTube-API-Service."""
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    if not YOUTUBE_AVAILABLE:
        log("FEHLER: google-api-python-client / google-auth-oauthlib "
            "nicht installiert. Bitte: pip install -r requirements.txt")
        return None

    if not CLIENT_SECRET_FILE.exists():
        log(f"FEHLER: {CLIENT_SECRET_FILE.name} nicht gefunden!")
        log("Siehe docs/youtube_credentials.md für die Einrichtung.")
        return None

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE), YOUTUBE_SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log(f"Token-Refresh fehlgeschlagen: {e}")
                creds = None

        if not creds:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CLIENT_SECRET_FILE), YOUTUBE_SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                log(f"OAuth-Anmeldung fehlgeschlagen: {e}")
                return None

        TOKEN_FILE.write_text(creds.to_json())
        log("YouTube-Token gespeichert.")

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"YouTube-Service konnte nicht erstellt werden: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
#  Playlist-Management
# ═════════════════════════════════════════════════════════════════

def find_or_create_playlist(service, title: str,
                            log_callback=None) -> Optional[str]:
    """Sucht eine existierende Playlist oder erstellt eine neue."""
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    if not title:
        return None

    try:
        request = service.playlists().list(
            part="snippet", mine=True, maxResults=50)
        while request:
            response = request.execute()
            for item in response.get("items", []):
                if item["snippet"]["title"] == title:
                    log(f"Playlist gefunden: {title}")
                    return item["id"]
            request = service.playlists().list_next(request, response)

        body = {
            "snippet": {"title": title, "description": ""},
            "status": {"privacyStatus": "unlisted"},
        }
        resp = service.playlists().insert(
            part="snippet,status", body=body).execute()
        playlist_id = resp["id"]
        log(f"Playlist erstellt: {title} ({playlist_id})")
        return playlist_id
    except Exception as e:
        log(f"Playlist-Fehler: {e}")
        return None


# ═════════════════════════════════════════════════════════════════
#  Upload
# ═════════════════════════════════════════════════════════════════

def upload_to_youtube(job, settings: AppSettings,
                      yt_service=None, log_callback=None) -> bool:
    """Lädt die YouTube-Version (oder das Hauptvideo) auf YouTube hoch."""
    yt = settings.youtube

    def log(msg: str):
        if log_callback:
            log_callback(msg)

    if not yt.upload_to_youtube:
        return False

    if not yt_service:
        log("Kein YouTube-Service verfügbar – Upload übersprungen.")
        return False

    mp4 = job.output_path
    if not mp4 or not mp4.exists():
        log("Keine Ausgabedatei zum Hochladen vorhanden.")
        return False

    yt_version = mp4.with_stem(mp4.stem + "_youtube")
    upload_file = yt_version if yt_version.exists() else mp4

    title = job.youtube_title or upload_file.stem
    log(f"YouTube-Upload: {upload_file.name} → \"{title}\"")

    body = {
        "snippet": {
            "title": title,
            "description": "Automatisch hochgeladen von MJPEG Converter",
            "categoryId": "17",  # Sport
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(upload_file),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10 MB Chunks
    )

    try:
        request = yt_service.videos().insert(
            part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                log(f"  Upload: {pct}%")

        video_id = response["id"]
        log(f"✓ Hochgeladen: https://youtu.be/{video_id}")

        # In Playlist einordnen
        if job.youtube_playlist:
            playlist_id = find_or_create_playlist(
                yt_service, job.youtube_playlist, log_callback)
            if playlist_id:
                try:
                    yt_service.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": playlist_id,
                                "resourceId": {
                                    "kind": "youtube#video",
                                    "videoId": video_id,
                                },
                            }
                        },
                    ).execute()
                    log(f"✓ Zur Playlist hinzugefügt: {job.youtube_playlist}")
                except Exception as e:
                    log(f"Playlist-Zuordnung fehlgeschlagen: {e}")

        return True
    except Exception as e:
        log(f"Upload-Fehler: {e}")
        return False
