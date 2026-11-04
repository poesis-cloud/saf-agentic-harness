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
- **Paths without spaces or special characters** for the venv interpreter, the harness
  checkout and the state directory (`%LOCALAPPDATA%`). The harness command the adapter
  rewrites must name them unquoted, or the app can never pre-approve it. On Windows the 8.3
  short form is used when it exists; otherwise `install.py` refuses (see
  [Approving the harness interpreter](#approving-the-harness-interpreter)).

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
  `FRAMEWORK_*_DIR` as resolved from `<fw>/.env` and `--workspace-dir`, plus
  `SAF_HARNESS_PYTHON` (the recorded interpreter). **No user-level variables (`setx`) are
  needed:** hooks get their layout from `env`, and agents get absolute paths from the
  injected hint (see below).
- The installer **refuses** an interpreter (`--python`, default: the one running it) that
  cannot import the harness requirements, and a malformed `<fw>/.env`. It also refuses when
  the interpreter, the harness entry point or the layout-file directory needs shell quoting,
  unless `--allow-quoted-paths` is given. Nothing is written then.
- After writing, it **prints the permission rule** the harness command needs (see
  [Approving the harness interpreter](#approving-the-harness-interpreter)). It never edits
  your permission file.
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
4. The orchestrator runs `& <venv python> <harness.py> resolve-step --workflow-slug <slug>`
   (or `… resolve-step-model`) with its shell tool. It may write `python`, `python3`, `py -3`
   or a full interpreter path. The adapter accepts exactly one plain invocation of the
   installed harness, without `--session-id` or `--env-file`, and rewrites it into the
   canonical, attributed command:
   `& <venv python> <harness.py> --env-file <layout file> resolve-step --session-id <session> --workflow-slug <slug>`.
   The layout file holds `FRAMEWORK_DIR` and every `FRAMEWORK_*_DIR`; the adapter publishes
   it under the state directory (`layouts\layout-<hash>.env`). Everything else that mentions
   `harness.py` is denied.
5. Steps are dispatched with `task`, synchronously; `mode: background` is denied. Writes go
   through `create`/`edit`, which the harness authorizes (H3) and validates (H5). `skill`,
   `ask_user` (the human gate), `view`, `grep` and `glob` are never gated.

**Failure is closed and visible.** If a hook cannot bootstrap, it exits 2 with a stderr
diagnostic. That happens when the framework root is gone, `.env` is malformed, the binding is
off-contract, or the payload is unreadable. The app then denies the tool call (`preToolUse`)
or shows a warning (other events). A broken install therefore blocks tool use wherever the
plugin's hooks fire. Repair it by re-running `install.py`, or by disabling the plugin.

Session records live in `%LOCALAPPDATA%\saf-agentic-harness\github-copilot-cli`, or
`$XDG_STATE_HOME/saf-agentic-harness/github-copilot-cli`. `SAF_HARNESS_STATE_DIR` overrides
both. Hook diagnostics go to stderr.

## Approving the harness interpreter

The app's permission layer checks the command that actually runs, which is the **rewritten**
harness line, not the one the agent wrote. That line is led by the venv interpreter. Without
a saved approval the app asks you each time; in autopilot or any unattended run it **denies**
(`denied-no-approval-rule-and-could-not-request-from-user`).

Approve the interpreter once, with the rule `install.py` prints:

```json
{
  "locations": {
    "C:\\saf\\saf-agentic-organization": {
      "tool_approvals": [
        {
          "kind": "commands",
          "commandIdentifiers": ["C:\\saf\\saf-agentic-harness\\.venv\\Scripts\\python.exe:*"]
        }
      ]
    }
  }
}
```

- Put it in `%USERPROFILE%\.copilot\permissions-config.json` (or `$COPILOT_HOME/…`,
  `~/.copilot/…` on Linux/macOS). Merge it into the existing `locations` rather than
  replacing the file, and do not edit the file while a session runs.
- The location key is the **Git root** of the folder you open sessions in (or that folder
  when it is not a repository).
- Instead, you can approve the first harness command for the session when the app asks.
- The rule approves **any** command led by that interpreter. The adapter still gates
  harness commands (H4), and a dedicated venv keeps the rule narrow.

The rewrite is kept approvable on purpose. It is one plain command, with no `$env:X=…;` or
`X=… cmd` prefix and no quotes around the interpreter; any of those makes a rule-based
approval impossible. When a path has no unquoted spelling (a space, `'`, `$`, `&`, …, and no
8.3 short name), the adapter quotes it, the line can only be approved interactively, and
`install.py` refuses unless you pass `--allow-quoted-paths`. To avoid this, put the harness,
its venv and the state directory (`SAF_HARNESS_STATE_DIR`, set user-wide so the app
inherits it) on plain paths.

## Known limitations

These are detailed, with their fallbacks, in the spec's *Limited or unsupported boundaries*.

- **Folder trust.** In an untrusted folder, scoped hooks never run, so no harness session
  opens. Harness commands are then denied with a diagnosis.
- **Timeouts fail open** in this runtime. The gating hooks have a 120 s budget.
- **Shell writes are not mediated.** The adapter denies shell commands that name the
  workspace or `logs/`, but a determined command can evade a textual check. The core's
  commit-set check is the backstop. Agents must write through `create`/`edit`.
- **Batching.** A harness command must be the only tool call in its batch.
- **Host denials of the rewritten line.** The agent sees the rewritten command (with
  `--env-file` and `--session-id`) in a host denial. In a live trial the model took those
  parts for its own mistake, "fixed" them and retried five times. The session hint, and the
  adapter's `postToolUseFailure` context for a failed harness command, therefore say that the
  harness rewrote the line and that the denial comes from the host permission layer, so the
  agent should stop and ask for approval. The host's own denial text cannot be changed, and
  it is not yet verified that a permission denial fires `postToolUseFailure`.
- **Latency.** Plugin hooks start Python for every tool call in every folder. Ungoverned
  sessions return after one state lookup.
- **Not yet byte-verified in the app:** `exec` entries (fall back to `--hook-style shell`),
  `modifiedArgs` without `permissionDecision`, and scoped hooks inside subagents.
