<#
.SYNOPSIS
    One-step launcher for Prompt Redaction Studio.

    Sets up the environment if it has not been set up yet, starts the server, and opens
    the browser. Safe to run repeatedly -- after the first run it goes straight to launch.

    Double-click Start.cmd rather than this file: PowerShell scripts open in Notepad on
    double-click, and ExecutionPolicy blocks them even when they do run.

.PARAMETER Port
    Port to listen on. Defaults to 8000.

.PARAMETER Small
    Use only en_core_web_sm (~12 MB) instead of also downloading en_core_web_lg (~590 MB).
    The large model detects noticeably more names and places.

.PARAMETER NoBrowser
    Do not open the browser once the server is up.

.PARAMETER SetupOnly
    Do the setup work and exit without starting the server.

.PARAMETER Tray
    Install the desktop tray app without asking.

.PARAMETER NoTray
    Skip the desktop tray app without asking.

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Small -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Small,
    [switch]$NoBrowser,
    [switch]$SetupOnly,
    [switch]$Tray,
    [switch]$NoTray
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Invoke-WebRequest's progress bar makes large downloads several times slower on
# Windows PowerShell 5.1. We print our own progress instead.
$ProgressPreference = "SilentlyContinue"

$Repo = "Bhairav-Pardiwala/PromptRedactionStudio"
$StampPath = Join-Path $PSScriptRoot ".venv\.setup-stamp"
$MinMinor = 10   # Presidio supports >=3.10,<3.15
$MaxMinor = 14

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host $Text -ForegroundColor Yellow
}

function Write-Info {
    param([string]$Text)
    Write-Host "      $Text" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- interpreter

function Get-InterpreterMinor {
    <# Returns the minor version of a Python executable, or $null if it cannot be run. #>
    param([string]$Exe)
    try {
        $output = & $Exe -c "import sys; print(sys.version_info[0]); print(sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -ne 0 -or $output.Count -lt 2) { return $null }
        if ([int]$output[0] -ne 3) { return $null }
        return [int]$output[1]
    } catch {
        return $null
    }
}

function Find-Python {
    <# Pick the highest interpreter inside Presidio's supported range.

    Deliberately not `py -3`: that selects the newest installed 3.x, which may be outside
    the supported range. And the bare `python` on PATH can be something unusable -- on the
    original development machine it is an Anaconda 3.5 (see the note in setup.ps1).
    #>
    $candidates = @()

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        # Two formats exist in the wild: " -V:3.13 *   C:\...\python.exe" (newer) and
        # " -3.11-64    C:\...\python.exe" (older).
        foreach ($line in (& py -0p 2>$null)) {
            $match = [regex]::Match($line, '^\s*-(?:V:)?(\d+)\.(\d+)(?:-\d+)?\s*\*?\s+(?<path>\S.*)$')
            if (-not $match.Success) { continue }
            if ([int]$match.Groups[1].Value -ne 3) { continue }
            $minor = [int]$match.Groups[2].Value
            $path = $match.Groups['path'].Value.Trim()
            if ($minor -ge $MinMinor -and $minor -le $MaxMinor -and (Test-Path $path)) {
                $candidates += [pscustomobject]@{ Minor = $minor; Path = $path }
            }
        }
    }

    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $onPath) {
        $minor = Get-InterpreterMinor $onPath.Source
        if ($null -ne $minor -and $minor -ge $MinMinor -and $minor -le $MaxMinor) {
            $candidates += [pscustomobject]@{ Minor = $minor; Path = $onPath.Source }
        }
    }

    if ($candidates.Count -eq 0) { return $null }
    return ($candidates | Sort-Object -Property Minor -Descending | Select-Object -First 1).Path
}

function Request-PythonInstall {
    <# No supported interpreter. Offer to install one rather than dead-ending. #>
    Write-Host ""
    Write-Host "Python 3.$MinMinor-3.$MaxMinor is required and was not found." -ForegroundColor Red
    Write-Host ""

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -ne $winget) {
        Write-Host "  It can be installed for your user account (no administrator rights needed)."
        $answer = Read-Host "  Install Python 3.12 now? [y/N]"
        if ($answer -match '^(y|yes)$') {
            Write-Info "Running winget. This takes a couple of minutes."
            & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            # winget does not refresh this process's PATH, but the installer registers the
            # interpreter with the py launcher, so re-probing usually succeeds.
            $found = Find-Python
            if ($null -ne $found) { return $found }
            Write-Host ""
            Write-Host "  Python was installed but is not visible to this window yet." -ForegroundColor Yellow
            Write-Host "  Close this window and run Start.cmd again." -ForegroundColor Yellow
            return $null
        }
    }

    Write-Host ""
    Write-Host "  Install Python from https://www.python.org/downloads/" -ForegroundColor Cyan
    Write-Host "  Tick 'Add python.exe to PATH' in the installer, then run Start.cmd again."
    return $null
}

