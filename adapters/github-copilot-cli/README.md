# Adapter — `github-copilot-cli`

This adapter binds the harness to the **GitHub Copilot CLI agent runtime**. The **GitHub
Copilot app** embeds the same runtime, so the adapter covers the standalone `copilot` CLI and
the app (Windows, macOS, Linux) alike. Where the two hosts were observed to differ, the
adapter accepts both forms (see the spec's host facts). The full specification, including the hook → boundary table and
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
- With `--hooks-target repo` only: the **folder trusted** (the app's trust, or the CLI's trust
  prompt / `--trust-folder`). Repository hooks silently do not run in an untrusted folder.
  Plugin hooks, including a plugin agent's scoped hooks, run regardless (observed on the
  standalone CLI).
- A **`models.yaml` that binds every profile** of the framework's model catalogue (see
  [Model binding](#model-binding)).

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
# or, the same through make (PYTHON defaults to python3 on PATH — activate the venv first,
# or pass HARNESS_PYTHON=<absolute venv python>):
make install-hooks-copilot-cli FRAMEWORK_DIR=… BUNDLE_DIR=… [WORKSPACE_DIR=…] [HARNESS_PYTHON=…]
```

## What the installer writes

- **`<bundle>/hooks.json`** holds the global hooks. If the bundle's `plugin.json` names a
  `hooks` file path, the installer writes there instead; an inline `hooks` object is refused.
  Every entry is `exec: <venv python>`, `args: [adapter.py, hook, --event, …,
  --framework-dir, <fw>]`, with `cwd: <fw>` and an `env` holding `FRAMEWORK_DIR` plus every
  `FRAMEWORK_*_DIR` as resolved from `<fw>/.env`, the installer's environment and
  `--workspace-dir`, plus
  `SAF_HARNESS_PYTHON` (the recorded interpreter). **No user-level variables (`setx`) are
  needed:** hooks get their layout from `env`, and agents get absolute paths from the
  injected hint (see below).
- The installer **refuses** an interpreter (`--python`, default: the one running it) that
  cannot import the harness requirements, and a malformed `<fw>/.env`. Nothing is written
  then.
- **A managed `hooks:` block** in the frontmatter of every orchestrator's agent file
  (`<bundle>/agents/<slug>.agent.md`). The orchestrators are the agents that
  `workflows/**/*.workflow.conf.yaml` names as `orchestrator:`. The block holds the scoped
  `userPromptSubmitted`/`userPromptTransformed` hooks that open the harness session (H0).
  A scoped hook cannot carry `env`, so its command passes the same resolved layout as
  `--layout FRAMEWORK_<X>_DIR=<value>` arguments. Re-running the installer replaces the block. An agent with its own `hooks:` key is refused.

**`--framework-dir` is the framework CHECKOUT, not the rendered bundle.** It is where `.env`,
`workflows/`, `instructions/` and `skills/` live. The bundle is only the package the host
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

## Using it (CLI or app)

1. **Load the bundle.** On the standalone CLI the simplest is
   `copilot --plugin-dir <bundle> --agent <plugin-name>:<orchestrator>`: the bundle is read
   live from that directory, with no install step. Otherwise (CLI or app), load it through a
   local marketplace:
   `copilot plugin marketplace add <bundle marketplace dir>`, then install the plugin from it.
   A path-sourced plugin in a local marketplace is read **live**, so re-running `install.py`
   takes effect at the next session. A direct `copilot plugin install <path>` **copies** the
   bundle instead; after every re-run of `install.py` you must then run
   `copilot plugin update <plugin>`, or the host keeps the old copy without hooks.
2. **Open the session in the framework folder.** Trust it if you installed repository hooks.
3. **Select an orchestrator agent** (e.g. the Release Train Engineer) and give it its task.
   A plugin agent is named `<plugin-name>:<agent>` by the runtime (for
   `saf-agentic-organization`: `poesis-saf-agentic-organization:release-train-engineer`), and
   so is the `agent_type` of every `task` dispatch of a plugin agent; a bare name is refused
   by the runtime. The adapter strips that namespace before opening the step session.
   Its scoped hook opens the harness session. Every session also receives a short context
   from `sessionStart` naming the framework root, the workspace, and the exact harness command
   lines.
4. The orchestrator asks the harness with ONE plain command, alone in its tool call:
   `& <venv python> <harness.py> resolve-step --workflow-slug <slug>` (or
   `… resolve-step-model`; on Linux/macOS without the leading `&`). It may write `python`, `python3`,
   `py -3` or a full interpreter path, but never `--session-id`, `$env:`, or chaining.
   **The shell does not run this command, and the adapter never edits it.** The
   `preToolUse` hook recognizes the request, runs the function itself for the agent's own
   session (taken from the hook payload), and returns the report as the tool response. The
   host shows it as a hook denial (the CLI hands the model `Denied by preToolUse hook: …`) whose reason starts with
   `SAFe agentic harness RESULT (resolve-step): …` followed by the JSON report. That is the
   successful answer: the agent must act on it and not re-issue the command. Only a reason
   starting with `SAFe agentic harness REJECTED` means the request was refused (it names
   the problem and the accepted line). Because the hook decides before the host's permission
   layer, **no permission rule or interactive approval is needed** for harness commands,
   including in autopilot, and agent shells need no `FRAMEWORK_*` variables.
5. Steps are dispatched with `task`, synchronously; `mode: background` is denied. Writes go
   through `create`/`edit`, which the harness authorizes (H3) and validates (H5). `skill`,
   `ask_user` (the human gate), `view`, `grep` and `glob` are never gated. The CLI's
   non-interactive prompt mode (`copilot -p`) offers no `ask_user` at all: run a framework
   that needs a human gate interactively.

## Model binding

`models.yaml` maps every model-profile slug of the framework catalogue
(`<fw>/conf/model-profiles.conf.yaml`) to a model id the host offers. **It must track the
models the host currently offers**, which can change after install. Every hook re-reads the
catalogue and `models.yaml` on load, never from an install-time cache, and refuses a catalogue
that names a slug `models.yaml` does not bind: the hook exits 2 and names the unbound slugs.
The runtime gives hooks no model list, so keeping `models.yaml` current is the operator's job.

**Failure is closed and visible.** If a hook cannot bootstrap, it exits 2 with a stderr
diagnostic. That happens when the framework root is gone, `.env` is malformed, the binding is
off-contract, the model catalogue names a slug `models.yaml` does not bind (or is missing),
or the payload is unreadable. The host then denies the tool call (`preToolUse`)
or shows a warning (other events). A broken install therefore blocks tool use wherever the
plugin's hooks fire. Repair it by re-running `install.py`, or by disabling the plugin.

Session records live in `%LOCALAPPDATA%\saf-agentic-harness\github-copilot-cli`, or
`$XDG_STATE_HOME/saf-agentic-harness/github-copilot-cli`. `SAF_HARNESS_STATE_DIR` overrides
both. Hook diagnostics go to stderr.

## Known limitations

These are detailed, with their fallbacks, in the spec's *Limited or unsupported boundaries*.

- **Folder trust.** In an untrusted folder, repository hooks never run, so the boundaries they
  register never fire. Harness commands are then denied with a diagnosis. Plugin hooks are
  not affected.
- **Timeouts fail open** in this runtime. The gating hooks have a 120 s budget, and the
  adapter stops every harness call 30 s before it: a stalled call is a denial, not a
  timeout.
- **Shell writes are not mediated.** The adapter denies shell commands that name the
  workspace or `logs/`, but a determined command can evade a textual check. The core's
  commit-set check is the backstop. Agents must write through `create`/`edit` (or
  `apply_patch`, which GPT-5.x models get instead).
- **Missing folders.** The runtime's `create` refuses a missing parent folder. The hook
  creates the missing folders of a `create` the harness allowed inside the workspace (folders
  only, no commit), so agents need no shell `mkdir`. It never creates a folder through a
  symlink or junction under the workspace.
- **Batching (app).** A harness command must be the only tool call in its batch. The CLI fires
  `preToolUse` once per call.
- **Plugin agent names.** Agents and dispatches must use the runtime's
  `<plugin-name>:<agent>` names; the framework bundle's prose has to say so.
- **No `ask_user` in `copilot -p`.**
- **The harness answer arrives as a "denial".** The runtime has no other channel for a
  `preToolUse` hook to answer the agent without running or editing its command. The reason
  prefix and the session hint say it is the result; a model that still retries just gets the
  same answer again. If the `preToolUse` hook times out, the agent's line runs in its shell
  without the hook environment, and the harness refuses it (`FRAMEWORK_DIR is unset`).
- **Latency.** Plugin hooks start Python for every tool call in every folder. Ungoverned
  sessions return after one state lookup.
- **Verified live on the standalone CLI** (1.0.105, Linux, `-p`, offline BYOK with a scripted
  model): `exec` entries, plugin and scoped hooks, the singular `preToolUse` shape, the
  hook-executed H4 result, H0, and H1–H3 for a plugin subagent, the model-catalogue refusal.
  **Not yet verified live:** a GitHub-authenticated CLI session with real models; the CLI
  on Windows/macOS; the interactive CLI (`ask_user`, trust prompt); marketplace installs on
  the CLI; and, in the app, `exec` entries and the deny-reason wrapper.
- **Nested dispatch** (observed in the app): an orchestrator subagent that dispatches a step
  fires a `subagentStart` whose `sessionId` is unrelated. The dispatcher is read from
  `parentAgentId`. An orchestrator acting a step still fires its scoped hook. That hook
  leaves the step session alone and never opens a root turn under a governed dispatcher.
