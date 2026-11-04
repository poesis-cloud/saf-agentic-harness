# Environment adapters

Each supported host environment gets one subfolder here — `vscode-github-copilot-chat/` (VS
Code + Copilot Chat) and `github-copilot-cli/` (the GitHub Copilot app and the Copilot CLI,
which share one agent runtime); Claude Code, Cursor, … follow the same shape. The harness core stays env-agnostic — it never
hardcodes a host's event names, tool names, or payload keys; every host-specific detail is
declared in that adapter's own files. Canonical harness-core documentation lives in
`../def/core/spec.md`; each adapter's own specification lives in `../def/adapter/<host>/spec.md`.

## Shared dispatch script

`dispatch.sh` is the ONE generic hook entry point, shared by every adapter — it is not
host-specific and must never be duplicated per environment. It takes the event name, the
environment id, and optionally the scoping agent slug (agent-scoped hooks) as its arguments,
and forwards the event payload (JSON on stdin) unchanged to the adapter's own hook entry
(`adapters/<env>/adapter.py`). The adapter classifies the event, invokes the harness's pure
function commands, and answers the host with **structured JSON on stdout, exit 0**
(`permissionDecision` / `decision: block` / `additionalContext`); exit 2 is only the
hard-failure fallback. No adapter edits an agent's tool input (`updatedInput` /
`modifiedArgs`): an agent's harness request is run by the hook itself and answered. Each
adapter's `hooks.yaml` calls it with its own env id — nothing in the script itself varies
per host.

```bash
adapters/dispatch.sh <event> <env> [<agent>]
```

**Exception — hosts that run on Windows without bash.** `github-copilot-cli` does not go through
`dispatch.sh`: the Copilot runtime can launch a program directly (`exec` + `args`), so each
rendered hook runs `<venv python> adapters/github-copilot-cli/adapter.py hook --event <event>
[--agent <slug>] --framework-dir <fw>` with an explicit interpreter and the `FRAMEWORK_*` layout
in the hook's own `env` — the same contract (payload on stdin, one JSON decision on stdout,
exit 0), no shell in between. Its installer is `adapters/github-copilot-cli/install.py`.

## Per-adapter layout

```text
saf-agentic-harness/
  adapters/
    dispatch.sh          # shared, generic — every adapter calls this; nothing host-specific inside
    <env>/
      adapter.py          # the adapter's hook entry: classification, session tracking, rendering
      hooks.yaml          # the host hook registration (YAML source of truth; rendered to the host's own hook config)
      tools.yaml          # host tool names, write verbs, payload keys (host-specific, never in the core)
      models.yaml         # model profile slug -> host model id binding
      contracts/          # the adapter's own seam contracts (hook-stdin / hook-stdout)
      README.md           # adapter-specific notes
```

## Adding a new host

1. Create `adapters/<new-env>/hooks.yaml` — register the host's lifecycle events, each entry
   calling `adapters/dispatch.sh <event> <new-env>` (plus the scoping agent slug for
   agent-scoped entries, where the host supports them) — or, for a host that must run without
   bash, the adapter entry through an explicit interpreter (see `github-copilot-cli/`).
2. Create `adapters/<new-env>/tools.yaml` — declare that host's tool names, write verbs, and
   payload keys (see `vscode-github-copilot-chat/tools.yaml` for the shape; validated against
   `contracts/conf/adapters/tools.conf.schema.json`).
3. Implement `adapters/<new-env>/adapter.py` — the event→boundary classification, session
   identification, and decision rendering for that host (see
   `../def/adapter/vscode-github-copilot-chat/spec.md` and
   `../def/adapter/github-copilot-cli/spec.md` with their class diagrams for the reference
   designs). Its only dependency into the core is the twelve function commands.
4. No change to `dispatch.sh` or the harness core — the new adapter folder is the only new
   surface.
