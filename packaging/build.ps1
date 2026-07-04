$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$specPath = Join-Path $scriptDir "second_cut.spec"

Push-Location $projectRoot
try {
    python -m PyInstaller --clean $specPath
}
finally {
    Pop-Location
}
