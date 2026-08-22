<#
.SYNOPSIS
    Sets up Prompt Redaction Studio: virtualenv, dependencies and spaCy models.

.PARAMETER WithTransformers
    Also installs torch and the transformers NER model (~2 GB extra). Everything else
    works without it; the transformers engine simply shows as unavailable in the UI.

.PARAMETER SkipLargeModel
    Skips en_core_web_lg (~590 MB). The app falls back to the small model, which detects
    noticeably fewer names and places.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -WithTransformers
#>
[CmdletBinding()]
param(
    [switch]$WithTransformers,
    [switch]$SkipLargeModel
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "Prompt Redaction Studio - setup" -ForegroundColor Cyan
Write-Host "===============================" -ForegroundColor Cyan
Write-Host ""

# The `python` on PATH here is Python 3.5 (Anaconda), which Presidio does not support.
# The py launcher gets us a supported interpreter (Presidio needs >=3.10,<3.15).
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($null -eq $launcher) {
    Write-Error "The 'py' launcher was not found. Install Python 3.10-3.14 from python.org."
}

Write-Host "[1/4] Creating virtual environment (.venv)..." -ForegroundColor Yellow
if (Test-Path ".venv") {
    Write-Host "      .venv already exists - reusing it."
} else {
    & py -3 -m venv .venv
    if (-not $?) { Write-Error "Failed to create the virtual environment." }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Error "Could not find $python" }

$version = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "      Using Python $version"

Write-Host ""
Write-Host "[2/4] Installing Python dependencies..." -ForegroundColor Yellow
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements.txt
if (-not $?) { Write-Error "Dependency installation failed." }

Write-Host ""
Write-Host "[3/4] Downloading spaCy models..." -ForegroundColor Yellow
Write-Host "      en_core_web_sm (~12 MB)"
& $python -m spacy download en_core_web_sm
if (-not $?) { Write-Error "Failed to download en_core_web_sm." }

if ($SkipLargeModel) {
    Write-Host "      Skipping en_core_web_lg (-SkipLargeModel)." -ForegroundColor DarkYellow
} else {
    Write-Host "      en_core_web_lg (~590 MB - this is the slow one)"
    & $python -m spacy download en_core_web_lg
    if (-not $?) { Write-Error "Failed to download en_core_web_lg." }
}

Write-Host ""
Write-Host "[4/4] Transformers engine..." -ForegroundColor Yellow
if ($WithTransformers) {
    Write-Host "      Installing torch and transformers (~2 GB)."
    & $python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    if (-not $?) { Write-Error "Failed to install torch." }
    & $python -m pip install "presidio-analyzer[transformers]"
    if (-not $?) { Write-Error "Failed to install the transformers extra." }
    Write-Host "      The NER model downloads on first use (~500 MB)."
} else {
    Write-Host "      Skipped. Re-run with -WithTransformers to enable it."
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Start the app with:  .\run.ps1" -ForegroundColor Green
Write-Host ""
