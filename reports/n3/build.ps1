param(
    [string]$TectonicExe = $env:TECTONIC_EXE
)

$ErrorActionPreference = "Stop"
$ReportDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildDirectory = Join-Path $ReportDirectory "build"
$MainFile = Join-Path $ReportDirectory "main.tex"
$FinalPdf = Join-Path $ReportDirectory "relatorio_n3_redes2.pdf"

if ([string]::IsNullOrWhiteSpace($TectonicExe)) {
    $TectonicCommand = Get-Command tectonic -ErrorAction SilentlyContinue
    if (-not $TectonicCommand) {
        throw "Tectonic não encontrado. Defina TECTONIC_EXE ou passe -TectonicExe."
    }
    $TectonicExe = $TectonicCommand.Source
}

if (-not (Test-Path -LiteralPath $TectonicExe -PathType Leaf)) {
    throw "Executável Tectonic inválido: $TectonicExe"
}

New-Item -ItemType Directory -Path $BuildDirectory -Force | Out-Null
Push-Location $ReportDirectory
try {
    & $TectonicExe --keep-logs --keep-intermediates --outdir $BuildDirectory $MainFile
    if ($LASTEXITCODE -ne 0) {
        throw "A compilação Tectonic falhou com código $LASTEXITCODE."
    }
    $BuiltPdf = Join-Path $BuildDirectory "main.pdf"
    if (-not (Test-Path -LiteralPath $BuiltPdf -PathType Leaf)) {
        throw "O compilador não produziu main.pdf."
    }
    Copy-Item -LiteralPath $BuiltPdf -Destination $FinalPdf -Force
}
finally {
    Pop-Location
}

Write-Host "[OK] PDF: $FinalPdf"
