# 🎲 MemoMoji

Ein kleines Browserspiel: Hunderte Emojis erscheinen in der Bildschirmmitte und fallen mit Schwerkraft nach unten. Klicke immer zwei gleiche an, damit sie verschwinden. Wenn alle weg sind, hast du gewonnen. Dein Score ist die Zeit, die du gebraucht hast.

- 4 Schwierigkeitsstufen (80 bis 500 Emojis)
- Eigene Physik: Verlet-Integration mit Kreis-Kollisionen, ruhende Emojis „schlafen“, damit nichts zittert
- Funktioniert auf Desktop und Handy (auch beim Drehen)
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