# ---------------------------------------------------------------------- stamp

function Get-SetupSignature {
    <# Changes when anything that would require re-running setup changes. #>
    $hash = (Get-FileHash -Algorithm SHA256 (Join-Path $PSScriptRoot "requirements.txt")).Hash
    $model = if ($Small) { "sm" } else { "lg" }
    return "req=$hash;model=$model"
}

function Read-Stamp {
    if (-not (Test-Path $StampPath)) { return $null }
    $stamp = @{}
    foreach ($line in (Get-Content $StampPath)) {
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) { $stamp[$parts[0].Trim()] = $parts[1].Trim() }
    }
    return $stamp
}

function Write-Stamp {
    param([string]$Signature, [string]$TrayAnswer)
    $lines = @("signature=$Signature")
    if ($TrayAnswer) { $lines += "tray=$TrayAnswer" }
    Set-Content -Path $StampPath -Value $lines -Encoding utf8
}

# ------------------------------------------------------------------ tray app

function Get-ExpectedHash {
    <# Pull the asset's line out of the release's SHA256SUMS. Format: "<hash>  <name>". #>
    param([string]$BaseUrl, [string]$Asset)
    try {
        $sums = (Invoke-WebRequest -Uri "$BaseUrl/SHA256SUMS" -UseBasicParsing -TimeoutSec 30).Content
    } catch {
        return $null
    }
    foreach ($line in ($sums -split "`n")) {
        $parts = $line.Trim() -split '\s+', 2
        if ($parts.Count -eq 2 -and $parts[1].Trim() -eq $Asset) { return $parts[0].Trim().ToLower() }
    }
    return $null
}

function Install-TrayApp {
    <# Download the published tray binary. Never throws -- the tray is optional and must
       not stop the web app from starting. #>
    $asset = "PromptRedactionTray-win-x64.exe"
    $baseUrl = "https://github.com/$Repo/releases/latest/download"
    $distDir = Join-Path $PSScriptRoot "dist"
    $target = Join-Path $distDir $asset

    try {
        if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

        Write-Step "Downloading the desktop app..."
        Write-Info "$asset (~97 MB)"
        $temp = "$target.partial"
        Invoke-WebRequest -Uri "$baseUrl/$asset" -OutFile $temp -UseBasicParsing -TimeoutSec 600

        # These binaries are not code-signed, so the checksum is the only integrity check
        # available. The release notes tell users to do exactly this by hand.
        $expected = Get-ExpectedHash -BaseUrl $baseUrl -Asset $asset
        if ($null -eq $expected) {
            Write-Host "      Could not fetch SHA256SUMS -- skipping the desktop app." -ForegroundColor Yellow
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
            return $false
        }

        $actual = (Get-FileHash -Algorithm SHA256 $temp).Hash.ToLower()
        if ($actual -ne $expected) {
            Write-Host "      Checksum mismatch -- the download was discarded." -ForegroundColor Red
            Write-Info "expected $expected"
            Write-Info "got      $actual"
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
            return $false
        }

        Move-Item -Path $temp -Destination $target -Force
        Write-Info "Checksum verified."
        Write-Host ""
        Write-Host "  Desktop app installed:" -ForegroundColor Green
        Write-Host "    $target"
        Write-Info "Windows warns on first run (the binary is not code-signed):"
        Write-Info "choose 'More info' then 'Run anyway'."
        Write-Info "Ctrl+Alt+R redacts the clipboard, Ctrl+Alt+U restores it."
        Write-Info "Start-with-Windows is a checkbox in its own Settings window."
        if ($Port -ne 8000) {
            Write-Host "    It defaults to port 8000 -- set http://127.0.0.1:$Port in its Settings." -ForegroundColor Yellow
        }
        return $true
    } catch {
        $status = $null
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 404) {
            Write-Host "      No published release found yet -- skipping the desktop app." -ForegroundColor Yellow
        } else {
            Write-Host "      Could not download the desktop app: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        Remove-Item "$target.partial" -Force -ErrorAction SilentlyContinue
        return $false
    }
}

