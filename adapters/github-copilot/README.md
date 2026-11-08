# Adapter — `github-copilot`

This adapter binds the harness to the **GitHub Copilot agent runtime**, which three hosts
share: the **Copilot CLI**, the **Copilot app** (Windows, macOS, Linux), and **VS Code** with
the chat **Session Target: Copilot**. VS Code's *Local* session target is not supported. Where
the hosts were observed to differ, the adapter accepts both forms (see the spec's host facts).
The full specification, including the hook → boundary table, plugin mode and the limits of
this runtime, is [`def/adapter/github-copilot/spec.md`](../../def/adapter/github-copilot/spec.md).

```text
adapters/github-copilot/
  adapter.py            hook entry:  <python> adapter.py hook --event <event> [--agent <slug>] --framework-dir <fw>
  render_plugin.py      renders the harness PLUGIN (Agent Plugins 1.0) — `make plugin`
  plugin/               the plugin's launcher.py, shims (bin/) and README
  install.py            the developer install: renders hooks.yaml + agent-hooks.yaml with absolute paths
  hooks.yaml            global hooks (source of truth, NOT installable as-is)
  agent-hooks.yaml      orchestrator-scoped H0 hooks (rendered into agent frontmatter)
  tools.yaml            tool names + payload keys (validated by contracts/conf/adapters/tools.conf.schema.json)
  models.yaml           canonical model slug -> host model id
  contracts/            hook-stdin / hook-stdout JSON Schemas
  github_copilot_adapter/  the adapter package
```

## Install the plugin (users)

The harness ships as the Copilot plugin **`poesis-saf-agentic-harness`**, beside the framework's
own plugin (for example `poesis-saf-agentic-organization`). Both come from the marketplace
`poesis-cloud/saf-marketplace`: branch `main` is the stable channel (`poesis-saf`), branch
`preview` the pre-release channel (`poesis-saf-preview`).

- **Copilot CLI:**

  ```bash
  copilot plugin marketplace add poesis-cloud/saf-marketplace
  copilot plugin install poesis-saf-agentic-harness@poesis-saf
  copilot plugin install poesis-saf-agentic-organization@poesis-saf
  ```

- **Copilot app:** Customize → marketplaces → add `poesis-cloud/saf-marketplace`, then install
  both plugins.
- **VS Code:** enable `chat.plugins.enabled`, add `poesis-cloud/saf-marketplace` to
  `chat.plugins.marketplaces`, install both plugins from the Agent Customizations editor, and
  select **Session Target: Copilot**.

Prerequisites: **Python ≥ 3.10** (or `SAF_HARNESS_PYTHON`), **Git ≥ 2.38** with a Git identity
in the workspace, and network access once (or a mirror through `PIP_INDEX_URL` /
`UV_INDEX_URL`). The first session provisions the harness requirements, hash-checked, into the
plugin's data folder; nothing else is installed by hand. A folder is governed when its Git
top-level holds a committed `.saf/workspace.yaml`:

```yaml
framework: poesis-saf-agentic-organization
root: portfolio
```

Elsewhere every tool call passes. In a governed folder, or once an orchestrator was selected, a
missing prerequisite denies tool calls with the exact remedy. Auto-update stays off unless you
opt in; a plugin updated during a session makes that session refuse further governed calls
("the plugin was updated; start a new session").

**Test it in VS Code** from a checkout: `make plugin` renders the plugin into `build/plugin`
(it needs [`uv`](https://docs.astral.sh/uv/) to generate the lock), then
`copilot plugin install build/plugin` makes it visible to VS Code as well, since VS Code loads
the plugins the Copilot CLI installed.

## Developer install (`install.py`)

The developer install wires a harness checkout to a framework checkout, with absolute paths.
It keeps the stricter rule that a broken bootstrap denies tool calls in every folder.

### Prerequisites

- **Python 3.10 or newer**, in a virtual environment that holds `requirements.txt`. The
  installer records that interpreter's absolute path in every hook, so hooks never depend on
  `PATH`, on bash, or on a `python` alias.
- **Git ≥ 2.38** on `PATH` (the harness rebases step instances in memory with
  `git merge-tree --write-tree`), and a **Git identity in the governed workspace**
  (`git config user.name` / `user.email`, in the workspace repository or globally): the
  harness commits every validated write onto its step instance's step ref.
- A **framework checkout**, e.g. [`saf-agentic-organization`](https://github.com/poesis-cloud/saf-agentic-organization).
  It must contain `.env`, `workflows/`, `instructions/`, `skills/` and `workspace/`.
- The framework's **Copilot plugin bundle**, rendered by the framework itself. For
  `saf-agentic-organization`, that is `builds/github-copilot/`. The bundle carries the
  agents and skills; this installer adds the hooks to it.
- With `--hooks-target repo` only: the **folder trusted** (the app's trust, or the CLI's trust
  prompt / `--trust-folder`). Repository hooks silently do not run in an untrusted folder.
  Plugin hooks, including a plugin agent's scoped hooks, run regardless (observed on the
  standalone CLI).
- A **`models.yaml` that binds every profile** of the framework's model catalogue (see
  [Model binding](#model-binding)).

### Install — Windows (PowerShell)

```powershell
# 1. the harness and its venv
git clone https://github.com/poesis-cloud/saf-agentic-harness C:\saf\saf-agentic-harness
cd C:\saf\saf-agentic-harness
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2. render the framework's bundle first (in the framework checkout; see its README)

# 3. add the hooks to the bundle — run with the VENV interpreter: it is the one recorded
.venv\Scripts\python adapters\github-copilot\install.py `
    --framework-dir C:\saf\saf-agentic-organization `
    --bundle-dir    <the rendered bundle> `
    --workspace-dir C:\saf\saf-agentic-workspace      # optional: a workspace outside the checkout
```

### Install — Linux / macOS

```bash
git clone https://github.com/poesis-cloud/saf-agentic-harness ~/saf/saf-agentic-harness
cd ~/saf/saf-agentic-harness
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python adapters/github-copilot/install.py \
    --framework-dir ~/saf/saf-agentic-organization \
    --bundle-dir <the rendered bundle> \
    [--workspace-dir ~/saf/saf-agentic-workspace]
# or, the same through make (PYTHON defaults to python3 on PATH — activate the venv first,
# or pass HARNESS_PYTHON=<absolute venv python>):
make install-hooks FRAMEWORK_DIR=… BUNDLE_DIR=… [WORKSPACE_DIR=…] [HARNESS_PYTHON=…]
```

### What the installer writes

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

- `--hooks-target repo --target-repo <repo>` writes `<repo>/.github/hooks/safe-harness-github-copilot.json`.
  This repository git-ignores that path; ignore it in the target repository too, because it
  holds machine paths.
- `--hooks-target user` writes `~/.copilot/hooks/` (or `$COPILOT_HOME/hooks/`).

Other options: `--hook-style shell` writes `powershell`/`bash` command strings instead of
`exec`. `--dry-run` prints what would be written. Everything the installer writes holds
absolute machine paths: **generated, never committed**. Re-run the installer after moving
either checkout or recreating the venv.

## Using it

1. **Load the bundle** (developer install; plugin users skip this step). On the standalone
   CLI the simplest is
   `copilot --plugin-dir <bundle> --agent <plugin-name>:<orchestrator>`: the bundle is read
   live from that directory, with no install step. Otherwise (CLI or app), load it through a
   local marketplace:
   `copilot plugin marketplace add <bundle marketplace dir>`, then install the plugin from it.
   A path-sourced plugin in a local marketplace is read **live**, so re-running `install.py`
   takes effect at the next session. A direct `copilot plugin install <path>` **copies** the
   bundle instead; after every re-run of `install.py` you must then run
   `copilot plugin update <plugin>`, or the host keeps the old copy without hooks.
2. **Open the session in the governed folder**: the SAF workspace (plugin mode), or the
   framework folder (developer install). Trust it if you installed repository hooks.
3. **Select an orchestrator agent** (e.g. the Release Train Engineer) and give it its task.
   A plugin agent is named `<plugin-name>:<agent>` by the runtime (for
   `saf-agentic-organization`: `poesis-saf-agentic-organization:release-train-engineer`), and
   so is the `agent_type` of every `task` dispatch of a plugin agent; a bare name is refused
   by the runtime. The adapter strips that namespace before opening the worker session.
   Its scoped hook opens the harness session. Every session also receives a short context
   from `sessionStart` naming the framework root, the workspace, and the exact harness command
   lines.
4. The orchestrator asks the harness with ONE plain command, alone in its tool call, either
   `& <python> <harness.py> resolve-step --workflow-slug <slug>` or
   `& <python> <harness.py> resolve-step-model`, which takes no flag at all (on Linux/macOS
   without the leading `&`). The `sessionStart` context gives both lines with the installed
   paths. It may write `python`, `python3`, `py -3` or a full interpreter path, but never
   `--session-id`, `$env:`, or chaining.
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

**Only bind a model whose subagents can write.** On this host the runtime chooses a `task`
subagent's tools by MODEL, not only by the agent's `tools:` list. A model is bindable only if a
`task` subagent running on it gets a write tool (`create`/`edit`, or `apply_patch`). Without
one, every artifact write falls back to the shell, the shell guard denies it, and the step can
never deliver. `gpt-5.3-codex` is not bound for this reason: in the Copilot app (CLI runtime
1.0.102) a `task` subagent on it got only `view`, `rg`, `glob`, `powershell`,
`read_powershell`, `stop_powershell`, `list_powershell` and `skill`, although its agent
declared `tools: [read, edit, search, execute, skill]`. A framework catalogue that still names
it is refused at hook load. Before binding a new model, dispatch a `task` subagent on it and
check that it gets a write tool.

**Failure is closed and visible.** If a hook cannot bootstrap, it exits 2 with a stderr
diagnostic. That happens when the framework root is gone, `.env` is malformed, the binding is
off-contract, the model catalogue names a slug `models.yaml` does not bind (or is missing),
or the payload is unreadable. The host then denies the tool call (`preToolUse`)
or shows a warning (other events). In the developer install, a broken install blocks tool use
wherever its hooks fire; repair it by re-running `install.py`. In plugin mode, only governed
folders and sessions with a selected orchestrator are blocked.

Session records live in `SAF_HARNESS_STATE_DIR`, else `$PLUGIN_DATA/state` in plugin mode, else
`%LOCALAPPDATA%\saf-agentic-harness\github-copilot` or
`$XDG_STATE_HOME/saf-agentic-harness/github-copilot`. The plugins' shared registry lives in
`SAF_AGENTIC_REGISTRY_DIR`, else `%LOCALAPPDATA%\saf-agentic\registry` or
`$XDG_STATE_HOME/saf-agentic/registry`. Hook diagnostics go to stderr.

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
- **Latency.** Plugin hooks fire on every tool call in every folder. In plugin mode the
  launcher returns at once when the folder holds no marker and no orchestrator was selected;
  on Windows each firing also starts a PowerShell.
- **VS Code's Local session target** is refused in a governed folder with "select Session
  Target: Copilot".
- **The framework's scoped H0 needs the harness plugin.** If it is not installed, or never
  started a session, the orchestrator's scoped hook fails and the session stays ungoverned.
- **Verified live on the standalone CLI** (1.0.105, Linux, `-p`, offline BYOK with a scripted
  model): `exec` entries, plugin and scoped hooks, the singular `preToolUse` shape, the
  hook-executed H4 result, H0, and H1–H3 for a plugin subagent, the model-catalogue refusal.
  **Not yet verified live:** a GitHub-authenticated CLI session with real models; the CLI
  on Windows/macOS; the interactive CLI (`ask_user`, trust prompt); marketplace installs on
  the CLI; and, in the app, `exec` entries and the deny-reason wrapper.
- **Nested dispatch** (observed in the app): an orchestrator subagent that dispatches a step
  fires a `subagentStart` whose `sessionId` is unrelated. The dispatcher is read from
  `parentAgentId`. An orchestrator acting a step still fires its scoped hook. That hook
  leaves the worker session alone and never opens a root turn under a governed dispatcher.
