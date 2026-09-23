param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectPath,

  [switch]$Json
)

$ErrorActionPreference = "Stop"

if (-not [System.IO.Path]::IsPathRooted($ProjectPath)) {
  $ProjectPath = Join-Path (Get-Location) $ProjectPath
}

$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)
$CadDir = Join-Path $ProjectPath "input\cad"
$ReferenceDir = Join-Path $ProjectPath "input\references"
$RequirementsDir = Join-Path $ProjectPath "requirements"

$scanRoot = if (Test-Path -LiteralPath $CadDir) { $CadDir } else { $ProjectPath }
$sources = @(Get-ChildItem -LiteralPath $scanRoot -File | Where-Object { $_.Extension -in @('.dwg', '.dxf') })
$dxf = @($sources | Where-Object Extension -eq '.dxf')
$dwg = @($sources | Where-Object Extension -eq '.dwg')

$result = [ordered]@{
  project_path = $ProjectPath
  status = if ($dxf.Count) { "ready_for_preflight" } elseif ($dwg.Count) { "needs_dxf_export" } else { "waiting_for_materials" }
  approved_scan_roots = @($scanRoot, $ReferenceDir, $RequirementsDir)
  dxf_files = @($dxf.FullName)
  dwg_files = @($dwg.FullName)
  next_step = if ($dxf.Count) {
    "Confirm the intended source set, then preflight the listed DXF files. Drawing roles are determined from content, not filenames."
  } else {
    "Obtain a DXF export of the approved CAD. Any filename is accepted; never convert or overwrite the original silently."
  }
}

if ($Json) {
  $result | ConvertTo-Json -Depth 4
} else {
  Write-Output "Status: $($result.status)"
  Write-Output "DXF files: $($result.dxf_files -join ', ')"
  Write-Output "DWG files: $($result.dwg_files -join ', ')"
  Write-Output "Approved scan roots:"
  $result.approved_scan_roots | ForEach-Object { Write-Output "  $_" }
  Write-Output "Next step: $($result.next_step)"
}
