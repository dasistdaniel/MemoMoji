#!/bin/sh
# Prüft einen laufenden MemoMoji-Server. Trägt nichts in die Bestenliste ein.
# Aufruf: scripts/smoketest.sh http://192.168.178.20:8080
set -u
URL="${1:-http://127.0.0.1:8080}"
URL="${URL%/}"
fail=0
check() { # name, erwartet, tatsächlich
  if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FEHLER $1: erwartet '$2', bekommen '$3'"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
json() { curl -s -H 'Content-Type: application/json' "$@"; }

check "Health"                 200 "$(code "$URL/api/health")"
check "Spielseite"             200 "$(code "$URL/")"
check "Spielseite enthält Spiel" 1 "$(curl -s "$URL/" | grep -c '<title>MemoMoji</title>')"
check "Bestenliste lesen"      200 "$(code "$URL/api/scores?level=40")"
check "Unbekannte Stufe"       400 "$(code "$URL/api/scores?level=41")"

id=$(json -d '{"level":40}' "$URL/api/games" | sed -n 's/.*"id": *"\([^"]*\)".*/\1/p')
check "Runde starten"          1 "$([ -n "$id" ] && echo 1 || echo 0)"
check "Zu schnelle Runde ungültig" 1 "$(json -X POST -d '{}' "$URL/api/games/$id/finish" | grep -c '"valid": false')"
check "Zu schnelle Zeit abgelehnt" 400 "$(code -H 'Content-Type: application/json' -d "{\"id\":\"$id\",\"name\":\"Test\"}" "$URL/api/scores")"

admin=$(code "$URL/admin")
case "$admin" in
  200) echo "ok    Admin-Seite ist an"
       check "Admin ohne Anmeldung gesperrt" 401 "$(code "$URL/api/admin/scores?level=40")" ;;
  404) echo "info  Admin-Seite ist aus (kein MEMOMOJI_ADMIN_PASSWORD gesetzt)" ;;
  *)   echo "FEHLER Admin-Seite: HTTP $admin"; fail=1 ;;
esac

[ $fail = 0 ] && echo "Alles in Ordnung." || { echo "Es gab Fehler."; exit 1; }
