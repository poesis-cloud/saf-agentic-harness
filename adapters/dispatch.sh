#!/usr/bin/env bash
# Generic host hook dispatcher — shared by every environment adapter; the thin entry into the
# deterministic harness. Nothing environment-specific lives in this file.
#
# Every host lifecycle hook execs this script with the event name as $1 and the environment id as
# $2; the event payload arrives as JSON on stdin. We forward it unchanged to the harness `hook`
# command, which is the single source of truth. The harness emits the host decision JSON on
# stdout; exit 2 = deny/fail. Each adapter's own hooks/map.yaml supplies its env id as $2 — adding a
# new host never touches this script.
set -euo pipefail

EVENT="${1:?usage: dispatch.sh <event> <env>}"
ENV="${2:?usage: dispatch.sh <event> <env>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$(cd "$HERE/.." && pwd)/harness.py"

exec python3 "$HARNESS" hook --event "$EVENT" --env "$ENV"