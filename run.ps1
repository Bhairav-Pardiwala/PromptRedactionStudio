<#
.SYNOPSIS
    Starts Prompt Redaction Studio on http://localhost:8000

.PARAMETER Port
    Port to listen on. Defaults to 8000.

.PARAMETER NoReload
    Disables auto-reload on file changes.
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No virtual environment found. Run .\setup.ps1 first."
}

Write-Host ""
Write-Host "Prompt Redaction Studio" -ForegroundColor Cyan
Write-Host "  http://localhost:$Port" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  The first request builds the spaCy model and takes a few seconds." -ForegroundColor DarkGray
Write-Host ""

$arguments = @("-m", "uvicorn", "app.main:app", "--port", "$Port", "--host", "127.0.0.1")
if (-not $NoReload) { $arguments += "--reload" }

& $python $arguments
