"""MemoMoji-Server: liefert index.html aus und verwaltet die gemeinsame Bestenliste.

Nur Python-Standardbibliothek (http.server + sqlite3), keine Abhängigkeiten.

Ablauf einer Runde (die Zeit misst der Server, nicht der Browser):
  POST api/games                  {"level": 75}       -> {"id": "..."}
  POST api/games/<id>/finish                          -> {"ms": 81234}
  POST api/scores                 {"id": "...", "name": "Anna"}
                                                      -> {"rank": 3, "scores": [...]}
  GET  api/scores?level=75                            -> {"scores": [...]}

Admin (nur wenn ADMIN_PASSWORD gesetzt ist, Seite unter .../admin):
  POST   api/admin/login          {"password": "..."} -> {"token": "..."}
  POST   api/admin/logout
  GET    api/admin/scores?level=75                    -> alle Einträge der Stufe
  DELETE api/admin/scores/<id>
  POST   api/admin/delete-name    {"name": "..."}     -> löscht alle Einträge des Namens
Admin-Anfragen schicken den Token als "Authorization: Bearer <token>".
"""

import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "/data/memomoji.db")
# Öffentliche Adresse des Spiels (z. B. https://example.org/memomoji/), für die
# Link-Vorschau beim Teilen. Leer: wird aus den Anfrage-Headern abgeleitet.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip()
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "public"))

# Anzahl Paare pro Stufe (muss zu LEVELS in index.html passen)
LEVELS = {40, 90, 140}
# Schneller als das ist kein Mensch: Mindestzeit pro Paar in ms
MIN_MS_PER_PAIR = 200
# Unbeendete oder nicht eingetragene Runden werden nach dieser Zeit gelöscht
GAME_TTL_S = 24 * 3600
MAX_GAME_S = 6 * 3600
TOP_N = 10
MAX_BODY = 1024
NAME_RE = re.compile(r"[\x00-\x1f\x7f<>]")

# Einfaches Rate-Limit pro IP für schreibende Anfragen
RATE_WINDOW_S = 60
RATE_MAX = 30

# Admin: Passwort aus der Umgebung, Sitzungen nur im Speicher
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_MIN_LEN = 8
ADMIN_SESSION_S = 12 * 3600
LOGIN_WINDOW_S = 15 * 60
LOGIN_MAX_PER_IP = 5      # Fehlversuche pro IP im Zeitfenster
LOGIN_MAX_TOTAL = 30      # Fehlversuche insgesamt im Zeitfenster, dann Sperre für alle
admin_enabled = len(ADMIN_PASSWORD) >= ADMIN_MIN_LEN
sessions = {}             # token -> Ablaufzeit
failed_logins = []        # (zeit, ip)

lock = threading.Lock()
rate = {}


