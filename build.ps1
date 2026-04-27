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
    --add-data "schema.sql;." `
    ".\src\tui.py"

$releaseDir = Join-Path $root "dist\MobiDB"
New-Item -ItemType Directory -Force (Join-Path $releaseDir "data") | Out-Null

Copy-Item -Force ".\remote_db.example.json" (Join-Path $releaseDir "remote_db.example.json")
Copy-Item -Force ".\schema.sql" (Join-Path $releaseDir "schema.sql")
Copy-Item -Force ".\data\mobidb.sqlite" (Join-Path $releaseDir "data\mobidb.sqlite")

for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        Compress-Archive -Path (Join-Path $releaseDir "*") -DestinationPath ".\dist\MobiDB.zip" -Force
        break
    }
    catch {
        if ($attempt -eq 5) {
            throw
        }
        Start-Sleep -Seconds 1
    }
}
Remove-Item -Recurse -Force ".\build", ".\MobiDB.spec" -ErrorAction SilentlyContinue

Write-Host "Build complete: $releaseDir"
Write-Host "Release zip: $(Join-Path $root 'dist\MobiDB.zip')"
