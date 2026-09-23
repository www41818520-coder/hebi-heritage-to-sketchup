param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectPath,

  [string]$ProjectName = "",

  [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not [System.IO.Path]::IsPathRooted($ProjectPath)) {
  $ProjectPath = Join-Path (Get-Location) $ProjectPath
}

$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)

if ((Test-Path -LiteralPath $ProjectPath) -and -not $Force) {
  $existing = Get-ChildItem -LiteralPath $ProjectPath -Force -ErrorAction SilentlyContinue
  if ($existing.Count -gt 0) {
    Write-Output "Project folder already exists: $ProjectPath"
  }
}

New-Item -ItemType Directory -Path $ProjectPath -Force | Out-Null

$folders = @(
  "input\cad",
  "input\references",
  "requirements",
  "work\contracts",
  "work\reading",
  "work\topology",
  "output",
  "reports\qa"
)

foreach ($folder in $folders) {
  New-Item -ItemType Directory -Path (Join-Path $ProjectPath $folder) -Force | Out-Null
}

if (-not $ProjectName) {
  $ProjectName = Split-Path -Leaf $ProjectPath
}

$requirementsPath = Join-Path $ProjectPath "requirements\modeling-requirements.md"
if (-not (Test-Path -LiteralPath $requirementsPath)) {
  $requirementsContent = @"
# $ProjectName Modeling Requirements

## Inputs

- CAD files or folder: input/cad/
- Reference PDF/JPG: input/references/
- Units:
- Modeling scope:
- Optional control mass or closed control polylines:

## Delivery Target

- Delivery level: candidate / full_deliverable
- Required drawing scope:
- Explicit exclusions:
- Output folder: output/
- Never overwrite original CAD, references, or SKP files.
"@
  [System.IO.File]::WriteAllText($requirementsPath, $requirementsContent, [System.Text.UTF8Encoding]::new($true))
}

$statePath = Join-Path $ProjectPath "work\workflow-state.json"
if (-not (Test-Path -LiteralPath $statePath)) {
  $stateContent = @"
{
  "schema": "cad_to_sketchup.workflow.2026-08-04",
  "status": "waiting_for_source_index",
  "contracts": {},
  "sketchup_target": {
    "confirmed": false,
    "model_path": ""
  }
}
"@
  [System.IO.File]::WriteAllText($statePath, $stateContent, [System.Text.UTF8Encoding]::new($true))
}

Write-Output "CAD modeling project initialized."
Write-Output "Project: $ProjectPath"
Write-Output "CAD source folder: $(Join-Path $ProjectPath 'input\cad')"
Write-Output "Optional images/PDFs: $(Join-Path $ProjectPath 'input\references')"
Write-Output "Write modeling requirements in: $requirementsPath"
Write-Output "Workflow state: $statePath"
