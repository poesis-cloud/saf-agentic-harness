# The harness plugin's hook shim (adapter spec, Plugin mode: Registration and the launcher).
#   saf-hook.ps1 <event> [--framework <name>] [--agent <slug>]   (payload on stdin)
# Finds Python >= 3.10 (SAF_HARNESS_PYTHON, py -3, python3, python) and runs launcher.py on
# the untouched payload. Without an interpreter it decides alone, without Git.
$root = if ($env:PLUGIN_ROOT) { $env:PLUGIN_ROOT } else { Split-Path -Parent $PSScriptRoot }
$launcher = Join-Path $root 'launcher.py'
# A final (not pre-release), GIL-enabled CPython >= 3.10: the interpreters the lock's wheels
# cover.
# No double quote inside: Windows PowerShell 5.1 strips them from native arguments.
$probe = 'import sys, sysconfig; sys.exit(0 if sys.version_info >= (3, 10) and sys.version_info.releaselevel == ''final'' and not sysconfig.get_config_var(''Py_GIL_DISABLED'') else 1)'
$candidates = @()
if ($env:SAF_HARNESS_PYTHON) { $candidates += , @($env:SAF_HARNESS_PYTHON) }
$candidates += , @('py', '-3')
$candidates += , @('python3')
$candidates += , @('python')
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $prefix = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try { & $exe @prefix -c $probe *> $null } catch { continue }
    if ($LASTEXITCODE -ne 0) { continue }
    & $exe @prefix $launcher @args
    exit $LASTEXITCODE
}

$hookEvent = if ($args.Count -gt 0) { $args[0] } else { '' }
$agent = ''
for ($index = 1; $index + 1 -lt $args.Count; $index += 2) {
    if ($args[$index] -eq '--agent') { $agent = $args[$index + 1] }
}
$raw = [Console]::In.ReadToEnd()
$payload = $null
try { $payload = $raw | ConvertFrom-Json } catch { $payload = $null }
$registry = if ($env:SAF_AGENTIC_REGISTRY_DIR) { $env:SAF_AGENTIC_REGISTRY_DIR } else { Join-Path $env:LOCALAPPDATA 'saf-agentic\registry' }
$session = if ($payload -and $payload.sessionId -is [string] -and $payload.sessionId -match '^[A-Za-z0-9][A-Za-z0-9._-]*$') { $payload.sessionId } else { '' }
$remedy = 'Python >= 3.10 was not found: install it, or set SAF_HARNESS_PYTHON, then start a new session'
switch ($hookEvent) {
    { $_ -in 'userPromptSubmitted', 'userPromptTransformed' } {
        if ($agent -and $session) {
            try {
                $intents = Join-Path $registry 'intents'
                New-Item -ItemType Directory -Force -Path $intents | Out-Null
                [IO.File]::WriteAllText((Join-Path $intents $session), "$agent`n")
            } catch { }
        }
        exit 0
    }
    'preToolUse' {
        $governed = $false
        $state = if ($env:SAF_HARNESS_STATE_DIR) { $env:SAF_HARNESS_STATE_DIR } elseif ($env:PLUGIN_DATA) { Join-Path $env:PLUGIN_DATA 'state' } else { '' }
        if ($session) {
            # An orchestrator's intent, or a session the adapter recorded (a worker included).
            if (Test-Path -LiteralPath (Join-Path (Join-Path $registry 'intents') $session) -PathType Leaf) { $governed = $true }
            if ($state -and (Test-Path -LiteralPath (Join-Path $state "github-copilot\sessions\$session.json") -PathType Leaf)) { $governed = $true }
        }
        $folders = @((Get-Location).Path)
        if ($payload -and $payload.cwd) { $folders = @([string]$payload.cwd) + $folders }
        foreach ($start in $folders) {
            $folder = $start
            while (-not $governed -and $folder) {
                if (Test-Path -LiteralPath (Join-Path $folder '.saf\workspace.yaml') -PathType Leaf) { $governed = $true }
                $parent = Split-Path -Parent $folder
                if (-not $parent -or $parent -eq $folder) { break }
                $folder = $parent
            }
        }
        if ($governed) {
            [Console]::Error.WriteLine("SAFe agentic harness: $remedy")
            exit 2
        }
        exit 0
    }
    'sessionStart' {
        [Console]::Error.WriteLine("[saf-agentic-harness] $remedy")
        exit 1
    }
}
exit 0
