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
- **One host adapter**: `vscode-github-copilot-chat`, which binds VS Code with GitHub Copilot
  Chat agent hooks. It ships the H0–H7 hooks, the shared `adapters/dispatch.sh` funnel, and
  the `adapters/render_hooks.py` installer.
- **Verification**: `make verify` runs the unit, functional, and adapter planes on Python
  3.10, 3.11 and 3.12.

### Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make install-hooks FRAMEWORK_DIR=<framework root> BUNDLE_DIR=<rendered bundle>
```

Full steps are in the
[adapter README](adapters/vscode-github-copilot-chat/README.md).

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
- **`artifacts['<kind>']` resolves by kind:** conditions now see every committed instance
  that `workspace_layout` binds to the artifact kind. Before, the slug was read as a
  root-relative path, so `artifacts['epic']` was always empty.
- **Artifact provenance:** every instance exposes the harness-owned
  `provenance.sessionId`, the session whose harness commit last changed it. The harness
  derives it from its own commit trailer. A document that supplies `provenance` itself is
  refused as `reserved`, so provenance cannot be forged. Conditions also get the
  `stepSessionIds` runtime constant.
- **Mandatory built-in delivery check:** `check-step-postconditions` always evaluates
  `harness-artifact-delivered` first. The step passes only if at least one instance of its
  declared artifact kind was committed, created or updated, by the step's own sessions.
  There is no opt-out: an agent's report is never evidence that it delivered. Workflows can
  drop their per-step existence postconditions. The `harness-artifact-delivered` condition
  slug is reserved.
- **Host-bound model resolution (breaking contract change):** `resolve-step-model` requires
  `boundModels`, the canonical model slugs the host binds, and ranks only those. If no
  catalogue model is bound, it fails with `inquiry-error` / `no-bound-model` instead of
  returning a model the host cannot run. The `vscode-github-copilot-chat` adapter supplies
  `--bound-model` from its `models.yaml` and denies agent-authored `--bound-model` flags.

### Known alpha limitations

- **Supported hosts:** only VS Code with GitHub Copilot Chat. Hooks are a VS Code preview
  feature, so settings and events may drift; adapter spec I13 lists the open host
  verifications.
- **Session attribution:** it relies on the core's interim tool-boundary-stamp exception
  (adapter spec I4). A host-native attribution surface is tracked, not scheduled.
- **Host fail-open:** the host proceeds when a hook times out or fails to spawn (I6).
- **`Stop` hooks:** a bootstrap failure at `Stop`/`SubagentStop` exits 2. That asks the
  agent to continue, bounded by the host's `stop_hook_active` guard.
- **`apply_patch` moves** of governed artifacts are refused, because a move implies a delete.
