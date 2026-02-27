# YouTube API – Credentials einrichten

Anleitung zur Einrichtung der Google/YouTube API-Credentials für den automatischen Upload aus der GUI (`main.py`).

[← Zurück zur Übersicht](../README.md) · Siehe auch: [GUI-Konverter](convert_mjpeg_gui.md)

---

## Übersicht

Der Upload verwendet die **YouTube Data API v3** mit **OAuth 2.0 (Desktop-App)**. Dafür werden zwei Dateien benötigt:

| Datei | Beschreibung | Erstellt von |
|-------|-------------|--------------|
| `client_secret.json` | API-Zugangsdaten (einmalig aus Google Cloud) | Du (manuell) |
| `youtube_token.json` | OAuth-Token (wird automatisch erzeugt) | Das Programm |

Beide Dateien liegen im Projektverzeichnis (neben `main.py`):

```
utilities/
├── main.py                     ← GUI-Einstiegspunkt
├── src/                        ← GUI-Paket
├── client_secret.json          ← manuell hinterlegen
├── youtube_token.json          ← wird automatisch erstellt
├── convert_mjpeg_settings.json
└── …
```

> **Wichtig:** `client_secret.json` und `youtube_token.json` enthalten sensible Zugangsdaten und dürfen **nicht** in Git eingecheckt werden. Beide sind in `.gitignore` eingetragen.

---

## Schritt 1: Google Cloud Projekt erstellen

1. Öffne die [Google Cloud Console](https://console.cloud.google.com/)
2. Klicke oben links auf das Projekt-Dropdown → **Neues Projekt**
3. Name: z. B. `Fussballverein Video Upload`
4. **Erstellen** klicken

## Schritt 2: YouTube Data API aktivieren

1. Im Projekt: **APIs & Services → Bibliothek**
2. Nach `YouTube Data API v3` suchen
3. **Aktivieren** klicken

## Schritt 3: OAuth-Zustimmungsbildschirm konfigurieren

1. **APIs & Services → OAuth-Zustimmungsbildschirm**
2. Nutzertyp: **Extern** (oder **Intern** bei Google Workspace)
3. App-Name: z. B. `MJPEG Converter`
4. Support-E-Mail: deine eigene
5. Unter **Scopes** hinzufügen:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube` (für Playlist-Verwaltung)
6. Unter **Testnutzer** dein Google-Konto hinzufügen (solange die App nicht verifiziert ist)

## Schritt 4: OAuth-Client-ID erstellen

1. **APIs & Services → Anmeldedaten → + Anmeldedaten erstellen → OAuth-Client-ID**
2. Anwendungstyp: **Desktop-App**
3. Name: z. B. `MJPEG Converter Desktop`
4. **Erstellen** klicken
5. Im Dialog auf **JSON herunterladen** klicken
6. Die heruntergeladene Datei umbenennen in **`client_secret.json`**
7. Die Datei in das `utilities/`-Verzeichnis verschieben (neben `main.py`)

## Schritt 5: Erster Upload (Token-Erstellung)

1. In der GUI: **Einstellungen → YouTube** → „Videos auf YouTube hochladen" aktivieren
2. Mindestens einen Job in der Jobliste anlegen und YouTube-Titel setzen (Doppelklick auf Job)
3. **▶ Starten** klicken
4. Beim ersten Upload öffnet sich ein **Browser-Fenster** zur Google-Anmeldung
5. Mit dem Google-Konto anmelden, das als Testnutzer hinterlegt ist
6. Zugriff gewähren
7. Das Token wird automatisch als `youtube_token.json` gespeichert
8. Ab jetzt läuft der Upload **ohne erneute Anmeldung** (bis das Token abläuft)

---

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| `client_secret.json nicht gefunden` | Datei muss im Projektverzeichnis liegen (neben `main.py`) |
| `Token abgelaufen / ungültig` | `youtube_token.json` löschen und erneut anmelden |
| `Access blocked: App not verified` | Dein Google-Konto muss als Testnutzer eingetragen sein (Schritt 3.6) |
| `Quota exceeded` | YouTube API hat ein tägliches Limit von 10.000 Einheiten. Ein Upload kostet 1.600 Einheiten → max. ~6 Uploads/Tag mit Standard-Quota |
| `403 Forbidden` | Prüfe, ob die YouTube Data API v3 im Projekt aktiviert ist (Schritt 2) |

---

## Token erneuern

Das gespeicherte Token wird automatisch erneuert (Refresh-Token). Falls es trotzdem abläuft:

```bash
# Token löschen und beim nächsten Upload neu anmelden
rm youtube_token.json
```

---

## Sicherheitshinweise

- `client_secret.json` **niemals** teilen oder committen
- `youtube_token.json` berechtigt zum Upload auf den verknüpften YouTube-Kanal
- Bei Verdacht auf Missbrauch: Token in der [Google Cloud Console](https://console.cloud.google.com/) unter **APIs & Services → Anmeldedaten** widerrufen
