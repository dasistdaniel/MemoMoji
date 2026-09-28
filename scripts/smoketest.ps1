# Prüft einen laufenden MemoMoji-Server. Trägt nichts in die Bestenliste ein.
# Aufruf:
#   powershell -ExecutionPolicy Bypass -File scripts\smoketest.ps1 http://192.168.178.20:8080
param([string]$Url = "http://127.0.0.1:8080")
$Url = $Url.TrimEnd("/")
$script:fail = $false

function Request([string]$path, [string]$method = "GET", [string]$body = $null) {
    $params = @{ Uri = "$Url$path"; Method = $method; UseBasicParsing = $true; ErrorAction = "Stop" }
    if ($body) { $params.Body = $body; $params.ContentType = "application/json" }
    try {
        $r = Invoke-WebRequest @params
        return @{ Code = [int]$r.StatusCode; Body = [string]$r.Content }
    }
    catch {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { return @{ Code = 0; Body = $_.Exception.Message } }
        return @{ Code = [int]$resp.StatusCode; Body = "" }
    }
}

function Check([string]$name, $expected, $actual) {
    if ("$expected" -eq "$actual") { Write-Host "ok     $name" }
    else { Write-Host "FEHLER $name`: erwartet '$expected', bekommen '$actual'" -ForegroundColor Red; $script:fail = $true }
}

$health = Request "/api/health"
Check "Health" 200 $health.Code
if ($health.Code -eq 0) { Write-Host "Server nicht erreichbar: $($health.Body)" -ForegroundColor Red; exit 1 }

$index = Request "/"
Check "Spielseite" 200 $index.Code
Check "Spielseite enthält Spiel" $true ($index.Body -like "*<title>MemoMoji</title>*")
Check "Bestenliste lesen" 200 (Request "/api/scores?level=40").Code
Check "Unbekannte Stufe" 400 (Request "/api/scores?level=41").Code

$game = Request "/api/games" "POST" '{"level":40}'
$id = if ($game.Code -eq 200) { ($game.Body | ConvertFrom-Json).id } else { "" }
Check "Runde starten" $true ([bool]$id)
$finish = Request "/api/games/$id/finish" "POST" '{}'
$valid = if ($finish.Code -eq 200) { ($finish.Body | ConvertFrom-Json).valid } else { "?" }
Check "Zu schnelle Runde ungültig" $false $valid
Check "Zu schnelle Zeit abgelehnt" 400 (Request "/api/scores" "POST" ('{"id":"' + $id + '","name":"Test"}')).Code

$admin = (Request "/admin").Code
switch ($admin) {
    200 { Write-Host "ok     Admin-Seite ist an"
          Check "Admin ohne Anmeldung gesperrt" 401 (Request "/api/admin/scores?level=40").Code }
    404 { Write-Host "info   Admin-Seite ist aus (kein MEMOMOJI_ADMIN_PASSWORD gesetzt)" }
    default { Check "Admin-Seite" "200 oder 404" $admin }
}

if ($script:fail) { Write-Host "Es gab Fehler." -ForegroundColor Red; exit 1 }
Write-Host "Alles in Ordnung." -ForegroundColor Green
