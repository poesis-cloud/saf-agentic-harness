# Environment adapters

Each supported host environment gets one subfolder here (`github-copilot/` today; Claude Code,
Cursor, … follow the same shape). The harness core stays env-agnostic — it never hardcodes a
host's event names, tool names, or payload keys; every host-specific detail is declared in that
adapter's own files. Canonical harness documentation lives in `../../def/harness/README.md`.

## Shared dispatch script

`dispatch.sh` is the ONE generic hook entry point, shared by every adapter — it is not
host-specific and must never be duplicated per environment. It takes the event name and the
environment id as its two arguments, forwards the event payload (JSON on stdin) unchanged to
`harness.py hook --event <event> --env <env>`, and exits with the harness's decision (exit 2 =
deny/fail). Each adapter's `hooks/map.yaml` calls it with its own env id as the second argument —
nothing in the script itself varies per host.

```bash
harness/adapters/dispatch.sh <event> <env>
```

## Per-adapter layout

```text
harness/
  adapters/
    dispatch.sh          # shared, generic — every adapter calls this; nothing host-specific inside
    <env>/
      hooks/
        map.yaml          # the host hook registration (YAML source of truth; rendered to the host's own hook config)
      tools/
        map.yaml          # host tool names, write verbs, payload keys (host-specific, never in the core)
      README.md           # adapter-specific notes
```

## Adding a new host

1. Create `adapters/<new-env>/hooks/map.yaml` — register the host's lifecycle events, each entry
   calling `../../dispatch.sh <event> <new-env>`.
2. Create `adapters/<new-env>/tools/map.yaml` — declare that host's tool names, write verbs, and
   payload keys (see `github-copilot/tools/map.yaml` for the shape).
3. If the host's lifecycle event names differ from the existing set, add rows to
   `HookService.EVENT_PHASE` mapping them to the normalized workflow phases (`session-open` /
   `observe` / `precondition` / `postcondition` / `session-close`).
4. No change to `dispatch.sh` or the harness core — the new adapter folder is the only new surface.
