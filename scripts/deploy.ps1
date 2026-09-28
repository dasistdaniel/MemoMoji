# Überträgt den aktuellen Commit per SSH auf einen Server und startet den Container.
# Aufruf (im Repo-Ordner):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 daniel@192.168.178.20 /home/daniel/memomoji
# Braucht git sowie ssh und scp (in Windows 10/11 enthalten).
param(
    [string]$Target = "daniel@192.168.178.20",
    [string]$Dir = "/home/daniel/memomoji"
)
$ErrorActionPreference = "Stop"

function Invoke-Checked([string]$what, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$what ist fehlgeschlagen (Exit-Code $LASTEXITCODE)" }
}

$repo = Split-Path -Parent $PSScriptRoot
$tar = Join-Path ([IO.Path]::GetTempPath()) "memomoji-deploy.tar"
$commit = (git -C $repo rev-parse --short HEAD)
if ($LASTEXITCODE -ne 0) { throw "Kein git-Repository gefunden in $repo" }

Write-Host "-> Packe Commit $commit"
Invoke-Checked "git archive" { git -C $repo archive --format=tar -o $tar HEAD }

try {
    Write-Host "-> Kopiere nach ${Target}:$Dir"
    Invoke-Checked "scp" { scp -q $tar "${Target}:/tmp/memomoji-deploy.tar" }

    Write-Host "-> Entpacke und starte Container"
    $remote = "set -e; mkdir -p '$Dir'; tar -xf /tmp/memomoji-deploy.tar -C '$Dir'; rm -f /tmp/memomoji-deploy.tar; cd '$Dir'; " +
              "if [ ! -f .env ]; then cp .env.example .env; echo '! .env angelegt, bitte MEMOMOJI_ADMIN_PASSWORD eintragen'; fi; " +
              "docker compose up -d --build; docker compose ps"
    Invoke-Checked "ssh" { ssh $Target $remote }
    Write-Host "Fertig."
}
finally {
    Remove-Item -ErrorAction SilentlyContinue $tar
}
