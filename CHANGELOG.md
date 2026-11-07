# Changelog

All notable changes to this project are documented here. Versions follow
[SemVer](https://semver.org/); pre-release tags (`-alpha`, `-beta`, `-rc`) are published as
GitHub pre-releases.

## Unreleased

### Step transactions (core spec, PR #18)

- **The step instance is the unit of acceptance.** Function 0 creates the worker session's
  step ref `refs/harness/steps/<workflowInstanceId>/<stepSlug>/<workerSessionId>` without any
  checkout; a retry on an unchanged base continues the previous step instance's ref tip.
- **Artifact locks.** Function 8 claims `refs/harness/locks/<artifactPath>` create-only
  before a step instance's first write to a path, re-points it on a retry, treats a promoted
  holder's lock as released, and denies a lock another step instance holds, naming its
  workflow instance and step. A write from outside an in-flight step instance with its own
  step ref is denied ("no step in flight"). The clean-baseline check applies to unclaimed
  paths only.
- **Commit gate onto the step ref.** Function 9 commits each validated set onto the step ref
  with a temporary index plus `git commit-tree`; `HEAD`, the working tree, and the workspace's
  index never move. A revert restores from the step ref when the step ref changed the path,
  else from `HEAD`, else deletes it.
- **Promotion.** Function 10 rebases the step ref in memory with `git merge-tree` (a path
  changed on both sides is `step-rebase-conflict`), checks delivery over the step state and
  the worker session's own commits, evaluates the postconditions on the step state, and on
  pass fast-forwards the workspace branch by one compare-and-swap, then syncs the promoted
  paths' index entries and releases the locks. Its report gains `promotion`
  (`{outcome: promoted | withheld, workspaceCommit}`).
- **Function 3** refuses a workflow instance another live orchestrator session drives
  (`workflow-instance-busy`).
- **The internal sweep** (`ArtifactStore.sweep_workspace`) reports step refs, every held
  artifact lock (stale ones included) with its holder, and unlocked working-tree changes.
- **Renames:** `step-session-bound` → `worker-session-bound`; `hostStepSessionKeys` →
  `hostWorkerSessionKeys` (both adapters' `tools.yaml` and the tools contract);
  `find_step_session_ids` → `find_worker_session_ids`; `host_step_session_keys` →
  `host_worker_session_keys`.
- **Requirements:** Git ≥ 2.38, and a Git identity in the governed workspace.

### The condition language (core spec, PR #19)

- `setQuery` / `setPredicate` read `workspace` (artifact instances: `kind`, `path`, `data`),
  `workflows` and `steps` (workflow and step instances from this machine's logs), `workflow`
  and `step` (the current ones), and `selected`. The `artifacts['<kind>']` map and the
  `artifact` constant are removed: a configuration using them fails to load.
- The configuration load type-checks every condition: an `a.data` read needs a kind guard in
  the same macro (`a.kind == '<literal>'`, or a path join with a step instance of a literal
  step of a known workflow). An unguarded read, an unknown kind, step, or workflow slug, an
  undeclared property and, in a precondition, a current-workflow step that is not a declared
  predecessor or a path join with `step` are configuration load errors. Only the kinds the
  guards fix are read from the evaluated state.

### Fixes

- **Function 0 now enforces "one worker session per step resolution" under concurrency.**
  When the same resolved step was dispatched twice in parallel, both worker sessions could
  register, because function 0 checked and then registered in two separate steps. Function
  10 then failed closed. The check and the registration now run as one critical section,
  under an exclusive OS file lock: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows. The
  lock file is `harness-registration.lock` in the workspace repository's Git directory, so
  it is repository metadata, not workspace state, a ref, or a log entry. A second
  concurrent start is refused with `worker-session-bound`.

### Adapters

- **A refused sub-agent's return no longer concludes the step** (live trial T1). When the
  orchestrator dispatched a resolved step twice, the duplicate's registration was refused
  (`worker-session-bound`). Its dispatch return still triggered `check-step-postconditions`,
  which closed the step while the bound worker was still running. Both adapters now record,
  at H1, each sub-agent whose registration was refused or never happened, and H6 skips one
  such return instead of evaluating. The step stays in flight until its bound worker
  returns. The core spec's function 10 trigger and both adapter specs' H6 state the rule.

- **The H5 retry hint names the revert the harness performed.** Both adapters used to say
  "restored from HEAD" for every rejected write. They now render each failing path's revert
  record from the function-9 report: "restored from <step ref>", "restored from HEAD", or
  "deleted". A record that names no source renders without one.

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
    block. It handles both `preToolUse` shapes: the standalone CLI's one call per firing and
    the app's batch, which is denied when any of its calls is. Plugin agents, which the
    runtime names `<plugin>:<agent>`, are governed under their framework agent name. The
    standalone CLI (1.0.105) was verified live with a scripted model. Agent harness commands are hook-executed: the `preToolUse` hook runs one
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
- **Unroutable model catalogue fails closed:** on every hook load, both adapters refuse a
  framework model catalogue (`conf/model-profiles.conf.yaml`) that is missing or names a
  profile slug the adapter's `models.yaml` does not bind. Nothing is cached from install, so
  `models.yaml` must track the models the host currently offers.

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
- **Artifacts by kind:** CEL conditions' `artifacts['<slug>']` now resolves instances by
  artifact kind through the workspace layout (function 5, invariant 2). It no longer treats
  the slug as a path, which found nothing unless a folder happened to share the slug's name.
- **Function 9 commit record:** the `valid` report carries `commitId` and `committedPaths`.
  `commitId` is `null` and the paths are empty when the set was byte-identical to `HEAD`. The
  session log alone now records what each session committed.
- **1 step = 1 session:** function 0 refuses a second step session for a step resolution
  that already registered one, with `state-error` / `step-session-bound`. The refusal is
  returned but not journaled, since the refused session has no log. A failed step recovers
  as before: function 10 concludes it and the orchestrator re-resolves it, and the new
  resolution admits a new step session.
- **Artifact delivery is enforced (breaking contract change):** a step outcome passes only
  if the step's session committed a change to exactly one instance of its declared artifact
  kind through function 9, and every declared postcondition passes (function 10, invariants
  4–5). Zero postconditions no longer pass vacuously. Delivery is a harness invariant, not a
  condition: it has no slug and cannot be configured. Function 10 reads it from the step
  session's log and reports it as `artifactDelivery` in the same journaled entry.
- **`github-copilot-cli`, nested step dispatch:** a step dispatched by an orchestrator that
  is itself a subagent now opens under that orchestrator's session. H1 reads the dispatcher
  from the host-observed `parentAgentId`. The nested `subagentStart` `sessionId` is an
  unrelated id. The scoped H0 of an orchestrator acting a step no longer re-registers the
  step as a root turn, and injects no workflow context into it. Before this fix, the actor's
  commits landed in a stray root session, and function 10 found no delivery.
- **YAML artifacts:** `.yaml`/`.yml` artifacts are read as YAML, no longer with `json.loads`,
  in validation, discovery and CEL data. Artifact YAML, frontmatter included, loads in the
  JSON data model: an unquoted `2026-11-04` stays a string, as JSON Schema 2020-12 expects.
  Malformed YAML is a `parse` finding.
- **`github-copilot-cli`, shell guard (I9):** a relative path token resolves against the
  payload `cwd`, and git pathspecs (`<rev>:<path>`, tokens after `--`) count as path
  references. So `New-Item portfolio\…` run in the workspace, or `git show HEAD:<path>`, is
  denied like an absolute path.
- **Hook-executed H4 attribution:** both adapters pass the tracked parent session with a
  `resolve-step`/`resolve-step-model` request, so a step session's request is journaled under
  its parent instead of `parentSessionId: null`.
- **`github-copilot-cli`, missing folders (H3 host adaptation):** the runtime's `create`
  refuses a missing parent folder, and only GPT-5.x models get `apply_patch`, which creates
  one. Once the harness has allowed a `create` and the whole firing is allowed, the hook
  creates the missing parent folders inside the workspace: folders only, no content, no
  commit. Actors no longer need a shell `mkdir`, which I9 denies.
- **Fail closed on a failed registration (both adapters):** a `start-session` that answers
  an error (`step-session-bound` included) or no report no longer leaves the session
  ungoverned. Only `not-applicable` does. The session is recorded unregistered, and its
  writes, dispatches and harness requests are denied until a registration succeeds.
- **`github-copilot-cli`, review hardening:**
  - H3 folder creation starts at the workspace's real path and refuses any symlink or
    junction under it, so it can never land outside the workspace.
  - `apply_patch` header paths are taken verbatim; an empty or whitespace-padded path makes
    the patch unextractable (denied), as in the VS Code adapter.
  - I9 checks a quoted option value (`--output="workspace/…"`) without its quotes, and no
    longer reads an option such as `--format=%h:%s` as a `<rev>:<path>` object.
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
- **Artifact YAML is JSON data:** YAML-only values (`!!set`, `!!binary`, `.nan`, `.inf`) are a
  `parse` finding, so the write is reverted instead of passing schema validation and failing
  later in CEL.

### Known alpha limitations

- **Supported hosts:** VS Code with GitHub Copilot Chat, and the GitHub Copilot app /
  Copilot CLI. VS Code hooks are a preview feature, so settings and events may drift;
  adapter spec I13 lists the open host verifications.
- **Copilot app / CLI:** the following limits apply.
  - Repository hooks run only in a **trusted folder**; plugin hooks run regardless.
  - A timed-out `preToolUse` **fails open**.
  - **Shell writes** are guarded textually only. The core's commit-set check is the
    backstop.
  - The harness answer to an agent reaches it as a hook denial whose reason starts with
    `SAFe agentic harness RESULT`.
  - `exec` hook entries and scoped hooks inside subagents are not yet byte-verified in the
    app. The standalone CLI was verified live only on Linux, offline with a scripted model.
  - The CLI's non-interactive `copilot -p` offers no `ask_user` (no human gate).
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
