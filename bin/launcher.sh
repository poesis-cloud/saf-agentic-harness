#!/bin/sh
# The stable launcher a framework orchestrator's scoped H0 calls (adapter spec, Plugin mode:
# The registry). Every harness version installs this same behavior: run the shim of
# harness/latest with that version's registered root and data folder; launcher.py then
# routes the firing to the version the registry records for the host session.
#
# Hosts do not order the `sessionStart` hooks of two plugins, nor `sessionStart` against the
# first prompt, so the registry may be empty for a moment: the launcher waits for it
# (SAF_AGENTIC_REGISTRY_WAIT_SEC, 8 s by default). If it stays empty, the launcher decides
# alone, in shell: a SAF workspace fails closed; any other folder is told it is not one.
registry=${SAF_AGENTIC_REGISTRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/saf-agentic/registry}
entry() { [ -f "$1" ] && head -n 1 "$1" | tr -d '\r\n'; }
resolve() {
    version=$(entry "$registry/harness/latest")
    root=''
    [ -n "$version" ] && root=$(entry "$registry/harness/$version/root")
}
resolve
limit=${SAF_AGENTIC_REGISTRY_WAIT_SEC:-8}
waited=0
while { [ -z "$root" ] || [ ! -f "$root/bin/saf-hook.sh" ]; } && [ "$waited" -lt "$limit" ]; do
    sleep 1
    waited=$((waited + 1))
    resolve
done
if [ -n "$root" ] && [ -f "$root/bin/saf-hook.sh" ]; then
    PLUGIN_ROOT=$root PLUGIN_DATA=$(entry "$registry/harness/$version/data") exec sh "$root/bin/saf-hook.sh" "$@"
fi

remedy='SAFe agentic harness: the harness plugin is not registered: install or enable it, then start a new session'
event=${1:-}
agent=''
shift 2>/dev/null
while [ $# -ge 2 ]; do
    [ "$1" = --agent ] && agent=$2
    shift 2
done
payload=$(cat | tr -d '\r\n')
# The raw JSON text of the first string member: never decoded, so it can be written back as is.
raw() {
    printf '%s' "$payload" |
        grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\"([^\"\\\\]|\\\\.)*\"" | head -n 1 |
        sed "s/^\"$1\"[[:space:]]*:[[:space:]]*\"\(.*\)\"\$/\1/"
}
session=$(raw sessionId | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9-]/-/g')
cwd=$(raw cwd | sed 's#\\/#/#g; s#\\\\#\\#g')
marked=''
for folder in "$cwd" "$PWD"; do
    while [ -z "$marked" ] && [ -n "$folder" ]; do
        if [ -f "$folder/.saf/workspace.yaml" ]; then marked=1; fi
        parent=$(dirname -- "$folder")
        [ "$parent" = "$folder" ] && break
        folder=$parent
    done
done
if [ -n "$marked" ]; then
    printf '%s\n' "$remedy" >&2
    exit 2
fi
if [ -n "$agent" ] && [ -n "$session" ]; then
    mkdir -p "$registry/intents" 2>/dev/null &&
        printf '%s\n' "$agent" >"$registry/intents/$session" 2>/dev/null
fi
if [ "$event" = userPromptTransformed ]; then
    notice='this folder is not a SAF workspace; ask me to initialise it'
    transformed=$(raw transformedPrompt)
    if [ -n "$transformed" ]; then
        printf '{"modifiedTransformedPrompt":"%s\\n\\n%s"}\n' "$notice" "$transformed"
    else
        printf '{"modifiedTransformedPrompt":"%s"}\n' "$notice"
    fi
fi
exit 0
