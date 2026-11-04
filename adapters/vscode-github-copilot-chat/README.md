# VS Code GitHub Copilot Chat adapter

The host binding for VS Code with the GitHub Copilot Chat extension
(`microsoft/vscode-copilot-chat`). Its canonical specification is
[`def/adapter/vscode-github-copilot-chat/spec.md`](../../def/adapter/vscode-github-copilot-chat/spec.md):
the hook-by-hook contract (H0–H7), session identity binding, invocation plumbing, and the
inconsistencies it surfaced against the core spec (I1–I15). The harness core is specified in
[`def/core/spec.md`](../../def/core/spec.md); the adapters-folder overview is
[`../README.md`](../README.md).

## Contents

- `adapter.py` — the hook entry `../dispatch.sh` runs; composes the binding, classifier,
  session tracker, harness command runner and renderer.
- `hooks.yaml` — workspace hook registration source (H1–H7), rendered to
  `.github/hooks/safe-harness.json` in the framework workspace.
- `agent-hooks.yaml` — the session-started boundary (H0): an agent-scoped `UserPromptSubmit`
  hook rendered into each framework orchestrator's `.agent.md` frontmatter.
- `settings.yaml` — the workspace settings merged into `.vscode/settings.json`.
- `tools.yaml` / `models.yaml` — host tool names, write verbs, payload keys, and model ids;
  validated at load against
  [`contracts/conf/adapters/*.schema.json`](../../contracts/conf/adapters/) (fail fast).
- `contracts/hook-stdin.schema.json` / `contracts/hook-stdout.schema.json` — the normative
  host I/O contracts.

## Prerequisites

- VS Code with GitHub Copilot Chat, agent hooks enabled (a preview feature).
- Python 3.10–3.12 with this repo's `requirements.txt` (PyYAML, jsonschema, cel-python)
  installed — preferably in a virtualenv. The hooks run under exactly this interpreter.
- `bash` (for `../dispatch.sh`) and `git` (function 9 commits artifacts).
- A framework checkout with its `.env` layout (`FRAMEWORK_*_DIR` keys) and workflow catalog
  (`conf/workflows/*.workflow.conf.yaml` naming each `orchestrator:`).

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1. in the framework checkout: render the host bundle the agents ship in
python3 builds/<host>/render_bundle.py . "$BUNDLE"

# 2. in this harness checkout: inject the hooks on top of that bundle
make install-hooks FRAMEWORK_DIR=<framework root> BUNDLE_DIR="$BUNDLE" \
  [HARNESS_PYTHON=/abs/path/to/python]
```

`make install-hooks` renders all three targets in one all-or-nothing run (nothing is written
on any refusal):

1. `<framework>/.github/hooks/safe-harness.json` — the workspace hooks file;
2. the H0 block in each orchestrator agent under `$BUNDLE/agents`;
3. `chat.useHooks` and `chat.hookFilesLocations` merged into
   `<framework>/.vscode/settings.json`.

The rendered files are machine-specific (absolute paths) and never committed. Re-run the
target whenever either checkout or the virtualenv moves.

## Environment

Every rendered hook entry records its own bootstrap in `env`: `FRAMEWORK_DIR` (the framework
root) and `SAF_HARNESS_PYTHON` (the interpreter, proven at render time to import the
requirements). The host runs hooks with its own environment, not your shell's, so nothing
needs exporting. The adapter reads the layout keys from `FRAMEWORK_DIR/.env`; a variable set
in the process environment wins over the file, as in the core config loader.

A hook that cannot bootstrap — no `FRAMEWORK_DIR`, a missing interpreter or module, a
malformed `.env`, a binding refused by its schema — **fails closed**: it exits 2 with a
diagnostic on stderr, which the host shows to the model and treats as blocking. See the spec,
Bootstrap failure (fail closed).

## Required VS Code settings

Merged by `make install-hooks` (spec — Required VS Code settings):

- `chat.useHooks: true` — master switch; hook files are discovered but not run without it.
- `chat.hookFilesLocations` — must include the hooks file location.
- `chat.useClaudeHooks` — not required (Copilot-format hooks only).

## Known limitations (alpha)

- **I4 — interim attribution exception.** The host has no attribution surface outside
  model-visible tool arguments; H4 stamps the session at the tool boundary
  (`updatedInput`) under the core's narrow, explicitly interim tool-boundary-stamp exception.
- **I13 — preview-setting drift and open host verifications.** Hooks are a VS Code preview
  feature: setting names, events, and payload fields may change between releases. The
  fail-fast binding validation is the drift detector; I13 lists what only live
  experimentation can confirm (e.g. whether steering messages fire `UserPromptSubmit`).
- **The host fails open** on hook timeout, spawn failure, or kill (I6). The adapter closes
  every failure it can observe (exit 2), but cannot close one where the hook never runs.
- **`Stop`/`SubagentStop` bootstrap failures exit 2**, which asks the agent to continue;
  the host's `stop_hook_active` guard bounds the loop. Repair the install to clear it.
- **`apply_patch`**: paths are parsed from the patch headers (`*** Add/Update/Delete File:`,
  `*** Move to:`); a move is authorized as a delete plus a create, and deletes are denied in
  v1 (function 8), so moves of governed artifacts are refused. A declared write whose paths
  cannot be extracted is denied.