function Resolve-TrayChoice {
    <# Returns "yes", "no", or "skip" (not asked -- ask again next time). #>
    if ($NoTray) { return "no" }
    if ($Tray) { return "yes" }

    $interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
    if (-not $interactive) { return "skip" }

    Write-Host ""
    Write-Host "  Optional: the desktop app" -ForegroundColor Cyan
    Write-Host "  It adds a global hotkey (Ctrl+Alt+R) that redacts whatever you have"
    Write-Host "  copied, from any application -- not just this browser tab. ~97 MB."
    $answer = Read-Host "  Install it too? [y/N]"
    if ($answer -match '^(y|yes)$') { return "yes" }
    return "no"
}

# ----------------------------------------------------------------------- run

Write-Host ""
Write-Host "Prompt Redaction Studio" -ForegroundColor Cyan
Write-Host "=======================" -ForegroundColor Cyan

$signature = Get-SetupSignature
$stamp = Read-Stamp
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$needSetup = (-not (Test-Path $venvPython)) -or ($null -eq $stamp) -or ($stamp["signature"] -ne $signature)

if ($needSetup) {
    if (-not (Test-Path $venvPython)) {
        $python = Find-Python
        if ($null -eq $python) {
            $python = Request-PythonInstall
            if ($null -eq $python) { exit 1 }
        }
        Write-Step "Creating the virtual environment..."
        Write-Info "Using $python"
        # Create .venv here rather than letting setup.ps1 do it. setup.ps1 reuses an
        # existing .venv, so this is how the interpreter we vetted gets used without
        # changing setup.ps1 -- its own `py -3` could pick an unsupported version.
        & $python -m venv (Join-Path $PSScriptRoot ".venv")
        if (-not (Test-Path $venvPython)) {
            Write-Host "Could not create the virtual environment." -ForegroundColor Red
            exit 1
        }
    }

    Write-Host ""
    Write-Host "First run: downloading Python packages and language models." -ForegroundColor Yellow
    if ($Small) {
        Write-Info "About 700 MB, typically 3-5 minutes."
    } else {
        Write-Info "About 1.5 GB, typically 5-15 minutes."
        Write-Info "Most of that is en_core_web_lg. Re-run with -Small to skip it."
    }
    Write-Info "This happens once. Later runs start in seconds."

    $setupArgs = @{}
    if ($Small) { $setupArgs["SkipLargeModel"] = $true }
    & (Join-Path $PSScriptRoot "setup.ps1") @setupArgs
}

# The tray question is asked once and remembered, so repeat launches stay quiet.
$trayAnswer = if ($null -ne $stamp) { $stamp["tray"] } else { $null }
if ($Tray -or $NoTray -or -not $trayAnswer) {
    $choice = Resolve-TrayChoice
    if ($choice -eq "yes") {
        Install-TrayApp | Out-Null
        $trayAnswer = "yes"
    } elseif ($choice -eq "no") {
        $trayAnswer = "no"
    }
}

Write-Stamp -Signature $signature -TrayAnswer $trayAnswer

if ($SetupOnly) {
    Write-Host ""
    Write-Host "Setup complete. Run Start.cmd again to launch." -ForegroundColor Green
    Write-Host ""
    exit 0
}

# 127.0.0.1 rather than localhost, deliberately. run.ps1 binds uvicorn to IPv4 only, but
# "localhost" resolves to ::1 first on Windows, where nothing is listening -- an HTTP probe
# against it times out rather than failing fast.
$url = "http://127.0.0.1:$Port"

Write-Host ""
Write-Host "Starting on $url" -ForegroundColor Green
Write-Info "Press Ctrl+C to stop."
Write-Info "The first redaction builds the language model and takes a few seconds."

$browserJob = $null
if (-not $NoBrowser) {
    # Wait for the port to accept connections rather than sleeping a fixed amount: uvicorn
    # binds quickly, but a cold machine can take a while, and opening the browser too early
    # shows a connection error. A TCP connect is the cheapest accurate test of "listening",
    # and unlike Invoke-WebRequest it is unaffected by proxy settings.
    $browserJob = Start-Job -ArgumentList $url, $Port -ScriptBlock {
        param($Url, $ProbePort)
        for ($i = 0; $i -lt 120; $i++) {
            $client = New-Object System.Net.Sockets.TcpClient
            try {
                $client.Connect("127.0.0.1", $ProbePort)
                $client.Close()
                Start-Process $Url
                break
            } catch {
                Start-Sleep -Milliseconds 500
            } finally {
                $client.Dispose()
            }
        }
    }
}

try {
    & (Join-Path $PSScriptRoot "run.ps1") -Port $Port -NoReload
} finally {
    if ($null -ne $browserJob) { Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue }
}
