# 🎲 MemoMoji

Ein kleines Browserspiel: Hunderte Emojis erscheinen in der Bildschirmmitte und fallen mit Schwerkraft nach unten. Klicke immer zwei gleiche an, damit sie verschwinden. Wenn alle weg sind, hast du gewonnen. Dein Score ist die Zeit, die du gebraucht hast.

- 3 Schwierigkeitsstufen (80, 180 und 280 Emojis)
- Festes Spielfeld im Handy-Format (400 × 760) mit immer gleich großen Emojis, damit Zeiten auf allen Geräten vergleichbar sind
- Spielfeld drehen: am Desktop mit ⟲/⟳ oder den Pfeiltasten (← →), am Handy durch Drehen des Geräts. Die Schwerkraft dreht sich mit
- Konfetti beim Gewinnen
- „Neu“-Knopf (Taste R) startet die Stufe sofort neu, „Menü“ (Esc) führt zurück zur Stufenauswahl
- Eigene Physik: Verlet-Integration mit Kreis-Kollisionen, ruhende Emojis „schlafen“, damit nichts zittert
- Lokale Bestzeiten pro Stufe, mit Server zusätzlich eine gemeinsame Bestenliste

## Ohne Server spielen

`index.html` im Browser öffnen. Du brauchst nichts zu installieren und keinen Build. Die Bestenliste ist dann ausgeblendet, Bestzeiten werden nur im Browser gespeichert.

## Mit Server und gemeinsamer Bestenliste (Docker)

```sh
docker compose up -d --build
```

Der Container lauscht auf `127.0.0.1:8080` und liefert das Spiel und die API aus. Die SQLite-Datenbank liegt im Volume `memomoji-data` (`/data/memomoji.db`) und übersteht Neustarts und Updates.

**Reverse Proxy:** Einfach alles an `http://127.0.0.1:8080` weiterleiten. Das Spiel ruft die API relativ auf (`api/...`), das funktioniert also auch unter einem Unterpfad wie `https://example.org/memomoji/`. Der Server erkennt `/api/` auch mit Präfix. Beispiel für nginx:

```nginx
location /memomoji/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

**Update:** `git pull && docker compose up -d --build`

**Per SSH deployen und prüfen** (vom eigenen Rechner aus, im Repo-Ordner). Auf dem Server brauchst du nur Docker, git ist dort nicht nötig.

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 daniel@192.168.178.20 /home/daniel/memomoji
powershell -ExecutionPolicy Bypass -File scripts\smoketest.ps1 http://192.168.178.20:8080
```

Linux/macOS:

```sh
scripts/deploy.sh daniel@192.168.178.20 /home/daniel/memomoji
scripts/smoketest.sh http://192.168.178.20:8080
```

Das Deploy-Skript überträgt den aktuellen Commit (nicht gespeicherte oder nicht committete Änderungen also nicht) per `ssh`/`scp` und startet `docker compose`. Beim ersten Mal legt es auf dem Server eine `.env` aus `.env.example` an. Damit der Container direkt im LAN erreichbar ist (ohne Reverse Proxy), dort `MEMOMOJI_BIND=0.0.0.0` setzen und das Deploy-Skript nochmal ausführen. Der Smoke-Test prüft Spielseite, Bestenliste, Zeitprüfung und Admin-Schutz, ohne etwas einzutragen.

### Link-Vorschau beim Teilen (WhatsApp, Signal, Telegram, …)

`index.html` enthält Open-Graph-Angaben mit Titel, Beschreibung und dem Bild `og-image.jpg` (1200 × 630, ca. 110 KB). Messenger brauchen dafür eine **absolute** Bild-URL, die der Server beim Ausliefern einsetzt. Am zuverlässigsten trägst du die öffentliche Adresse in die `.env` ein:

```sh
MEMOMOJI_PUBLIC_URL=https://example.org/memomoji/
```

Ohne diese Angabe leitet der Server die Adresse aus `Host`, `X-Forwarded-Proto` und `X-Forwarded-Prefix` ab. Das klappt nur, wenn der Reverse Proxy diese Header setzt.

Prüfen: `curl -s https://example.org/memomoji/ | grep og:image` muss eine vollständige `https://…`-Adresse zeigen. WhatsApp speichert Vorschauen eine Weile zwischen. Nach einer Änderung hilft es, den Link einmal mit angehängtem `?v=2` zu teilen.

### Wie die Zeiten geprüft werden

Die Zeit für die Bestenliste misst der Server, nicht der Browser: Beim Fallen der Emojis startet das Spiel eine Runde (`POST api/games`), beim Gewinnen beendet es sie (`POST api/games/<id>/finish`). Danach kann die Runde genau einmal mit einem Namen eingetragen werden (`POST api/scores`). Unrealistisch schnelle Zeiten (unter 0,2 s pro Paar) werden abgelehnt, und pro IP gibt es ein einfaches Rate-Limit. Ganz fälschungssicher ist das bei einem Browserspiel nicht, für eine Runde unter Freunden reicht es.

Die Bestenliste zeigt pro Stufe die 10 besten Namen, jeweils mit ihrer besten Zeit.

### Admin-Seite

Unter `…/admin` (z. B. `https://example.org/memomoji/admin`) kannst du Einträge ansehen und löschen: einzeln oder alle Einträge eines Namens auf einmal.

Einschalten, indem du ein Passwort (mind. 8 Zeichen) in eine `.env`-Datei neben der `docker-compose.yml` schreibst:

```sh
cp .env.example .env
# MEMOMOJI_ADMIN_PASSWORD=ein-langes-eigenes-passwort eintragen
docker compose up -d
```

Ohne Passwort ist die Admin-Seite ausgeschaltet und liefert 404.

- Nach dem Anmelden bleibt die Sitzung 12 Stunden gültig (nur in diesem Browser-Tab). Ein Neustart des Containers meldet alle ab.
- Nach 5 falschen Passwörtern pro IP (oder 30 insgesamt) ist die Anmeldung für 15 Minuten gesperrt.
- Fehlgeschlagene Anmeldungen und Löschungen stehen im Log: `docker compose logs memomoji`
- Die Seite nur über HTTPS aufrufen, sonst geht das Passwort im Klartext übers Netz.
- Der Server nimmt die IP für Sperren aus dem letzten Eintrag von `X-Forwarded-For`, also dem, den dein Reverse Proxy setzt. Der Proxy sollte den Header daher wie im nginx-Beispiel oben setzen.

### Datenbank

```sh
docker compose exec memomoji python -c "import sqlite3; d=sqlite3.connect('/data/memomoji.db'); print(d.execute('select level,name,ms from scores order by level,ms').fetchall())"
```

Einen Eintrag löschen, z. B. per Name:

```sh
docker compose exec memomoji python -c "import sqlite3; d=sqlite3.connect('/data/memomoji.db'); d.execute(\"delete from scores where name='Cheater'\"); d.commit()"
```
