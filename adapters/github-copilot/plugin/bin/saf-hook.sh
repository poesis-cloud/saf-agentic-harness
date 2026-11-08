#!/bin/sh
# The harness plugin's hook shim (adapter spec, Plugin mode: Registration and the launcher).
#   sh saf-hook.sh <event> [--framework <name>] [--agent <slug>]   (payload on stdin)
# Finds a usable Python >= 3.10 (SAF_HARNESS_PYTHON, python3, python) and runs launcher.py on the
# untouched payload. Without an interpreter it decides alone, in shell and without Git.
root=${PLUGIN_ROOT:-$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)}
event=${1:-}

# A final (not pre-release), GIL-enabled CPython >= 3.10: the interpreters the lock's wheels
# cover.
usable() {
    "$1" -c 'import sys, sysconfig; sys.exit(0 if sys.version_info >= (3, 10) and sys.version_info.releaselevel == "final" and not sysconfig.get_config_var("Py_GIL_DISABLED") else 1)' >/dev/null 2>&1
}

for candidate in "${SAF_HARNESS_PYTHON:-}" python3 python; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ] || continue
    if usable "$candidate"; then
        exec "$candidate" "$root/launcher.py" "$@"
    fi
done

payload=$(cat)
field() {
    printf '%s' "$payload" | tr -d '\r\n' |
        sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p"
}
registry=${SAF_AGENTIC_REGISTRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/saf-agentic/registry}
session=$(field sessionId)
case $session in '' | .* | */* | *\\*) session='' ;; esac
remedy='Python >= 3.10 was not found: install it, or set SAF_HARNESS_PYTHON, then start a new session'
agent=''
shift 2>/dev/null
while [ $# -ge 2 ]; do
    [ "$1" = --agent ] && agent=$2
    shift 2
done

case $event in
    userPromptSubmitted | userPromptTransformed)
        if [ -n "$agent" ] && [ -n "$session" ]; then
            mkdir -p "$registry/intents" 2>/dev/null &&
                printf '%s\n' "$agent" >"$registry/intents/$session" 2>/dev/null
        fi
        exit 0
        ;;
    preToolUse)
        governed=''
        state=${SAF_HARNESS_STATE_DIR:-${PLUGIN_DATA:+$PLUGIN_DATA/state}}
        if [ -n "$session" ]; then
            # An orchestrator's intent, or a session the adapter recorded (a worker included).
            if [ -f "$registry/intents/$session" ]; then governed=1; fi
            if [ -n "$state" ] && [ -f "$state/github-copilot/sessions/$session.json" ]; then
                governed=1
            fi
        fi
        # The payload's folder, its JSON escapes decoded where sh can (`\/`, `\\`), and the
        # hook's own folder: the session's, so an undecodable path is still scanned.
        cwd=$(field cwd | sed 's#\\/#/#g; s#\\\\#\\#g')
        for folder in "$cwd" "$PWD"; do
            while [ -z "$governed" ] && [ -n "$folder" ]; do
                if [ -f "$folder/.saf/workspace.yaml" ]; then governed=1; fi
                parent=$(dirname -- "$folder")
                [ "$parent" = "$folder" ] && break
                folder=$parent
            done
        done
        if [ -n "$governed" ]; then
            printf 'SAFe agentic harness: %s\n' "$remedy" >&2
            exit 2
        fi
        exit 0
        ;;
    sessionStart)
        printf '[saf-agentic-harness] %s\n' "$remedy" >&2
        exit 1
        ;;
esac
exit 0
