#!/bin/sh
# The Claude Code harness plugin's hook shim (adapter spec claude-code, Plugin mode).
#   sh saf-hook.sh <Event>   (payload on stdin)
# Finds a usable Python >= 3.10 (SAF_HARNESS_PYTHON, python3, python) and runs launcher.py on
# the untouched payload. Without an interpreter it decides alone, in shell and without Git:
# Claude Code blocks a tool call only on exit 2, so a governed PreToolUse exits 2.
root=${CLAUDE_PLUGIN_ROOT:-$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)}
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
        exec "$candidate" "$root/launcher.py" "$event"
    fi
done

payload=$(cat)
# The FIRST occurrence of a string member: the host writes its top-level members before the
# tool arguments, whose nested members must never stand in for them.
field() {
    printf '%s' "$payload" | tr -d '\r\n' |
        grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -n 1 |
        sed "s/^\"$1\"[[:space:]]*:[[:space:]]*\"\(.*\)\"\$/\1/"
}
registry=${SAF_AGENTIC_REGISTRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/saf-agentic/registry}
# Named as the adapter names its records: lowercase, [^a-z0-9-] mapped to '-'.
safe() { tr 'A-Z' 'a-z' | sed 's/[^a-z0-9-]/-/g'; }
session=$(field session_id | safe)
agent_id=$(field agent_id | safe)
remedy='Python >= 3.10 was not found: install it, or set SAF_HARNESS_PYTHON, then start a new session'

case $event in
    UserPromptSubmit)
        agent=$(field agent_type)
        framework=${agent%:*}
        if [ -z "$agent_id" ] && [ -n "$session" ] && [ "$framework" != "$agent" ] &&
            [ -f "$registry/hosts/claude-code/frameworks/$framework/root" ]; then
            mkdir -p "$registry/intents" 2>/dev/null &&
                printf '%s\n' "$agent" >"$registry/intents/$session" 2>/dev/null
        fi
        exit 0
        ;;
    PreToolUse)
        governed=''
        state=${SAF_HARNESS_STATE_DIR:-${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/state}}
        if [ -n "$session" ] && [ -f "$registry/intents/$session" ]; then governed=1; fi
        for host in "$agent_id" "$session"; do
            if [ -n "$host" ] && [ -n "$state" ] && [ -f "$state/claude-code/sessions/$host.json" ]; then
                governed=1
            fi
        done
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
    SessionStart)
        printf '{"systemMessage":"[saf-agentic-harness] %s"}\n' "$remedy"
        exit 0
        ;;
esac
exit 0
