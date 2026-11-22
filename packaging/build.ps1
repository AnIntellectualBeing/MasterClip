param([string]$PythonPath = "")
# Builds dist\MasterClipSetup-<version>.exe
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Work files go to %USERPROFILE%\ClipStudioBuild (C: has the room). The vendor folder there must hold
# bin\ffmpeg.exe, bin\ffprobe.exe, bin\deno.exe and FFMPEG-LICENSE.txt (see packaging\README.md).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $env:USERPROFILE "ClipStudioBuild"
$Vendor = Join-Path $Build "vendor"
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Root ".venv\Scripts\python.exe" }
$Iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { $Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }

foreach ($tool in "ffmpeg.exe", "ffprobe.exe", "deno.exe") {
    if (-not (Test-Path (Join-Path $Vendor "bin\$tool"))) { throw "Missing $Vendor\bin\$tool" }
}
if (-not (Test-Path $Iscc)) { throw "Inno Setup 6 not found. winget install JRSoftware.InnoSetup --scope user" }

# Never ship a local .env or secrets: the spec only collects static\, fonts\, models\ and code.
Copy-Item (Join-Path $Vendor "FFMPEG-LICENSE.txt") (Join-Path $PSScriptRoot "licences\FFMPEG-LICENSE.txt") -Force

# Local speech recognition works right after install: ship Whisper "base" (MIT, ~145 MB) in models\whisper-base.
$Whisper = Join-Path $Root "models\whisper-base"
& $Python -c "import shutil, sys; from pathlib import Path; from faster_whisper.utils import download_model; src = Path(download_model('base')); dst = Path(sys.argv[1]); dst.mkdir(parents=True, exist_ok=True); [shutil.copy2(src / f, dst / f) for f in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt')]" $Whisper
if ($LASTEXITCODE -or -not (Test-Path (Join-Path $Whisper "model.bin"))) { throw "Couldn't get the Whisper base model to bundle" }

$env:CLIPSTUDIO_VENDOR = $Vendor
& $Python (Join-Path $PSScriptRoot "make_icon.py")
& $Python -m PyInstaller (Join-Path $PSScriptRoot "clipstudio.spec") --noconfirm --clean `
    --distpath (Join-Path $Build "dist") --workpath (Join-Path $Build "work")
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$App = Join-Path $Build "dist\Clip Studio"
$leaks = @(Get-ChildItem $App -Recurse -Force -Include ".env", ".env.*", "posting.json", "ai.json", "*.key" -ErrorAction SilentlyContinue)
$leaks += Get-ChildItem $App -Recurse -Force -Include "*.pem" -ErrorAction SilentlyContinue |
    Where-Object { Select-String -Path $_.FullName -Pattern "PRIVATE KEY" -Quiet }
if ($leaks) { throw "Refusing to package private files: $($leaks.FullName -join ', ')" }

# What the buyer's app will use for buying and seats (clipstudio\shop.py; env vars don't travel to the buyer's PC).
Remove-Item Env:CLIPSTUDIO_LICENCE_API, Env:CLIPSTUDIO_BUY_URL -ErrorAction SilentlyContinue
$shop = & $Python -c "import sys; sys.path.insert(0, r'$Root'); from clipstudio import shop; print(shop.LICENCE_API or '-', shop.BUY_URL or '-')"
if ($shop -eq "- -") { Write-Warning "clipstudio\shop.py is empty: this build has no Buy button and no 2-PC limit (keys still work)." }
else { Write-Host "Shop: $shop" }

$Out = Join-Path $Root "dist"
New-Item -ItemType Directory -Force $Out | Out-Null
Push-Location $PSScriptRoot
& $Iscc "/DSourceDir=$App" "/DOutDir=$Out" "ClipStudio.iss"
$code = $LASTEXITCODE
Pop-Location
if ($code) { throw "Inno Setup failed" }
Get-ChildItem $Out -Filter "MasterClipSetup-*.exe" | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB) } }
