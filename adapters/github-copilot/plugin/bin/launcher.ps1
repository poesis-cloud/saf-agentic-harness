# The stable launcher a framework orchestrator's scoped H0 calls (adapter spec, Plugin mode:
# The registry). Every harness version installs this same behavior: run the shim of
# harness\latest with that version's registered root and data folder; launcher.py then
# routes the firing to the version the registry records for the host session.
$registry = if ($env:SAF_AGENTIC_REGISTRY_DIR) { $env:SAF_AGENTIC_REGISTRY_DIR } else { Join-Path $env:LOCALAPPDATA 'saf-agentic\registry' }
function Read-Entry([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return ([string](Get-Content -LiteralPath $Path -TotalCount 1)).Trim() }
    return ''
}
$version = Read-Entry (Join-Path $registry 'harness\latest')
$root = if ($version) { Read-Entry (Join-Path $registry "harness\$version\root") } else { '' }
$shim = if ($root) { Join-Path $root 'bin\saf-hook.ps1' } else { '' }
if (-not $shim -or -not (Test-Path -LiteralPath $shim -PathType Leaf)) {
    [Console]::Error.WriteLine('SAFe agentic harness: the harness plugin is not registered: install or enable it, then start a new session')
    exit 2
}
$env:PLUGIN_ROOT = $root
$env:PLUGIN_DATA = Read-Entry (Join-Path $registry "harness\$version\data")
& powershell -NoProfile -ExecutionPolicy Bypass -File $shim @args
exit $LASTEXITCODE