def connect():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            level INTEGER NOT NULL,
            started REAL NOT NULL,
            finished REAL,
            submitted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level INTEGER NOT NULL,
            name TEXT NOT NULL,
            ms INTEGER NOT NULL,
            created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS scores_level_ms ON scores(level, ms);
        """
    )
    return db


os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
db = connect()


def top_scores(level):
    # Pro Name nur die beste Zeit
    rows = db.execute(
        """
        SELECT name, MIN(ms) AS best, MIN(created)
        FROM scores WHERE level = ?
        GROUP BY name COLLATE NOCASE
        ORDER BY best ASC LIMIT ?
        """,
        (level, TOP_N),
    ).fetchall()
    return [{"name": r[0], "ms": r[1]} for r in rows]


def cleanup():
    db.execute("DELETE FROM games WHERE started < ?", (time.time() - GAME_TTL_S,))


class Handler(BaseHTTPRequestHandler):
    server_version = "MemoMoji"

    # ----- Hilfen -----
    def client_ip(self):
        # Der letzte Eintrag stammt vom eigenen Reverse Proxy; frühere Einträge
        # kann der Client selbst mitschicken und damit fälschen.
        fwd = self.headers.get("X-Forwarded-For")
        return fwd.split(",")[-1].strip() if fwd else self.client_address[0]

    def is_admin(self):
        auth = self.headers.get("Authorization", "")
        if not admin_enabled or not auth.startswith("Bearer "):
            return False
        token = auth[7:].strip()
        now = time.time()
        with lock:
            for t, exp in list(sessions.items()):
                if exp < now:
                    del sessions[t]
            return any(hmac.compare_digest(token, t) for t in sessions)

    def send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.send_body(body)

    def error(self, status, msg):
        self.send_json(status, {"error": msg})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            return data if isinstance(data, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    def rate_limited(self):
        now = time.time()
        ip = self.client_ip()
        with lock:
            hits = [t for t in rate.get(ip, []) if now - t < RATE_WINDOW_S]
            hits.append(now)
            rate[ip] = hits
            if len(rate) > 10000:
                rate.clear()
        return len(hits) > RATE_MAX

    def api_path(self):
        # Funktioniert auch, wenn der Reverse Proxy ein Präfix mitschickt (/memomoji/api/...)
        path = urlsplit(self.path).path
        i = path.find("/api/")
        return path[i + 4:] if i >= 0 else None

    def log_message(self, fmt, *args):
        print(f"{self.client_ip()} {fmt % args}", flush=True)

    # ----- GET -----
    def do_GET(self):
        api = self.api_path()
        if api is None:
            return self.serve_static()
        if api == "/admin/scores":
            if not self.is_admin():
                return self.error(401, "Bitte neu anmelden")
            q = parse_qs(urlsplit(self.path).query)
            try:
                level = int(q.get("level", [""])[0])
            except ValueError:
                return self.error(400, "Unbekannte Stufe")
            with lock:
                rows = db.execute(
                    "SELECT id, name, ms, created FROM scores WHERE level = ? ORDER BY ms ASC LIMIT 2000",
                    (level,),
                ).fetchall()
            return self.send_json(200, {
                "scores": [{"id": r[0], "name": r[1], "ms": r[2], "created": r[3]} for r in rows]
            })
        if api == "/health":
            return self.send_json(200, {"ok": True})
        if api == "/scores":
            q = parse_qs(urlsplit(self.path).query)
            try:
                level = int(q.get("level", [""])[0])
            except ValueError:
                return self.error(400, "Unbekannte Stufe")
            if level not in LEVELS:
                return self.error(400, "Unbekannte Stufe")
            with lock:
                scores = top_scores(level)
            return self.send_json(200, {"scores": scores})
        self.error(404, "Nicht gefunden")

    def public_base(self):
        """Absolute Basis-URL des Spiels, auf "/" endend."""
        if PUBLIC_URL:
            return PUBLIC_URL if PUBLIC_URL.endswith("/") else PUBLIC_URL + "/"
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip()
        prefix = (self.headers.get("X-Forwarded-Prefix") or "").rstrip("/")
        path = urlsplit(self.path).path
        directory = path[: path.rfind("/") + 1]
        if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host) or proto not in ("http", "https"):
            return ""
        return f"{proto}://{host}{prefix}{directory}"

    def do_HEAD(self):
        # Manche Link-Vorschauen fragen erst mit HEAD an
        self.head_only = True
        self.do_GET()

    def send_body(self, body):
        if not getattr(self, "head_only", False):
            self.wfile.write(body)

    def serve_static(self):
        path = urlsplit(self.path).path
        if path.endswith("/og-image.jpg"):
            try:
                with open(os.path.join(STATIC_DIR, "og-image.jpg"), "rb") as f:
                    body = f.read()
            except OSError:
                return self.error(404, "Nicht gefunden")
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return self.send_body(body)
        if path.endswith("/") and not path.endswith("/admin/") or path.endswith("/index.html"):
            name = "index.html"
        elif path.endswith(("/admin", "/admin/", "/admin.html")) and admin_enabled:
            name = "admin.html"
        else:
            return self.error(404, "Nicht gefunden")
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                body = f.read()
        except OSError:
            return self.error(500, name + " fehlt")
        if name == "index.html":
            # Link-Vorschau braucht absolute URLs
            base = self.public_base()
            if base:
                body = body.replace(
                    b'content="og-image.jpg"', b'content="' + base.encode() + b'og-image.jpg"'
                ).replace(
                    b'<meta property="og:type"',
                    b'<meta property="og:url" content="' + base.encode() + b'">\n<meta property="og:type"',
                )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if name == "admin.html":
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'; form-action 'none'",
            )
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.send_body(body)

    # ----- POST -----
    def do_POST(self):
        api = self.api_path()
        if api is None:
            return self.error(404, "Nicht gefunden")
        if api.startswith("/admin/"):
            return self.admin_post(api)
        if self.rate_limited():
            return self.error(429, "Zu viele Anfragen, bitte kurz warten")
        data = self.read_json()
        if data is None:
            return self.error(400, "Ungültige Anfrage")

        if api == "/games":
            level = data.get("level")
            if level not in LEVELS:
                return self.error(400, "Unbekannte Stufe")
            gid = secrets.token_urlsafe(16)
            with lock, db:
                cleanup()
                db.execute(
                    "INSERT INTO games (id, level, started) VALUES (?, ?, ?)",
                    (gid, level, time.time()),
                )
            return self.send_json(200, {"id": gid})

        m = re.fullmatch(r"/games/([A-Za-z0-9_-]{10,64})/finish", api)
        if m:
            now = time.time()
            with lock, db:
                row = db.execute(
                    "SELECT level, started, finished FROM games WHERE id = ?", (m.group(1),)
                ).fetchone()
                if not row:
                    return self.error(404, "Runde nicht gefunden")
                level, started, finished = row
                if finished is None:
                    finished = now
                    db.execute("UPDATE games SET finished = ? WHERE id = ?", (now, m.group(1)))
            ms = int((finished - started) * 1000)
            valid = level * MIN_MS_PER_PAIR <= ms <= MAX_GAME_S * 1000
            return self.send_json(200, {"ms": ms, "valid": valid})

        if api == "/scores":
            gid = data.get("id")
            name = data.get("name")
            if not isinstance(gid, str) or not isinstance(name, str):
                return self.error(400, "Ungültige Anfrage")
            name = NAME_RE.sub("", name).strip()[:20]
            if not name:
                return self.error(400, "Bitte einen Namen eingeben")
            with lock, db:
                row = db.execute(
                    "SELECT level, started, finished, submitted FROM games WHERE id = ?", (gid,)
                ).fetchone()
                if not row or row[2] is None:
                    return self.error(404, "Runde nicht gefunden")
                level, started, finished, submitted = row
                if submitted:
                    return self.error(409, "Diese Runde ist schon eingetragen")
                ms = int((finished - started) * 1000)
                if not (level * MIN_MS_PER_PAIR <= ms <= MAX_GAME_S * 1000):
                    return self.error(400, "Diese Zeit kann nicht stimmen")
                db.execute("UPDATE games SET submitted = 1 WHERE id = ?", (gid,))
                db.execute(
                    "INSERT INTO scores (level, name, ms, created) VALUES (?, ?, ?, ?)",
                    (level, name, ms, time.time()),
                )
                scores = top_scores(level)
                # Platz dieser Zeit unter den Bestzeiten aller Namen
                rank = 1 + db.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT MIN(ms) AS best FROM scores
                        WHERE level = ? AND name != ? COLLATE NOCASE
                        GROUP BY name COLLATE NOCASE
                    ) WHERE best < ?
                    """,
                    (level, name, ms),
                ).fetchone()[0]
            return self.send_json(200, {"ms": ms, "rank": rank, "name": name, "scores": scores})

        self.error(404, "Nicht gefunden")


    def admin_post(self, api):
        if not admin_enabled:
            return self.error(404, "Nicht gefunden")
        data = self.read_json()
        if data is None:
            return self.error(400, "Ungültige Anfrage")

        if api == "/admin/login":
            ip, now = self.client_ip(), time.time()
            with lock:
                failed_logins[:] = [(t, i) for t, i in failed_logins if now - t < LOGIN_WINDOW_S]
                if (len(failed_logins) >= LOGIN_MAX_TOTAL
                        or sum(1 for _, i in failed_logins if i == ip) >= LOGIN_MAX_PER_IP):
                    return self.error(429, "Zu viele Fehlversuche, bitte in 15 Minuten erneut versuchen")
                pw = data.get("password")
                if not isinstance(pw, str) or not hmac.compare_digest(
                    pw.encode(), ADMIN_PASSWORD.encode()
                ):
                    failed_logins.append((now, ip))
                    print(f"{ip} Admin-Anmeldung fehlgeschlagen", flush=True)
                    return self.error(401, "Falsches Passwort")
                token = secrets.token_urlsafe(32)
                sessions[token] = now + ADMIN_SESSION_S
            return self.send_json(200, {"token": token})

        if not self.is_admin():
            return self.error(401, "Bitte neu anmelden")

        if api == "/admin/logout":
            token = self.headers.get("Authorization", "")[7:].strip()
            with lock:
                sessions.pop(token, None)
            return self.send_json(200, {"ok": True})

        if api == "/admin/delete-name":
            name = data.get("name")
            if not isinstance(name, str) or not name.strip():
                return self.error(400, "Name fehlt")
            with lock, db:
                n = db.execute(
                    "DELETE FROM scores WHERE name = ? COLLATE NOCASE", (name.strip(),)
                ).rowcount
            print(f"{self.client_ip()} Admin: {n} Einträge von {name!r} gelöscht", flush=True)
            return self.send_json(200, {"deleted": n})

        self.error(404, "Nicht gefunden")

    def do_DELETE(self):
        api = self.api_path() or ""
        m = re.fullmatch(r"/admin/scores/(\d+)", api)
        if not m:
            return self.error(404, "Nicht gefunden")
        if not self.is_admin():
            return self.error(401, "Bitte neu anmelden")
        with lock, db:
            n = db.execute("DELETE FROM scores WHERE id = ?", (int(m.group(1)),)).rowcount
        if not n:
            return self.error(404, "Eintrag nicht gefunden")
        print(f"{self.client_ip()} Admin: Eintrag {m.group(1)} gelöscht", flush=True)
        self.send_json(200, {"deleted": n})


if __name__ == "__main__":
    if ADMIN_PASSWORD and not admin_enabled:
        print(f"ADMIN_PASSWORD ist kürzer als {ADMIN_MIN_LEN} Zeichen, Admin-Seite bleibt aus", flush=True)
    print(f"Admin-Seite: {'an' if admin_enabled else 'aus (ADMIN_PASSWORD nicht gesetzt)'}", flush=True)
    print(f"MemoMoji läuft auf Port {PORT}, Datenbank: {DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
