[CmdletBinding()]
param(
    [switch]$CloseRunningInstances
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$specPath = Join-Path $scriptDir "second_cut.spec"
$distExePath = Join-Path $projectRoot "dist\Second Cut.exe"

function Get-LockingSecondCutProcesses {
    if (-not (Test-Path -LiteralPath $distExePath)) {
        return @()
    }

    Get-Process |
        Where-Object { $_.Path -eq $distExePath }
}

$lockingProcesses = @(Get-LockingSecondCutProcesses)
if ($lockingProcesses.Count -gt 0) {
    if ($CloseRunningInstances) {
        $lockingProcesses | Stop-Process -Force
        Start-Sleep -Milliseconds 500
    }
    else {
        $ids = ($lockingProcesses | Select-Object -ExpandProperty Id) -join ", "
        throw "Cannot build while Second Cut is running from dist. Close process id(s): $ids, or rerun with -CloseRunningInstances."
    }
}

Push-Location $projectRoot
try {
    python -m PyInstaller --clean $specPath
}
finally {
    Pop-Location
}
