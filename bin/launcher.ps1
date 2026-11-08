# The stable launcher a framework orchestrator's scoped H0 calls (adapter spec, Plugin mode:
# The registry). Every harness version installs this same behavior: run the shim of
# harness\latest with that version's registered root and data folder; launcher.py then
# routes the firing to the version the registry records for the host session.
#
# Hosts do not order the `sessionStart` hooks of two plugins, nor `sessionStart` against the
# first prompt, so the registry may be empty for a moment: the launcher waits for it
# (SAF_AGENTIC_REGISTRY_WAIT_SEC, 8 s by default). If it stays empty, the launcher decides
# alone: a SAF workspace fails closed; any other folder is told it is not one.
$registry = if ($env:SAF_AGENTIC_REGISTRY_DIR) { $env:SAF_AGENTIC_REGISTRY_DIR } else { Join-Path $env:LOCALAPPDATA 'saf-agentic\registry' }
function Read-Entry([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return ([string](Get-Content -LiteralPath $Path -TotalCount 1)).Trim() }
    return ''
}
function Find-Shim {
    $script:version = Read-Entry (Join-Path $registry 'harness\latest')
    $root = if ($script:version) { Read-Entry (Join-Path $registry "harness\$($script:version)\root") } else { '' }
    $shim = if ($root) { Join-Path $root 'bin\saf-hook.ps1' } else { '' }
    if ($shim -and (Test-Path -LiteralPath $shim -PathType Leaf)) { return @($root, $shim) }
    return $null
}
$limit = 8
if ($env:SAF_AGENTIC_REGISTRY_WAIT_SEC -match '^\d+(\.\d+)?$') { $limit = [double]$env:SAF_AGENTIC_REGISTRY_WAIT_SEC }
$clock = [Diagnostics.Stopwatch]::StartNew()
$found = Find-Shim
while (-not $found -and $clock.Elapsed.TotalSeconds -lt $limit) {
    Start-Sleep -Milliseconds 500
    $found = Find-Shim
}
if ($found) {
    $env:PLUGIN_ROOT = $found[0]
    $env:PLUGIN_DATA = Read-Entry (Join-Path $registry "harness\$($script:version)\data")
    & powershell -NoProfile -ExecutionPolicy Bypass -File $found[1] @args
    exit $LASTEXITCODE
}

$hookEvent = if ($args.Count -gt 0) { $args[0] } else { '' }
$agent = ''
for ($index = 1; $index + 1 -lt $args.Count; $index += 2) {
    if ($args[$index] -eq '--agent') { $agent = $args[$index + 1] }
}
$payload = $null
try { $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json } catch { $payload = $null }
$session = if ($payload -and $payload.sessionId -is [string] -and $payload.sessionId) { $payload.sessionId.ToLowerInvariant() -replace '[^a-z0-9-]', '-' } else { '' }
$folders = @((Get-Location).Path)
if ($payload -and $payload.cwd) { $folders = @([string]$payload.cwd) + $folders }
$marked = $false
foreach ($start in $folders) {
    $folder = $start
    while (-not $marked -and $folder) {
        if (Test-Path -LiteralPath (Join-Path $folder '.saf\workspace.yaml') -PathType Leaf) { $marked = $true }
        $parent = Split-Path -Parent $folder
        if (-not $parent -or $parent -eq $folder) { break }
        $folder = $parent
    }
}
if ($marked) {
    [Console]::Error.WriteLine('SAFe agentic harness: the harness plugin is not registered: install or enable it, then start a new session')
    exit 2
}
if ($agent -and $session) {
    try {
        $intents = Join-Path $registry 'intents'
        New-Item -ItemType Directory -Force -Path $intents | Out-Null
        [IO.File]::WriteAllText((Join-Path $intents $session), "$agent`n")
    } catch { }
}
if ($hookEvent -eq 'userPromptTransformed') {
    $notice = 'this folder is not a SAF workspace; ask me to initialise it'
    $prompt = if ($payload -and $payload.transformedPrompt -is [string] -and $payload.transformedPrompt) { "$notice`n`n$($payload.transformedPrompt)" } else { $notice }
    # ASCII only: the host's pipe encoding is not ours to know.
    $json = @{ modifiedTransformedPrompt = $prompt } | ConvertTo-Json -Compress
    [Console]::Out.WriteLine([regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value }))
}
exit 0
