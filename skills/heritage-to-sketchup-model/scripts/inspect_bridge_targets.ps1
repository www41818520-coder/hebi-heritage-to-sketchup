param(
  [int]$StaleAfterSeconds = 15,
  [switch]$Json
)

$ErrorActionPreference = "Stop"

$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$RegistryDir = Join-Path $Workspace "su_bridge_registry"

if (-not (Test-Path -LiteralPath $RegistryDir)) {
  Write-Output "No bridge registry directory found."
  Write-Output "Next step: run scripts\install_su_bridge.ps1, then restart SketchUp once."
  exit 0
}

$now = Get-Date
$rows = Get-ChildItem -LiteralPath $RegistryDir -Filter "sketchup_*.json" -ErrorAction SilentlyContinue | ForEach-Object {
  try {
    $entry = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
    $updated = $null
    if ($entry.updated_at) {
      $updated = [datetime]::Parse($entry.updated_at)
    }
    $age = if ($updated) { [math]::Round(($now - $updated).TotalSeconds, 1) } else { $null }
    $reachable = $false
    if ($entry.port) {
      $client = New-Object System.Net.Sockets.TcpClient
      $async = $client.BeginConnect("127.0.0.1", [int]$entry.port, $null, $null)
      $reachable = $async.AsyncWaitHandle.WaitOne([TimeSpan]::FromMilliseconds(500))
      if ($reachable) { $client.EndConnect($async) }
      $client.Close()
    }
    [pscustomobject]@{
      pid = [int]$entry.pid
      port = [int]$entry.port
      model_title = $entry.model_title
      model_path = $entry.model_path
      updated_at = $entry.updated_at
      age_seconds = $age
      stale = if ($age -ne $null) { $age -gt $StaleAfterSeconds } else { $true }
      reachable = $reachable
      target_example = "powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\send_su_code.ps1 -UserConfirmedBuild -TargetPid $($entry.pid) -Code `"Sketchup.active_model.title`""
    }
  } catch {
    [pscustomobject]@{
      pid = $null
      port = $null
      model_title = ""
      model_path = ""
      updated_at = ""
      age_seconds = $null
      stale = $true
      reachable = $false
      target_example = ""
    }
  }
}

$rows = @($rows)

if ($Json) {
  $rows | ConvertTo-Json -Depth 4
} else {
  if ($rows.Count -eq 0) {
    Write-Output "No SketchUp bridge targets found. Open SketchUp after installing the bridge."
  } else {
    $rows | Sort-Object pid | Format-Table pid, port, reachable, stale, model_title, model_path, age_seconds -AutoSize
  }
}
