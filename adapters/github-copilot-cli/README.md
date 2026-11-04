# Adapter — `github-copilot-cli`

This adapter binds the harness to the **GitHub Copilot CLI agent runtime**. The **GitHub
Copilot app** embeds the same runtime, so the adapter covers the app (Windows, macOS, Linux)
and the `copilot` CLI alike. The full specification, including the hook → boundary table and
the limits of this runtime, is
[`def/adapter/github-copilot-cli/spec.md`](../../def/adapter/github-copilot-cli/spec.md).

```text
adapters/github-copilot-cli/
  adapter.py            hook entry:  <python> adapter.py hook --event <event> [--agent <slug>] --framework-dir <fw>
  install.py            renders hooks.yaml + agent-hooks.yaml with absolute machine paths
  hooks.yaml            global hooks (source of truth, NOT installable as-is)
  agent-hooks.yaml      orchestrator-scoped H0 hooks (rendered into agent frontmatter)
  tools.yaml            tool names + payload keys (validated by contracts/conf/adapters/tools.conf.schema.json)
  models.yaml           canonical model slug -> host model id
  contracts/            hook-stdin / hook-stdout JSON Schemas
  copilot_cli_adapter/  the adapter package
```

## Prerequisites

- **Python 3.10 or newer**, in a virtual environment that holds `requirements.txt`. The
  installer records that interpreter's absolute path in every hook, so hooks never depend on
  `PATH`, on bash, or on a `python` alias.
