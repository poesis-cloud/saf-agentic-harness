# Changelog

All notable changes to this project are documented here. Versions follow
[SemVer](https://semver.org/); pre-release tags (`-alpha`, `-beta`, `-rc`) are published as
GitHub pre-releases.

## v1.0.0-alpha.1

The first pinnable pre-release of the SAFe agentic orchestration harness. It **supersedes
`v1.0.0-beta.1`**, which was tagged before the correctness fixes below and should not be
pinned.

### What ships

- **The harness core** (`src/`, entry point `harness.py`): the twelve functions of
  [`def/core/spec.md`](def/core/spec.md), functions 0–11 from `start-session` to
  `end-session`. They cover workflow and step resolution (instructions, skills, model),
  pre- and postcondition checks, write authorization, and artifact validation and commit,
  all driven by declarative workflow contracts with CEL conditions.
- **Contracts** (`contracts/`): JSON Schemas for the framework configuration, the session and
  report logs, and the adapter bindings.
- **Two host adapters**:
  - `vscode-github-copilot-chat` binds VS Code with GitHub Copilot Chat agent hooks. It ships
    the H0–H7 hooks, the shared `adapters/dispatch.sh` funnel, and the
    `adapters/render_hooks.py` installer. Agent harness commands are hook-executed, as for
    the Copilot app: the `PreToolUse` hook runs one plain `run_in_terminal`
    `resolve-step`/`resolve-step-model` request itself, for the session its tracker
    resolves, and returns the report as the deny reason plus `additionalContext`. It no
    longer stamps `--session-id` through `updatedInput`.
  - `github-copilot-cli` binds the GitHub Copilot app and the Copilot CLI, which share one
    agent runtime ([spec](def/adapter/github-copilot-cli/spec.md),
    [README](adapters/github-copilot-cli/README.md)). It is Windows-first: hooks run
    `adapter.py` through the recorded venv interpreter (`exec` + `args`), with no bash. Its
    `install.py` (or `make install-hooks-copilot-cli`) adds the hooks to a framework's
    Copilot plugin bundle: a global `hooks.json` plus the orchestrators' agent-scoped H0
    block. It handles the runtime's batched `preToolUse`, and a batch is denied when any of
    its calls is. Agent harness commands are hook-executed: the `preToolUse` hook runs one
    plain `resolve-step`/`resolve-step-model` request itself, for the session named by the
    payload, and returns the report as the deny reason (`SAFe agentic harness RESULT …`).
    It never edits or runs the command, and needs no permission rule. Anything else that
    names `harness.py` is denied.
- **Verification**: `make verify` runs the unit, functional, and adapter planes on Python
  3.10, 3.11 and 3.12. CI also runs the suite on Windows (Python 3.12).

### Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make install-hooks FRAMEWORK_DIR=<framework root> BUNDLE_DIR=<rendered bundle>
```

For the GitHub Copilot app / Copilot CLI (Windows, PowerShell):

```powershell
.venv\Scripts\python adapters\github-copilot-cli\install.py `
    --framework-dir <framework checkout> --bundle-dir <rendered plugin bundle>
```

Full steps are in the [VS Code adapter README](adapters/vscode-github-copilot-chat/README.md)
and the [Copilot CLI adapter README](adapters/github-copilot-cli/README.md).

### Integrity boundaries

- **Write authorization:** writes to governed artifacts are authorized per path and per
  action (create, update, delete).
- **Deletion:** deleting governed artifacts is intentionally unsupported in v1.
- **Undeclared paths:** a declared write tool whose paths cannot be extracted is denied.
- **Commits:** function 9 commits only the validated artifact set. Anything else already
  staged stays staged and out of the commit.
- **Orchestrator sessions:** each session drives at most one workflow instance at a time.
- **Unreadable logs:** an unreadable or malformed session log becomes a `system-error`
  report. No raw exception crosses the command boundary.
- **Hook bootstrap fails closed:** every rendered hook records `FRAMEWORK_DIR` and its
  validated interpreter. A hook that cannot bootstrap exits 2, which the host treats as
  blocking.

### Fixed since `v1.0.0-beta.1`

- **Function 9 commit scope:** the commit is scoped to the validated paths. It no longer
  sweeps foreign staged content into the commit.
- **One instance per orchestrator session:** a session already bound to an in-flight
  instance is refused with `state-error` / `session-instance-bound` when it tries to resolve
  another workflow.
- **Workflow-slug collision:** instance lookup matches the workflow slug exactly, so
  `planning` no longer claims `planning-review-…` instances.
- **Read and write failures on session logs** are classified as `system-error`:
  - `log-unreadable` for read failures; retryable when the file cannot be opened, not
    retryable when it cannot be parsed;
  - `log-creation-failed` and `log-append-failed` for write failures.
- **Adapter binding validation:** `tools.yaml` and `models.yaml` are validated against their
  contract schemas at load, using the shared `adapters/binding_contract.py`.
- **`apply_patch` paths:** they are parsed from the patch headers, and each path is
  authorized with its own action.
- **Hook bootstrap:** it no longer depends on the inherited environment. The interpreter
  and `FRAMEWORK_DIR` are recorded in each hook's `env`, and `FRAMEWORK_DIR/.env` is loaded.
- **Release hygiene:** documentation was corrected, and pre-release tags now publish as
  GitHub pre-releases.
- **Windows portability:**
  - Artifact paths are read with `\` and `/` both as separators and reported in POSIX
    form, so `portfolio\x.md` resolves like `portfolio/x.md` on every OS.
  - A path that is not workspace-relative (absolute, drive, UNC or `..`) never resolves
    to an artifact. It is denied as `unresolvable-resource`, even under a root
    `<placeholder>`.
  - `harness.py` and both adapter entries answer UTF-8 whatever the console codepage.
    Child output (Git, the harness, interpreter probes) is decoded as UTF-8 with
    replacement.
  - `.gitattributes` keeps `*.sh` LF on Windows checkouts.

### Known alpha limitations

- **Supported hosts:** VS Code with GitHub Copilot Chat, and the GitHub Copilot app /
  Copilot CLI. VS Code hooks are a preview feature, so settings and events may drift;
  adapter spec I13 lists the open host verifications.
- **Copilot app / CLI:** the following limits apply.
  - Hooks run only in a **trusted folder**.
  - A timed-out `preToolUse` **fails open**.
  - **Shell writes** are guarded textually only. The core's commit-set check is the
    backstop.
  - The harness answer to an agent reaches it as a hook denial whose reason starts with
    `SAFe agentic harness RESULT`.
  - `exec` hook entries and scoped hooks inside subagents are not yet byte-verified in the
    app.
  - See the adapter spec's *Limited or unsupported boundaries*.
- **Session attribution:** no adapter edits an agent's command. Both hooks run the agent's
  harness request themselves, so the core's former tool-boundary-stamp exception is retired
  (VS Code adapter spec I4). In VS Code, the answer arrives as `Tool execution denied: …`,
  and the user also sees a `Tried to use run_in_terminal` block. Inside a VS Code subagent,
  attribution comes from the adapter's session stack (I13).
- **Host fail-open:** the host proceeds when a hook times out or fails to spawn (I6).
- **`Stop` hooks:** a bootstrap failure at `Stop`/`SubagentStop` exits 2. That asks the
  agent to continue, bounded by the host's `stop_hook_active` guard.
- **`apply_patch` moves** of governed artifacts are refused, because a move implies a delete.
