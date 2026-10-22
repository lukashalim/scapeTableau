<#
.SYNOPSIS
  Optional local Tabular Editor 2 validation for a generated .pbip folder.

.DESCRIPTION
  Tabular Editor 2 is Windows-only and is NOT bundled into the Electron app or
  Vercel. Install TE2 separately, then point this script at an unzipped PBIP.

  CI: intentionally not wired — TE2 GUI/license weight is too heavy for Actions.
  See docs/third-party-pbip.md.

.PARAMETER PbipDir
  Path to the folder that contains <Name>.pbip and <Name>.SemanticModel.

.PARAMETER TabularEditor
  Path to TabularEditor.exe (defaults to common install locations).
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$PbipDir,

    [string]$TabularEditor = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PbipDir)) {
    Write-Error "PBIP directory not found: $PbipDir"
}

$candidates = @(
    $TabularEditor,
    "$env:LOCALAPPDATA\TabularEditor\TabularEditor.exe",
    "$env:ProgramFiles\Tabular Editor\TabularEditor.exe",
    "${env:ProgramFiles(x86)}\Tabular Editor\TabularEditor.exe"
) | Where-Object { $_ -and (Test-Path $_) }

if (-not $candidates) {
    Write-Host @"
Tabular Editor 2 not found.
Install from https://github.com/TabularEditor/TabularEditor/releases
then re-run: pwsh scripts/validate_te2.ps1 -PbipDir <unzipped-pbip>
"@
    exit 2
}

$te = $candidates[0]
$database = Get-ChildItem -Path $PbipDir -Recurse -Filter "database.tmdl" | Select-Object -First 1
if (-not $database) {
    Write-Error "No database.tmdl under $PbipDir — unzip the .pbip.zip first."
}
$modelDir = $database.Directory.Parent.FullName

Write-Host "Opening semantic model with Tabular Editor 2:"
Write-Host "  $te"
Write-Host "  $modelDir"
# TE2 can load a folder; headless BestPracticeAnalyzer CLI varies by build —
# open the model for interactive validation when /BPAREPORT is unavailable.
& $te "`"$modelDir`""
exit $LASTEXITCODE