- A **framework checkout**, e.g. [`saf-agentic-organization`](https://github.com/poesis-cloud/saf-agentic-organization).
  It must contain `.env`, `workflows/`, `instructions/`, `skills/` and `workspace/`.
- The framework's **Copilot plugin bundle**, rendered by the framework itself. For
  `saf-agentic-organization`, that is `builds/github-copilot-cli/`. The bundle carries the
  agents and skills; this installer adds the hooks to it.
- The **framework folder trusted** in the app. Repository hooks and agent-scoped hooks do not
  run in an untrusted folder, and they fail silently. Without them no harness session opens.

## Install — Windows (PowerShell)

```powershell
# 1. the harness and its venv
git clone https://github.com/poesis-cloud/saf-agentic-harness C:\saf\saf-agentic-harness
cd C:\saf\saf-agentic-harness
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2. render the framework's bundle first (in the framework checkout; see its README)

# 3. add the hooks to the bundle — run with the VENV interpreter: it is the one recorded
.venv\Scripts\python adapters\github-copilot-cli\install.py `
    --framework-dir C:\saf\saf-agentic-organization `
    --bundle-dir    <the rendered bundle> `
    --workspace-dir C:\saf\saf-agentic-workspace      # optional: a workspace outside the checkout
```

## Install — Linux / macOS

```bash
git clone https://github.com/poesis-cloud/saf-agentic-harness ~/saf/saf-agentic-harness
cd ~/saf/saf-agentic-harness
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python adapters/github-copilot-cli/install.py \
    --framework-dir ~/saf/saf-agentic-organization \
    --bundle-dir <the rendered bundle> \
    [--workspace-dir ~/saf/saf-agentic-workspace]
# or, the same through make (uses the python3 on PATH — activate the venv first):
make install-hooks-copilot-cli FRAMEWORK_DIR=… BUNDLE_DIR=… [WORKSPACE_DIR=…]
```

## What the installer writes

- **`<bundle>/hooks.json`** holds the global hooks. If the bundle's `plugin.json` names a
  `hooks` file path, the installer writes there instead; an inline `hooks` object is refused.
  Every entry is `exec: <venv python>`, `args: [adapter.py, hook, --event, …,
  --framework-dir, <fw>]`, with `cwd: <fw>` and an `env` holding `FRAMEWORK_DIR` plus every
  `FRAMEWORK_*_DIR` as resolved from `<fw>/.env` and `--workspace-dir`. **No user-level
  variables (`setx`) are needed:** hooks get their layout from `env`, and agents get absolute
  paths from the injected hint (see below).
- **A managed `hooks:` block** in the frontmatter of every orchestrator's agent file
  (`<bundle>/agents/<slug>.agent.md`). The orchestrators are the agents that
  `workflows/**/*.workflow.conf.yaml` names as `orchestrator:`. The block holds the scoped
  `userPromptSubmitted`/`userPromptTransformed` hooks that open the harness session (H0).
  Re-running the installer replaces the block. An agent with its own `hooks:` key is refused.

**`--framework-dir` is the framework CHECKOUT, not the rendered bundle.** It is where `.env`,
`workflows/`, `instructions/` and `skills/` live. The bundle is only the package the app
loads.

Other targets for the global hook file:

- `--hooks-target repo --target-repo <repo>` writes `<repo>/.github/hooks/safe-harness-copilot-cli.json`.
  This repository git-ignores that path; ignore it in the target repository too, because it
  holds machine paths.
- `--hooks-target user` writes `~/.copilot/hooks/` (or `$COPILOT_HOME/hooks/`).

Other options: `--hook-style shell` writes `powershell`/`bash` command strings instead of
`exec`. `--dry-run` prints what would be written. Everything the installer writes holds
absolute machine paths: **generated, never committed**. Re-run the installer after moving
either checkout or recreating the venv.

## Using it in the GitHub Copilot app

1. **Load the bundle through a local marketplace**:
   `copilot plugin marketplace add <bundle marketplace dir>`, then install the plugin from it.
   A path-sourced plugin in a local marketplace is read **live**, so re-running `install.py`
   takes effect at the next session. A direct `copilot plugin install <path>` **copies** the
   bundle instead; after every re-run of `install.py` you must then run
   `copilot plugin update <plugin>`, or the app keeps the old copy without hooks.
2. **Trust the framework folder** in the app, and open the session in it.
3. **Select an orchestrator agent** (e.g. the Release Train Engineer) and give it its task.
   Its scoped hook opens the harness session. Every session also receives a short context
   from `sessionStart` naming the framework root, the workspace, and the exact harness command
   lines.
4. The orchestrator runs `& '<venv python>' '<harness.py>' resolve-step --workflow-slug <slug>`
   (or `… resolve-step-model`) with its shell tool. It may write `python`, `python3`, `py -3`
   or a full interpreter path. The adapter accepts exactly one plain invocation of the
   installed harness, without `--session-id`, and rewrites it into the canonical, attributed
   command. Everything else that mentions `harness.py` is denied.
5. Steps are dispatched with `task`, synchronously; `mode: background` is denied. Writes go
   through `create`/`edit`, which the harness authorizes (H3) and validates (H5). `skill`,
   `ask_user` (the human gate), `view`, `grep` and `glob` are never gated.

Session records live in `%LOCALAPPDATA%\saf-agentic-harness\github-copilot-cli`, or
`$XDG_STATE_HOME/saf-agentic-harness/github-copilot-cli`. `SAF_HARNESS_STATE_DIR` overrides
both. Hook diagnostics go to stderr.

## Known limitations

These are detailed, with their fallbacks, in the spec's *Limited or unsupported boundaries*.

- **Folder trust.** In an untrusted folder, scoped hooks never run, so no harness session
  opens. Harness commands are then denied with a diagnosis.
- **Timeouts fail open** in this runtime. The gating hooks have a 120 s budget.
- **Shell writes are not mediated.** The adapter denies shell commands that name the
  workspace or `logs/`, but a determined command can evade a textual check. The core's
  commit-set check is the backstop. Agents must write through `create`/`edit`.
- **Batching.** A harness command must be the only tool call in its batch.
- **Latency.** Plugin hooks start Python for every tool call in every folder. Ungoverned
  sessions return after one state lookup.
- **Not yet byte-verified in the app:** `exec` entries (fall back to `--hook-style shell`),
  `modifiedArgs` without `permissionDecision`, and scoped hooks inside subagents.
