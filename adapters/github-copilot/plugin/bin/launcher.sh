#!/bin/sh
# The stable launcher a framework orchestrator's scoped H0 calls (adapter spec, Plugin mode:
# The registry). Every harness version installs this same behavior: run the shim of
# harness/latest with that version's registered root and data folder; launcher.py then
# routes the firing to the version the registry records for the host session.
registry=${SAF_AGENTIC_REGISTRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/saf-agentic/registry}
entry() { [ -f "$1" ] && head -n 1 "$1" | tr -d '\r\n'; }
version=$(entry "$registry/harness/latest")
root=''
[ -n "$version" ] && root=$(entry "$registry/harness/$version/root")
if [ -z "$root" ] || [ ! -f "$root/bin/saf-hook.sh" ]; then
    echo 'SAFe agentic harness: the harness plugin is not registered: install or enable it, then start a new session' >&2
    exit 2
fi
PLUGIN_ROOT=$root PLUGIN_DATA=$(entry "$registry/harness/$version/data") exec sh "$root/bin/saf-hook.sh" "$@"
