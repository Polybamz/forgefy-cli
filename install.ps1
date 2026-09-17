# Install the standalone forgefy CLI binary (no Python required).
#
#   irm https://raw.githubusercontent.com/Polybamz/forgefy-cli/main/install.ps1 | iex
#
# Downloads the latest GitHub Release Windows binary and installs it as
# forgefy.exe. By default it installs into %LOCALAPPDATA%\Microsoft\WindowsApps,
# a per-user directory Windows itself already puts on PATH (it's where Store
# "app execution alias" stubs like python.exe live) - so no PATH edit is
# needed at all, in this terminal or any future one. If that directory isn't
# present (non-standard setups), it falls back to installing into
# %LOCALAPPDATA%\forgefy\bin and adding that to the User PATH instead, which
# does require opening a new terminal.
$ErrorActionPreference = "Stop"

$repo = "Polybamz/forgefy-cli"
$asset = "forgefy-windows-amd64.exe"
$url = "https://github.com/$repo/releases/latest/download/$asset"

$aliasDir = "$env:LOCALAPPDATA\Microsoft\WindowsApps"
$needsPathEdit = $false

if ($env:FORGEFY_INSTALL_DIR) {
    $installDir = $env:FORGEFY_INSTALL_DIR
    $needsPathEdit = $true
} elseif (Test-Path $aliasDir) {
    $installDir = $aliasDir
} else {
    $installDir = "$env:LOCALAPPDATA\forgefy\bin"
    $needsPathEdit = $true
}

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

if ($needsPathEdit) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $installDir) {
        [Environment]::SetEnvironmentVariable("Path", "$installDir;$userPath", "User")
        Write-Host ""
        Write-Host "Added $installDir to your User PATH. Open a NEW terminal for this to take effect."
    } else {
        Write-Host ""
        Write-Host "$installDir is already on your PATH."
    }
} else {
    Write-Host "$installDir is already on your PATH - no setup needed, even in this terminal."
}

Write-Host "Then run 'forgefy --help' to get started."
