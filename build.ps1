$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m PyInstaller --version | Out-Null

Remove-Item -Recurse -Force ".\build", ".\dist\mabiDB", ".\dist\mabiDB.exe", ".\dist\mabiDB.zip", ".\mabiDB.spec" -ErrorAction SilentlyContinue

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name mabiDB `
    --add-data "resources\schema.sql;resources" `
    --add-data "data\mobidb.sqlite;data" `
    --add-data "data\db_version.txt;data" `
    --add-data "config\remote_db.json;config" `
    ".\src\main.py"

Remove-Item -Recurse -Force ".\build", ".\mabiDB.spec" -ErrorAction SilentlyContinue

Write-Host "Build complete: $(Join-Path $root 'dist\mabiDB.exe')"

