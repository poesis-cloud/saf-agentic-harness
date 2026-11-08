# Environment adapters

Each supported host environment gets one subfolder here. Today there is one:
`github-copilot/`, the GitHub Copilot agent runtime that the Copilot CLI, the Copilot app and
VS Code (Session Target: Copilot) share. Other hosts would follow the same shape. The harness
core stays host-agnostic: it never hardcodes a host's event names, tool names, or payload
keys; every host-specific detail is declared in that adapter's own files. Canonical
harness-core documentation lives in `../def/core/spec.md`; each adapter's own specification
lives in `../def/adapter/<host>/spec.md`.

An adapter answers the host with **structured JSON on stdout, exit 0** (`permissionDecision`,
`modifiedResult`, `additionalContext`); exit 2 is only the hard-failure fallback. No adapter
edits an agent's tool input: an agent's harness request is run by the hook itself and
answered.

`github-copilot/` reaches its hook entry in two ways:

- **Plugin mode:** the rendered harness plugin's hooks are shell strings that run
  `bin/saf-hook.sh` or `bin/saf-hook.ps1` through `PLUGIN_ROOT`. The shim finds Python and runs
  the stdlib-only `launcher.py`, which runs `adapter.py` on the provisioned interpreter
  (`render_plugin.py`, `make plugin`).
- **Developer install:** each rendered hook runs
  `<venv python> adapters/github-copilot/adapter.py hook --event <event> [--agent <slug>]
  --framework-dir <fw>` with an explicit interpreter and the `FRAMEWORK_*` layout in the hook's
  own `env` (`install.py`, `make install-hooks`).

`binding_contract.py` is the shared, harness-core-free validation of every adapter's
`tools.yaml` and `models.yaml` against `../contracts/conf/adapters/`.

## Per-adapter layout

```text
saf-agentic-harness/
  adapters/
    binding_contract.py   # shared binding validation; nothing host-specific inside
    <env>/
      adapter.py          # the adapter's hook entry: classification, session tracking, rendering
      hooks.yaml          # the host hook registration (YAML source of truth; rendered to the host's own hook config)
      tools.yaml          # host tool names, write verbs, payload keys (host-specific, never in the core)
      models.yaml         # model profile slug -> host model id binding
      contracts/          # the adapter's own seam contracts (hook-stdin / hook-stdout)
      README.md           # adapter-specific notes
```

## Adding a new host

1. Create `adapters/<new-env>/hooks.yaml`: register the host's lifecycle events, each entry
   reaching the adapter entry through an explicit interpreter.
2. Create `adapters/<new-env>/tools.yaml`: declare that host's tool names, write verbs, and
   payload keys (see `github-copilot/tools.yaml` for the shape; validated against
   `contracts/conf/adapters/tools.conf.schema.json`).
3. Implement `adapters/<new-env>/adapter.py`: the event→boundary classification, session
   identification, and decision rendering for that host (see
   `../def/adapter/github-copilot/spec.md` and its class diagram for the reference design).
   Its only dependency into the core is the twelve function commands.
4. No change to the harness core: the new adapter folder is the only new surface.
