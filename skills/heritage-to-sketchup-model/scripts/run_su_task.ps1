param(
  [Parameter(Mandatory = $true)]
  [string]$TaskScript,

  [string]$ModelPath = "",

  [int]$WaitSeconds = 180,

  [string]$SketchUpVersion = "SketchUp 2024",

  [string]$SketchUpExe = "",

  [switch]$AllowRunningSketchUp,

  [switch]$UserConfirmedBuild,

  [switch]$UserAuthorizedAutoStart
)

$ErrorActionPreference = "Stop"

if (-not $UserConfirmedBuild) {
  throw "Build is not authorized. Obtain explicit user confirmation to start modeling, then pass -UserConfirmedBuild."
}

if (-not $UserAuthorizedAutoStart) {
  throw "Automatic SketchUp startup is not authorized. Ask the user to open SketchUp, or obtain separate authorization and pass -UserAuthorizedAutoStart."
}

$ScriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillRoot = Split-Path -Parent $ScriptsDir
$Workspace = $ScriptsDir
$PluginDir = Join-Path $env:APPDATA "SketchUp\$SketchUpVersion\SketchUp\Plugins"
$LoaderSource = Join-Path $Workspace "su_heritage_task_loader.rb"
$LoaderDest = Join-Path $PluginDir "heritage_su_task_loader.rb"
$LoaderDisabled = "$LoaderDest.disabled"
$ConfigDest = Join-Path $PluginDir "heritage_su_task_config.json"
$StatusPath = Join-Path $Workspace "su_task_status.txt"
$LogPath = Join-Path $Workspace "su_task_log.txt"

if (-not $SketchUpExe) {
  $SketchUpExe = Join-Path "C:\Program Files\SketchUp\$SketchUpVersion" "SketchUp.exe"
}

function Resolve-InputPath {
  param([string]$PathValue)

  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return (Resolve-Path -LiteralPath $PathValue).Path
  }

  $candidates = @(
    (Join-Path (Get-Location) $PathValue),
    (Join-Path $SkillRoot $PathValue),
    (Join-Path $ScriptsDir $PathValue)
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  throw "Input path not found: $PathValue"
}

$runningSketchUp = Get-Process SketchUp -ErrorAction SilentlyContinue
if ($runningSketchUp -and -not $AllowRunningSketchUp) {
  $titles = ($runningSketchUp | Select-Object -ExpandProperty MainWindowTitle) -join "; "
  throw "SketchUp is already running, so startup plugins will not be reloaded reliably. Close/restart the target SketchUp window first, or use the persistent bridge scripts. Open windows: $titles"
}

if (-not (Test-Path -LiteralPath $LoaderSource)) {
  throw "Missing loader script: $LoaderSource"
}

$TaskScript = Resolve-InputPath $TaskScript

if ($ModelPath) {
  $ModelPath = Resolve-InputPath $ModelPath
}

if (-not (Test-Path -LiteralPath $SketchUpExe)) {
  throw "SketchUp executable not found: $SketchUpExe. Use -SketchUpVersion or -SketchUpExe."
}

New-Item -ItemType Directory -Path $PluginDir -Force | Out-Null
Remove-Item -LiteralPath $LoaderDest -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LoaderDisabled -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StatusPath -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $LogPath -ErrorAction SilentlyContinue

$config = [ordered]@{
  workspace = $Workspace
  task_script = $TaskScript
  model_path = $ModelPath
  status_path = $StatusPath
  log_path = $LogPath
  started_at = (Get-Date).ToString("s")
}
$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ConfigDest -Encoding UTF8
Copy-Item -LiteralPath $LoaderSource -Destination $LoaderDest -Force

Start-Process -FilePath $SketchUpExe

Write-Output "Started SketchUp task."
Write-Output "SketchUp: $SketchUpExe"
Write-Output "Task: $TaskScript"
if ($ModelPath) { Write-Output "Model: $ModelPath" }
Write-Output "Waiting for status: $StatusPath"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
  if (Test-Path -LiteralPath $StatusPath) {
    Get-Content -LiteralPath $StatusPath
    exit 0
  }
  Start-Sleep -Seconds 1
}

Write-Error "Timed out waiting for SketchUp task status. Check $LogPath"
exit 2
