# Changelog

All notable changes to this project are documented here. Versions follow
[SemVer](https://semver.org/); pre-release tags (`-alpha`, `-beta`, `-rc`) are published as
GitHub pre-releases.

## v1.0.0-alpha.1

The first pinnable pre-release of the SAFe agentic orchestration harness.

### What ships

- **The harness core** (`src/`, entry point `harness.py`): the twelve functions of
  [`def/core/spec.md`](def/core/spec.md), functions 0–11 from `start-session` to
  `end-session`. They cover workflow and step resolution (instructions, skills, model),
  pre- and postcondition checks, write authorization, artifact validation and commit, and
  promotion, all driven by declarative workflow contracts with CEL conditions.
- **Step transactions.** The step instance is the unit of acceptance:
  - Function 0 creates the worker session's step ref
    `refs/harness/steps/<workflowInstanceId>/<stepSlug>/<workerSessionId>` without any
    checkout. A retry on an unchanged base continues the previous step instance's ref tip.
  - **Artifact locks.** Function 8 claims `refs/harness/locks/<artifactPath>` create-only
    before a step instance's first write to a path, re-points it on a retry, and treats a
    promoted holder's lock as released. It denies a path whose lock another step instance
    holds, naming that step instance's workflow instance and step. The clean-baseline check
    applies to unclaimed paths only.
  - **Commit onto the step ref.** Function 9 commits each validated set onto the step ref
    with a temporary index plus `git commit-tree`. `HEAD`, the working tree and the
    workspace's index never move. A revert restores a path from the step ref when the step
    ref changed it, else from `HEAD`, else deletes it.
  - **Promotion.** Function 10 rebases the step ref in memory with `git merge-tree` (a path
    changed on both sides is `step-rebase-conflict`). It checks delivery over the step state
    and the worker session's own commits, and evaluates the postconditions on the step state.
    On pass, it fast-forwards the workspace branch by one atomic compare-and-swap, the
    acceptance point; it then syncs the promoted paths' index entries and deletes the locks,
    best-effort, since a promoted holder's locks already count as released. On fail, it
    withholds: the workspace branch, the index and the locks are untouched (only the step ref
    may have moved, by the rebase). Its report carries `promotion`
    (`{outcome: promoted | withheld, workspaceCommit}`).
  - The internal sweep (`ArtifactStore.sweep_workspace`) reports step refs, every held
    artifact lock (stale ones included) with its holder, and unlocked working-tree changes.
- **The condition language.** `setQuery` / `setPredicate` read `workspace` (artifact
  instances: `kind`, `path`, `data`), `workflows` and `steps` (workflow and step instances
  from this machine's logs), `workflow` and `step` (the current ones), and `selected`. The
  configuration load type-checks every condition: an `a.data` read needs a kind guard in the
  same macro (`a.kind == '<literal>'`, or a path join with a step instance of a literal step
  of a known workflow). An unguarded read; an unknown kind, step or workflow slug; an
  undeclared property; and, in a precondition, a current-workflow step that is not a
  declared predecessor or a path join with `step` are configuration load errors. Only the
  kinds the guards fix are read from the evaluated state.
- **Recovery.** `workflow-instance-busy` names the holding orchestrator session and prints
  the exact command that ends it: the effective framework layout as environment variables,
  the installed interpreter and `harness.py`, and `end-session --session-id=<holder>`,
  rendered for PowerShell on Windows. See the README's "Recovering an interrupted workflow
  instance".
- **Contracts** (`contracts/`): JSON Schemas for the framework configuration, the session and
  report logs, and the adapter bindings. Both adapters validate `tools.yaml` and
  `models.yaml` against them at load, through the shared `adapters/binding_contract.py`.
- **Two host adapters**:
  - `vscode-github-copilot-chat` binds VS Code with GitHub Copilot Chat agent hooks. It ships
    the H0–H7 hooks, the shared `adapters/dispatch.sh` funnel, and the
    `adapters/render_hooks.py` installer. Agent harness commands are hook-executed: the
    `PreToolUse` hook runs one plain `run_in_terminal` `resolve-step`/`resolve-step-model`
    request itself, for the session its tracker resolves, and returns the report as the deny
    reason plus `additionalContext`.
  - `github-copilot-cli` binds the GitHub Copilot app and the Copilot CLI, which share one
    agent runtime ([spec](def/adapter/github-copilot-cli/spec.md),
    [README](adapters/github-copilot-cli/README.md)). It is Windows-first: hooks run
    `adapter.py` through the recorded venv interpreter (`exec` + `args`), with no bash. Its
    `install.py` (or `make install-hooks-copilot-cli`) adds the hooks to a framework's
    Copilot plugin bundle: a global `hooks.json` plus the orchestrators' agent-scoped H0
    block. It handles both `preToolUse` shapes: the standalone CLI's one call per firing and
    the app's batch, which is denied when any of its calls is. Plugin agents, which the
    runtime names `<plugin>:<agent>`, are governed under their framework agent name. Agent
    harness commands are hook-executed: the `preToolUse` hook runs one plain
    `resolve-step`/`resolve-step-model` request itself, for the session named by the
    payload, and returns the report as the deny reason (`SAFe agentic harness RESULT …`).
    It never edits the command or lets the shell run it, and needs no permission rule.
    Anything else that names `harness.py` is denied. It does not bind `gpt-5.3-codex`: on
    this host a model is bindable only if its `task` subagents get a write tool, and in the
    Copilot app (runtime 1.0.102) a `task` subagent on that model got none.
  - **A refused sub-agent's return never concludes the step.** Both adapters record, at H1,
    each sub-agent whose worker-session registration was refused or never happened, and arm
    that record at the sub-agent's own stop. H6 skips one armed return instead of
    evaluating, so the step stays in flight until its bound worker session returns.
  - **The H5 retry hint names the revert the harness performed:** each failing path's revert
    record from the function-9 report, "restored from <step ref>", "restored from HEAD" or
    "deleted". A record that names no source renders without one.
- **Verification**: `make verify` runs the unit, functional, and adapter planes on Python
  3.10, 3.11 and 3.12. CI also runs the suite on Windows (Python 3.12).

### Requirements

- Python 3.10–3.12, with `requirements.txt` installed in the interpreter the hooks run.
- **Git ≥ 2.38** on `PATH`: promotion rebases in memory with `git merge-tree --write-tree`.
- **A Git identity** (`user.name` and `user.email`) in the governed workspace repository, or
  globally: the harness commits every validated write onto its step ref and replays step
  commits on promotion. The `refs/harness/*` refs stay local-only.

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
  action (create, update, delete). `apply_patch` paths are parsed from the patch headers,
  and each path is authorized with its own action.
- **Deletion:** deleting governed artifacts is intentionally unsupported in v1.
- **Undeclared paths:** a declared write tool whose paths cannot be extracted is denied.
- **Out-of-step writes:** a write from outside an in-flight step instance with its own step
  ref is denied ("no step in flight").
- **Commits:** function 9 commits exactly the validated artifact set onto the step ref.
  Anything else already staged in the workspace's index stays staged and out of the commit.
  The `valid` report carries `commitId` and `committedPaths`; `commitId` is `null` and the
  paths are empty when the set was byte-identical to the step ref tip, so the session log
  alone records what each worker session committed.
- **Artifact delivery:** a step outcome passes only if its step state changes exactly one
  artifact instance of the step's declared kind, which the worker session's own function-9
  commits also change, and every declared postcondition passes (function 10, invariants
  4–5). Delivery is checked even when a step declares no postconditions; with none, delivery
  alone decides. Delivery is a harness invariant, not a condition: it has no slug and cannot
  be configured. Function 10 reports it as
  `artifactDelivery` in the same journaled entry.
- **Orchestrator sessions:** each orchestrator session drives at most one workflow instance
  at a time; another is refused with `state-error` / `session-instance-bound`. A workflow
  instance another live orchestrator session drives is refused with
  `workflow-instance-busy`. Workflow instance lookup matches the workflow slug exactly, so
  `planning` never claims `planning-review-…` workflow instances.
- **One worker session per step resolution:** function 0 refuses a second worker session
  for a step resolution that already registered one, with `state-error` /
  `worker-session-bound`, also under concurrency. The check and the registration run as one
  critical section under an exclusive OS file lock (`fcntl.flock` on POSIX,
  `msvcrt.locking` on Windows) on `harness-registration.lock` in the workspace repository's
  Git directory: repository metadata, not workspace state, a ref, or a log entry. A failed
  step recovers through function 10 and re-resolution, which admits a new worker session.
- **Failed registrations fail closed (both adapters):** a `start-session` that answers an
  error (`worker-session-bound` included) or no report leaves the session recorded
  unregistered. Its writes, dispatches and harness requests are denied until a registration
  succeeds. Only `not-applicable` leaves a session ungoverned.
- **Unreadable logs:** session-log read and write failures are a `system-error` report, never
  a raw exception: `log-unreadable` (retryable when the file cannot be opened, not when it
  cannot be parsed), `log-creation-failed` and `log-append-failed`.
- **Hook bootstrap fails closed:** every rendered hook records `FRAMEWORK_DIR` and its
  validated interpreter in its `env`, independent of the inherited environment, and loads
  `FRAMEWORK_DIR/.env`. A hook that cannot bootstrap exits 2, which the host treats as
  blocking.
- **Unroutable model catalogue fails closed:** on every hook load, both adapters refuse a
  framework model catalogue (`conf/model-profiles.conf.yaml`) that is missing or names a
  profile slug the adapter's `models.yaml` does not bind. Nothing is cached from install, so
  `models.yaml` must track the models the host currently offers.

### Fixes

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
- **YAML artifacts:** `.yaml`/`.yml` artifacts are read as YAML in validation, discovery and
  CEL data, and load in the JSON data model: an unquoted `2026-11-04` stays a string, as
  JSON Schema 2020-12 expects. Malformed YAML, and YAML-only values (`!!set`, `!!binary`,
  `.nan`, `.inf`), are a `parse` finding, so the write is reverted.
- **Hook-executed H4 attribution:** both adapters pass the tracked parent session with a
  `resolve-step`/`resolve-step-model` request, so a worker session's request is journaled
  under its orchestrator session.
- **`github-copilot-cli`, nested step dispatch:** a step dispatched by an orchestrator that
  is itself a subagent opens under that orchestrator's session. H1 reads the dispatcher
  from the host-observed `parentAgentId`; the nested `subagentStart` `sessionId` is an
  unrelated id. The scoped H0 of an orchestrator acting a step does not re-register the
  step as a root turn, and injects no workflow context into it.
- **`github-copilot-cli`, shell guard (I9):** a relative path token resolves against the
  payload `cwd`, and git pathspecs (`<rev>:<path>`, tokens after `--`) count as path
  references. So `New-Item portfolio\…` run in the workspace, or `git show HEAD:<path>`, is
  denied like an absolute path.
- **`github-copilot-cli`, missing folders (H3 host adaptation):** the runtime's `create`
  refuses a missing parent folder. Once the harness has allowed a `create` and the whole
  firing is allowed, the hook creates the missing parent folders inside the workspace:
  folders only, no content, no commit. Actors need no shell `mkdir`, which I9 denies.
- **`github-copilot-cli`, review hardening:**
  - H3 folder creation starts at the workspace's real path and refuses any symlink or
    junction under it, so it can never land outside the workspace.
  - `apply_patch` header paths are taken verbatim; an empty or whitespace-padded path makes
    the patch unextractable (denied), as in the VS Code adapter.
  - I9 checks a quoted option value (`--output="workspace/…"`) without its quotes, and does
    not read an option such as `--format=%h:%s` as a `<rev>:<path>` object.
  - The H4 recognizer accepts the hinted line when a path holds an apostrophe, in the exact
    PowerShell or POSIX quoting the hint renders.
  - H5 claims a restore only for a `reverted` write; an error report is relayed as
    "write outcome unknown", and the actor inspects the artifact before retrying.
  - Every harness call of a firing is bounded by a deadline inside the hook's registered
    timeout, so a stalled call is a denial, not a fail-open timeout.
  - Context injection follows the documented limits (10 KB for `postToolUse`, 10 MiB of
    hook output for `subagentStart` and H0), falls back to load directives past them, and
    marks the context injected only when all of it was delivered.
  - Scoped H0 commands carry the same resolved `FRAMEWORK_*_DIR` layout as the global hooks
    (`--layout`), and `install.py` commits its outputs all or nothing.

### Known alpha limitations

- **Supported hosts:** VS Code with GitHub Copilot Chat, and the GitHub Copilot app /
  Copilot CLI. VS Code hooks are a preview feature, so settings and events may drift;
  adapter spec I13 lists the open host verifications.
- **VS Code adapter: not live-verified for this release.** It has CI coverage only.
- **Not exercised live (CI only):**
  - `apply_patch` deletes and the multi-file all-or-nothing revert: no model bound on the
    Copilot app writes with `apply_patch` (`gpt-5.3-codex` got no write tool and is unbound).
  - The lock conflict between two step instances, a compare-and-swap failure, stale locks,
    and the sweep.
- **Bootstrap failure at `preToolUse`:** the hook exits 2 and the agent sees only "hook
  exited with code 2". The diagnostic goes to the hook's stderr.
- **A hook timeout fails open** (confirmed live), and so does a hook that fails to spawn
  (I6). In the Copilot app / CLI adapter, deadlines inside the hook stop harness calls before
  the host's timeout, except for a stall before the adapter even runs. The VS Code adapter
  sets no such deadline, so a stalled harness call there can reach the host's timeout.
- **A `start-session` refused with `worker-session-bound` is not journaled** (core spec,
  rule 4: the refused worker session has no log). The refusal reaches the agent only through
  the hook.
- **An interrupted orchestrator session keeps its workflow instance busy** until it is
  ended manually; see the README's "Recovering an interrupted workflow instance".
- **Copilot app / CLI:** the following limits apply.
  - Repository hooks run only in a **trusted folder**; plugin hooks run regardless.
  - **Shell writes** are guarded textually only. The core's commit-set check is the
    backstop.
  - The harness answer to an agent reaches it as a hook denial whose reason starts with
    `SAFe agentic harness RESULT`; the app's wrapper around that reason is not
    byte-verified.
  - The standalone CLI was verified live only on Linux, offline with a scripted model.
  - The CLI's non-interactive `copilot -p` offers no `ask_user` (no human gate).
  - See the adapter spec's *Limited or unsupported boundaries*.
- **Session attribution:** no adapter edits an agent's command. Both hooks run the agent's
  harness request themselves (VS Code adapter spec I4). In VS Code, the answer arrives as
  `Tool execution denied: …`, and the user also sees a `Tried to use run_in_terminal` block.
  Inside a VS Code subagent, attribution comes from the adapter's session stack (I13).
- **`Stop` hooks:** a bootstrap failure at `Stop`/`SubagentStop` exits 2. That asks the
  agent to continue, bounded by the host's `stop_hook_active` guard.
- **`apply_patch` moves** of governed artifacts are refused, because a move implies a delete.
