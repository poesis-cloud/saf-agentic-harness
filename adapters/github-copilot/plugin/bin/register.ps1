# The harness plugin's sessionStart registration (adapter spec, Plugin mode: The registry).
# PowerShell only, never Python: it records this harness version's root and data folder,
# installs the stable launcher, and names this version the latest. Every write is a rename.
$root = if ($env:PLUGIN_ROOT) { $env:PLUGIN_ROOT } else { Split-Path -Parent $PSScriptRoot }
$null = [Console]::In.ReadToEnd()
$registry = if ($env:SAF_AGENTIC_REGISTRY_DIR) { $env:SAF_AGENTIC_REGISTRY_DIR } else { Join-Path $env:LOCALAPPDATA 'saf-agentic\registry' }
$encoding = New-Object System.Text.UTF8Encoding $false

function Write-Entry([string]$Path, [string]$Value) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $temporary = "$Path.tmp.$PID"
    [IO.File]::WriteAllText($temporary, "$Value`n", $encoding)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Install-Copy([string]$Source, [string]$Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $temporary = "$Path.tmp.$PID"
    Copy-Item -LiteralPath $Source -Destination $temporary -Force
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

try {
    $version = (Get-Content -LiteralPath (Join-Path $root 'VERSION') -TotalCount 1).Trim()
    if (-not $version) { throw 'VERSION is empty' }
    Write-Entry (Join-Path $registry "harness\$version\root") $root
    Write-Entry (Join-Path $registry "harness\$version\data") ([string]$env:PLUGIN_DATA)
    Install-Copy (Join-Path $root 'bin\launcher.sh') (Join-Path $registry 'harness\launcher.sh')
    Install-Copy (Join-Path $root 'bin\launcher.ps1') (Join-Path $registry 'harness\launcher.ps1')
    Write-Entry (Join-Path $registry 'harness\latest') $version
    exit 0
} catch {
    [Console]::Error.WriteLine("[saf-agentic-harness] the SAF registry $registry cannot be written: grant it to the session, or set SAF_AGENTIC_REGISTRY_DIR ($_)")
    exit 1
}
