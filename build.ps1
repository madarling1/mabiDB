$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m PyInstaller --version | Out-Null

Remove-Item -Recurse -Force ".\build", ".\dist\MobiDB", ".\dist\MobiDB.exe", ".\dist\MobiDB.zip", ".\MobiDB.spec" -ErrorAction SilentlyContinue

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name MobiDB `
    --add-data "resources\schema.sql;resources" `
    ".\src\tui.py"

$releaseDir = Join-Path $root "dist\MobiDB"
New-Item -ItemType Directory -Force (Join-Path $releaseDir "data") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $releaseDir "config") | Out-Null

if (Test-Path ".\config\remote_db.example.json") {
    Copy-Item -Force ".\config\remote_db.example.json" (Join-Path $releaseDir "config\remote_db.example.json")
}
if (Test-Path ".\config\remote_db.json") {
    Copy-Item -Force ".\config\remote_db.json" (Join-Path $releaseDir "config\remote_db.json")
}
if (Test-Path ".\remote_db.json") {
    Copy-Item -Force ".\remote_db.json" (Join-Path $releaseDir "config\remote_db.json")
}
Copy-Item -Force ".\USER_GUIDE.txt" (Join-Path $releaseDir "USER_GUIDE.txt")
Copy-Item -Force ".\data\mobidb.sqlite" (Join-Path $releaseDir "data\mobidb.sqlite")

Remove-Item -Force ".\dist\MobiDB.zip" -ErrorAction SilentlyContinue
tar.exe -a -cf ".\dist\MobiDB.zip" -C $releaseDir .
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Remove-Item -Recurse -Force ".\build", ".\MobiDB.spec" -ErrorAction SilentlyContinue

Write-Host "Build complete: $releaseDir"
Write-Host "Release zip: $(Join-Path $root 'dist\MobiDB.zip')"
