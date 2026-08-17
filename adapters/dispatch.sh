#!/usr/bin/env bash
# Generic host hook dispatcher — shared by every environment adapter; the thin entry into the
# adapter's own hook handler. Nothing environment-specific lives in this file.
#
# Every host lifecycle hook execs this script with the event name as $1, the environment id as
# $2, and optionally the scoping agent slug as $3 (agent-scoped hooks — H0); the event payload
# arrives as JSON on stdin and is forwarded unchanged to the adapter's hook entry
# (adapters/<env>/adapter.py). The adapter classifies the event, invokes the harness's pure
# function commands, and emits the host decision as STRUCTURED JSON ON STDOUT WITH EXIT 0
# (permissionDecision / decision:block / additionalContext / updatedInput); exit 2 is only the
# hard-failure fallback. Each adapter's own hooks.yaml supplies its env id as $2 — adding a
# new host never touches this script.
set -euo pipefail

EVENT="${1:?usage: dispatch.sh <event> <env> [<agent>]}"
ENV="${2:?usage: dispatch.sh <event> <env> [<agent>]}"
AGENT="${3:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/$ENV/adapter.py"

exec python3 "$ADAPTER" hook --event "$EVENT" ${AGENT:+--agent "$AGENT"}