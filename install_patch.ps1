$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetRoot = Get-Location

Write-Host "Target project: $TargetRoot"

if (-not (Test-Path (Join-Path $TargetRoot "app.py"))) {
    throw "Jalankan script ini dari root project yang memiliki app.py."
}

New-Item -ItemType Directory -Path (Join-Path $TargetRoot "utils") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $TargetRoot "parsers") -Force | Out-Null

Copy-Item (Join-Path $ProjectRoot "utils\pdf_converter.py") (Join-Path $TargetRoot "utils\pdf_converter.py") -Force
Copy-Item (Join-Path $ProjectRoot "parsers\opendataloader_parser.py") (Join-Path $TargetRoot "parsers\opendataloader_parser.py") -Force

Write-Host "[OK] Replacement files copied."
Write-Host "Apply file_utils_PATCH.py.txt manually to utils/file_utils.py."
Write-Host "Then install: python -m pip install pywin32 Pillow 'opendataloader-pdf[hybrid]'"
Write-Host "Finally run: python -m compileall app.py utils parsers comparison"
