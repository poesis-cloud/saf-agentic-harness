#!/bin/sh
# The harness plugin's sessionStart registration (adapter spec, Plugin mode: The registry).
# Shell only, never Python: it records this harness version's root and data folder, installs
# the stable launcher, and names this version the latest. Every write is a rename.
root=${PLUGIN_ROOT:-$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)}
cat >/dev/null 2>&1
registry=${SAF_AGENTIC_REGISTRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/saf-agentic/registry}
version=$(head -n 1 "$root/VERSION" | tr -d '\r\n\t ')

put() {
    mkdir -p "$(dirname -- "$1")" && printf '%s\n' "$2" >"$1.tmp.$$" && mv -f "$1.tmp.$$" "$1"
}
install_copy() {
    mkdir -p "$(dirname -- "$2")" && cp "$1" "$2.tmp.$$" && mv -f "$2.tmp.$$" "$2"
}

if [ -n "$version" ] &&
    put "$registry/harness/$version/root" "$root" &&
    put "$registry/harness/$version/data" "${PLUGIN_DATA:-}" &&
    install_copy "$root/bin/launcher.sh" "$registry/harness/launcher.sh" &&
    install_copy "$root/bin/launcher.ps1" "$registry/harness/launcher.ps1" &&
    put "$registry/harness/latest" "$version"; then
    exit 0
fi
printf '[saf-agentic-harness] the SAF registry %s cannot be written: grant it to the session, or set SAF_AGENTIC_REGISTRY_DIR\n' "$registry" >&2
exit 1
