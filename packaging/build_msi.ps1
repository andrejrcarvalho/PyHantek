# Build dist\PyHantek-<version>-windows-x64.msi
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File packaging\build_msi.ps1
# Needs the .NET SDK (for the WiX toolset, installed here as a dotnet tool if missing).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$wixVersion = "5.0.2"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
    Write-Host "Installing WiX $wixVersion..."
    dotnet tool install --global wix --version $wixVersion
    if ($LASTEXITCODE -ne 0) { throw "installing WiX failed" }
    $env:Path = "$env:USERPROFILE\.dotnet\tools;$env:Path"
}

# Keep the Windows environment separate from a Linux/WSL .venv in the same folder
$env:UV_PROJECT_ENVIRONMENT = ".venv-windows"

uv sync --group build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
$version = (uv run python -c "import pyhantek; print(pyhantek.__version__)").Trim()
# MSI versions must be plain numbers (major.minor.build), so drop any suffix like ".dev1"
$msiVersion = [regex]::Match($version, '^\d+(\.\d+){0,2}').Value

# A folder build (not the one-file exe): starts faster and installs cleanly into Program Files
$env:PYHANTEK_ONEDIR = "1"
uv run pyinstaller --noconfirm --clean --distpath build\dist-msi --workpath build\work-msi packaging\pyhantek.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
Remove-Item Env:PYHANTEK_ONEDIR

New-Item -ItemType Directory -Force dist | Out-Null
$out = "dist\PyHantek-$version-windows-x64.msi"
$appDir = (Resolve-Path build\dist-msi\PyHantek).Path
$icon = (Resolve-Path pyhantek\assets\pyhantek.ico).Path
wix build -arch x64 -d "Version=$msiVersion" -d "AppDir=$appDir" -d "Icon=$icon" -o $out packaging\pyhantek.wxs
if ($LASTEXITCODE -ne 0) { throw "wix build failed" }
Write-Host "Built $out"
