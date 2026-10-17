# Build dist\PyHantek-<version>-windows-x64.exe
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# Keep the Windows environment separate from a Linux/WSL .venv in the same folder
$env:UV_PROJECT_ENVIRONMENT = ".venv-windows"

uv sync --group build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
$version = (uv run python -c "import pyhantek; print(pyhantek.__version__)").Trim()

uv run pyinstaller --noconfirm --clean --distpath build\dist-windows --workpath build\work-windows packaging\pyhantek.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

New-Item -ItemType Directory -Force dist | Out-Null
$out = "dist\PyHantek-$version-windows-x64.exe"
Copy-Item build\dist-windows\PyHantek.exe $out -Force
Write-Host "Built $out"
