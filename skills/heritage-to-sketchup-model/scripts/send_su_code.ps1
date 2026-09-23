param(
  [string]$Code = "",
  [string]$Script = "",
  [string]$ModelPath = "",
  [int]$TargetPid = 0,
  [int]$TimeoutSeconds = 300,
  [switch]$UserConfirmedBuild
)

$ErrorActionPreference = "Stop"

if (-not $UserConfirmedBuild) {
  throw "SketchUp control is not authorized. Obtain explicit user confirmation to start modeling, then pass -UserConfirmedBuild."
}

$ScriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillRoot = Split-Path -Parent $ScriptsDir
$Workspace = $ScriptsDir
$RegistryDir = Join-Path $Workspace "su_bridge_registry"

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

if ($Script) {
  $Script = (Resolve-InputPath $Script).Replace("\", "/")
  $Code = "load '$Script'"
}

if (-not $Code) {
  throw "Provide -Code or -Script."
}

if (-not (Test-Path -LiteralPath $RegistryDir)) {
  throw "No bridge registry directory found. Run install_su_bridge.ps1 and restart SketchUp."
}

$entries = Get-ChildItem -LiteralPath $RegistryDir -Filter "sketchup_*.json" -ErrorAction SilentlyContinue | ForEach-Object {
  try { Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $null }
} | Where-Object { $_ -and $_.port }

if ($TargetPid) {
  $entries = $entries | Where-Object { [int]$_.pid -eq $TargetPid }
}

if ($ModelPath) {
  $resolvedModel = Resolve-InputPath $ModelPath
  $entries = $entries | Where-Object { $_.model_path -and ([System.IO.Path]::GetFullPath($_.model_path) -eq $resolvedModel) }
}

$entries = @($entries)
if ($entries.Count -ne 1) {
  $summary = $entries | Select-Object pid,port,model_title,model_path | Format-Table -AutoSize | Out-String
  $examples = $entries | ForEach-Object {
    "  -TargetPid $($_.pid)  # $($_.model_title) $($_.model_path)"
  } | Out-String
  throw "Expected exactly one SketchUp bridge target, found $($entries.Count). Select the intended model with -TargetPid or -ModelPath.`n$summary`nTarget examples:`n$examples"
}

$target = $entries[0]
$client = New-Object System.Net.Sockets.TcpClient
$async = $client.BeginConnect("127.0.0.1", [int]$target.port, $null, $null)
if (-not $async.AsyncWaitHandle.WaitOne([TimeSpan]::FromSeconds(5))) {
  $client.Close()
  throw "Timed out connecting to SketchUp bridge on port $($target.port)."
}
$client.EndConnect($async)
$client.ReceiveTimeout = $TimeoutSeconds * 1000
$client.SendTimeout = 10000

$stream = $client.GetStream()
$writer = New-Object System.IO.StreamWriter($stream, [System.Text.Encoding]::ASCII)
$reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::ASCII)
$encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Code))
$writer.WriteLine($encoded)
$writer.Flush()

$line = $reader.ReadLine()
$client.Close()

if (-not $line) {
  throw "No response from SketchUp bridge."
}

$parts = $line -split "`t", 2
$status = $parts[0]
$message = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[1]))

if ($status -ne "ok") {
  throw $message
}

Write-Output $message
