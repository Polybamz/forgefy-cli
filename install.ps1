# Install the standalone forgefy CLI binary (no Python required).
#
#   irm https://raw.githubusercontent.com/Polybamz/forgefy-cli/main/install.ps1 | iex
#
# Downloads the latest GitHub Release Windows binary, installs it to
# %LOCALAPPDATA%\forgefy\bin\forgefy.exe, and adds that directory to your
# User PATH if it isn't already there.
$ErrorActionPreference = "Stop"

$repo = "Polybamz/forgefy-cli"
$installDir = if ($env:FORGEFY_INSTALL_DIR) { $env:FORGEFY_INSTALL_DIR } else { "$env:LOCALAPPDATA\forgefy\bin" }
$asset = "forgefy-windows-amd64.exe"
$url = "https://github.com/$repo/releases/latest/download/$asset"

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$dest = Join-Path $installDir "forgefy.exe"

Write-Host "Downloading $asset..."
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
} catch {
    Write-Host "forgefy install.ps1: could not download $url" -ForegroundColor Red
    Write-Host "Try 'pip install forgefy-cli' or 'pipx install forgefy-cli' instead." -ForegroundColor Yellow
    exit 1
}

Write-Host "Installed forgefy to $dest"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $installDir) {
    [Environment]::SetEnvironmentVariable("Path", "$installDir;$userPath", "User")
    Write-Host ""
    Write-Host "Added $installDir to your User PATH. Open a NEW terminal for this to take effect."
} else {
    Write-Host ""
    Write-Host "$installDir is already on your PATH."
}

Write-Host "Then run 'forgefy --help' to get started."
