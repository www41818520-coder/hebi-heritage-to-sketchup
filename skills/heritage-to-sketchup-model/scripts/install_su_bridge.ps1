param(
  [string]$SketchUpVersion = "SketchUp 2024"
)

$ErrorActionPreference = "Stop"

$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir = Join-Path $env:APPDATA "SketchUp\$SketchUpVersion\SketchUp\Plugins"
$RegistryDir = Join-Path $Workspace "su_bridge_registry"
$BridgeSource = Join-Path $Workspace "su_heritage_bridge.rb"
$BridgeDest = Join-Path $PluginDir "su_heritage_bridge.rb"
$ConfigDest = Join-Path $PluginDir "heritage_su_bridge_config.json"

if (-not (Test-Path -LiteralPath $BridgeSource)) {
  throw "Missing bridge source: $BridgeSource"
}

New-Item -ItemType Directory -Path $PluginDir -Force | Out-Null
New-Item -ItemType Directory -Path $RegistryDir -Force | Out-Null

$config = [ordered]@{
  workspace = $Workspace
  registry_dir = $RegistryDir
  log_path = (Join-Path $Workspace "su_bridge.log")
  installed_at = (Get-Date).ToString("s")
}

$config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ConfigDest -Encoding UTF8
Copy-Item -LiteralPath $BridgeSource -Destination $BridgeDest -Force

Write-Output "Installed Codex SketchUp bridge."
Write-Output "Plugin: $BridgeDest"
Write-Output "Registry: $RegistryDir"
Write-Output "Restart SketchUp once so the bridge loads."
