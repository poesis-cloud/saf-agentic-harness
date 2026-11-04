# Adapter — `github-copilot-cli`

The host binding for the **GitHub Copilot CLI agent runtime**, and therefore for the
**GitHub Copilot app**, which embeds the same runtime. This document is the adapter's
canonical specification. It binds the runtime's **hooks** to the harness boundaries, the same
way [`../vscode-github-copilot-chat/spec.md`](../vscode-github-copilot-chat/spec.md) does for
VS Code. The boundary vocabulary (H0–H7), the invariants (I1–I15), and the harness function
contracts are not restated here. They are the VS Code spec's and [`../../core/spec.md`](../../core/spec.md)'s.
This document specifies only what differs for this host. The class model is
[`adapter-src-classes.puml`](adapter-src-classes.puml).

**Why the id `github-copilot-cli`.** The app and the CLI run one runtime, with one hook
engine, one tool vocabulary and one custom-agent/plugin format. The adapter binds that
runtime, not a window around it. A user of the CLI alone installs the same adapter.

## Sources

| Host fact | Source |
| --- | --- |
| Hook events, input/output fields, config format, locations, timeouts, failure semantics | [Hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference), [About hooks](https://docs.github.com/en/copilot/concepts/customizations/hooks) |
| Custom agents (`*.agent.md`, frontmatter `tools`, `model`, `hooks`) | [Custom agents configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration) |
| Plugins (`plugin.json`, `hooks.json`, marketplaces) | [CLI plugin reference](https://docs.github.com/en/copilot/reference/cli-plugin-reference) |
| **Actual** payload shapes, tool names, subagent identity (authoritative where they differ from the docs) | Payloads captured in the GitHub Copilot app on Windows, runtime **1.0.102**: [`tests/functional/adapter_copilot_cli/fixtures/copilot-app-windows-1.0.102.jsonl`](../../../tests/functional/adapter_copilot_cli/fixtures/copilot-app-windows-1.0.102.jsonl) |

## Host facts the design rests on

1. **Config.** A hook file is `{"version": 1, "hooks": {"<event>": [<entry>…]}}`. An entry is
   `type: command` with `bash`/`powershell`/`command`, or `exec` + `args` (no shell). It also
   takes `cwd`, `env` and `timeoutSec`. Files are collected from `.github/hooks/*.json` (the
   repository), `~/.copilot/hooks/*.json` (the user), and an installed plugin's `hooks.json`.
   A custom agent's frontmatter `hooks:` map registers **agent-scoped** hooks, which run only
   while that agent is selected. Each entry there takes `command`, with `windows`/`linux`/`osx`
   overrides.
2. **Output.** Stdout must be ONE JSON object, or empty to pass. `preToolUse` decides through
   `permissionDecision` (`allow`/`deny`/`ask`) and `permissionDecisionReason`, and may replace
   the arguments through `modifiedArgs`. `postToolUse` may replace the result through
   `modifiedResult` (`resultType: "success"`) and add `additionalContext`.
   `userPromptTransformed` may replace the model-facing prompt through
   `modifiedTransformedPrompt`. `sessionStart` and `subagentStart` take `additionalContext`.
   `additionalContext` is capped at 10 KB.
3. **Failure semantics.** A crash or a non-zero exit DENIES at `preToolUse`. A **timeout fails
   OPEN**.
4. **`preToolUse` is BATCHED** (observed; the docs show a single call):
   `{"sessionId", "cwd", "toolCalls": [{"id", "name", "args"}…]}`. One firing may carry several
   calls, and there is no timestamp. `postToolUse` / `postToolUseFailure` are single-call:
   `toolName`, `toolArgs`, `toolResult{resultType, textResultForLlm}` or `error`, plus
   `timestamp`.
5. **Tools (app, 1.0.102).**
   - Files: `view`, `create` (`path`, `file_text`), `edit` (`path`, `old_str`, `new_str`).
   - Shell: `powershell` on Windows, `bash` elsewhere (`command`, `description`).
   - Search: `grep`, `glob`.
   - Dispatch: `task` (`agent_type`, `name`, `description`, `prompt`, optional `model`/`mode`).
   - Also `skill`, `ask_user`, and MCP tools.
   - **No `apply_patch`.** Paths are absolute host paths (`C:\Users\…`).
6. **Subagents.** `subagentStart`/`subagentStop` carry the DISPATCHER's `sessionId` and the
   subagent's `agentId`/`agentType`. Inside the subagent, EVERY event carries
   `sessionId = agentId`, plus `parentAgentId`. Observed order at a step's return: subagent
   `agentStop`, then `subagentStop`, then the dispatcher's `postToolUse(task)`. The `task`
   result text is elided by the runtime.
7. **Background sessions.** The app spawns short title-summarizer sessions in the same folder.
   They fire only the global hooks (`sessionStart`, `userPromptSubmitted`, `agentStop`,
   `sessionEnd`), with prompts such as "Summarize this assistant message".
8. **Folder trust.** Repository hooks and agent-scoped hooks run only in a folder the app
   trusts. In an untrusted folder they silently do not run.

## Hook → boundary binding

| Runtime event (registration) | Boundary | Harness function(s) | Decision on stdout |
| --- | --- | --- | --- |
| `sessionStart` (global) | — (announce) | none | `additionalContext`: framework root, workspace root, the exact harness command lines. Stateless. |
| `userPromptSubmitted` (**agent-scoped**, orchestrators) | **H0** session-started | `start-session` (turn) | none (the runtime ignores this event's output) |
| `userPromptTransformed` (**agent-scoped**, orchestrators) | **H0** context injection | `start-session` if still needed; `resolve-workflow-instructions`, `resolve-workflow-skills` | `modifiedTransformedPrompt` = hint + context + original |
| `subagentStart` (global) | **H1** step-started | `start-session` (step, parent = dispatcher), `resolve-step-instructions`, `resolve-step-skills` | `additionalContext` = hint + step context |
| `preToolUse` · `task` (global) | **H2** step-starting | `check-step-preconditions` | deny on fail; deny `mode: background` |
| `preToolUse` · `create`/`edit`/`str_replace_editor`/`apply_patch` | **H3** write-starting | `check-step-authorization` per target path | deny unless EVERY target of EVERY write in the batch is allowed. An unextractable target is denied. |
| `preToolUse` · `powershell`/`bash` running `harness.py` | **H4** mediated attribution | none (stamp only) | `modifiedArgs.command` = canonical line with `--session-id`; anything else that mentions `harness.py` is denied |
| `preToolUse` · other `powershell`/`bash` | I9 shell guard | none | deny when the command names the workspace or `logs/` |
| `postToolUse` · write tool | **H5** write-ended | `check-step-artifact` | `modifiedResult` + `additionalContext` on revert |
| `postToolUse` / `postToolUseFailure` · `task` | **H6** step-ended | `check-step-postconditions` (dispatcher's session) | `additionalContext` on fail |
| `postToolUse` · any (first one only, if H0 context never landed) | H0 fallback | `resolve-*-instructions/skills` | `additionalContext` with load directives |
| `subagentStop` (global) | **H7** step session ended | `end-session` (step) | none |
| `agentStop` (global) | **H7** turn session ended | `end-session` (the stopping agent's own session) | none |
| `sessionEnd` (global) | **H7** closure | `end-session` for the session and every descendant still open | none |
| `errorOccurred` (global) | diagnostic | none | none (stderr only) |

**Agents never need hook environment.** The agent's shell inherits nothing from a hook's `env`,
so H4 REWRITES the authorized command. The rewritten line has explicit `FRAMEWORK_*`
assignments, the installed interpreter, the installed harness, and the stamped session. This
removes any dependency on user-level variables (`setx`) for agent-run harness commands.

## Session identity

- **Turn session (H0):** `<sessionId>-t<timestamp>`. It is created only by an orchestrator's
  scoped hook, which supplies `--agent <slug>`; nothing in a payload names the selected agent.
  The record is keyed by `sessionId`.
- **Step session (H1):** the subagent's `agentId`, which is exactly the `sessionId` every event
  inside the subagent carries. Attribution at the tool boundary is therefore native: no
  `parentToolCallId` correlation, and no interim exception like the VS Code adapter's I4.
- **Records** live outside every repository, under `SAF_HARNESS_STATE_DIR`, else
  `%LOCALAPPDATA%\saf-agentic-harness\github-copilot-cli`, else
  `$XDG_STATE_HOME/saf-agentic-harness/github-copilot-cli`. A corrupt record is an error:
  `preToolUse` denies.
- **Ungoverned sessions** are those with no record: title summarizers, any session in a folder
  without the orchestrators, and any non-orchestrator agent. Every tool call passes and no
  harness function runs. The one exception is a harness command, which is denied with a
  diagnosis ("is this an orchestrator agent, and is the folder trusted?") instead of failing
  obscurely inside the harness.

## The hooks

Every handler reads the payload through `tools.yaml` keys, so a renamed runtime field is a data
fix. "Pass" means empty stdout and exit 0. The adapter never emits `allow`, so the runtime's
own permission flow still applies to what the harness lets through.

### sessionStart — announce

- **In:** `{source, initialPrompt?, sessionId, timestamp, cwd}`.
- **Out:** `additionalContext` naming the framework root, the governed workspace, and the two
  command lines, rendered for the installed interpreter:
  `& '<python>' '<harness.py>' resolve-step --workflow-slug <workflow-slug>` and
  `… resolve-step-model`.
- **Invariant:** it reads no state and writes none. It fires for summarizer sessions too.

### H0 — userPromptSubmitted / userPromptTransformed (agent-scoped)

- **Pre:** `--agent` is present (scoped hook) and `sessionId` is non-empty. Otherwise pass.
- **Effect:** on the first firing with no record, run `start-session --agent <slug>
  --session-id <sessionId>-t<timestamp>`. On `started`, write the record. On
  `not-applicable` (not an orchestrator per the framework), write nothing and pass.
- **Context:** at `userPromptTransformed`, while `contextInjected` is false, resolve the
  workflow instructions and skills, inline the instruction bodies and add skill load
  directives. Return `modifiedTransformedPrompt` = hint + context + the runtime's transformed
  prompt. Mark the record injected only when both resolutions answered `resolved`.

### H1 — subagentStart

- **Pre:** the dispatcher (`sessionId`) has a record, and `agentId`/`agentType` are present.
  Otherwise pass, which leaves the subagent ungoverned: see *Limited boundaries*.
- **Effect:** `start-session --agent <agentType> --session-id <agentId> --parent-session-id
  <dispatcher session>`. On `started`, record it with `origin: step` and return
  `additionalContext` = hint + step context.

### H2/H3/H4 — preToolUse (one firing, N calls)

1. Unreadable payload (not an object, `toolCalls` not a list, a call without a name) → deny.
2. Each call is classified by `tools.yaml` as dispatch, write, harness command, shell, or
   other. Every call is decided. ANY denial denies the whole firing, and the reasons are joined.
3. **H2:** `mode` in `asyncDispatchModes` → deny. Otherwise `check-step-preconditions`; `fail`
   → deny with the failing conditions.
4. **H3:** extract `(path, action)` targets.
   - `create`/`edit` → `path`.
   - `str_replace_editor` → `path`, with the action refined by `command`; `view` is read-only.
   - `apply_patch` → the `*** Add/Update/Delete File:` and `*** Move to:` headers.

   No target, a non-object argument, or an unparseable patch → **deny (fail closed)**.
   Otherwise run `check-step-authorization` for every target path, relativized to the
   workspace (Windows drive letters and separators are normalized; the comparison is
   case-insensitive). Any `denied` → deny.
5. **H4:** a `powershell`/`bash` command mentioning `harness.py` must FULLMATCH one plain
   invocation:
   `[&] <interpreter> [-3[.x]] <harness.py> <function> [--flag [value]]…`, with no `;`, `|`,
   `&&`, redirection, `$()`, backtick or `$env:`.
   - The interpreter must be any `python`, `python3`, `python3.x` or `py`, bare or a path.
   - The harness path must resolve to the INSTALLED `harness.py`.
   - The function must be `resolve-step` or `resolve-step-model`.
   - `--session-id`/`--parent-session-id` must be absent.

   Anything else → deny, with the exact accepted line in the reason. An accepted command is
   rewritten as `modifiedArgs` (all other arguments are kept):
   - PowerShell: `$env:FRAMEWORK_DIR='…'; …; & '<python>' '<harness>' <function> --session-id <session> <flags>`
   - bash: `FRAMEWORK_DIR='…' … '<python>' '<harness>' <function> --session-id <session> <flags>`

   A harness command batched with other calls is denied, because the runtime takes one
   `modifiedArgs` per firing.
6. **I9:** any other shell command naming the workspace (by name, relative path or absolute
   prefix) or `logs/` → deny.

### H5/H6 — postToolUse / postToolUseFailure

- **H5** runs only on `postToolUse` of a write tool in a governed session:
  `check-step-artifact --artifact-path <each target>`.
  - `valid` → pass.
  - `reverted` → the harness has already restored or deleted the files. Return
    `modifiedResult` ("write rejected: …") and `additionalContext` with the retry directive.
- **H6** runs on `postToolUse` *or* `postToolUseFailure` of `task` in the dispatcher's session:
  `check-step-postconditions`. On `fail`, return `additionalContext` with the failing
  conditions and the "re-resolve" directive. The step's result text is NOT read; the runtime
  elides it.
- **H0 fallback:** if the session's context never landed (no `userPromptTransformed` firing),
  the first `postToolUse` appends it as short load directives, which fit the 10 KB cap.

### H7 — agentStop / subagentStop / sessionEnd

- `agentStop` ends the stopping agent's own session (`sessionId`): a turn, or a step.
- `subagentStop` ends the step session named by `agentId`. This is idempotent with the
  subagent's own `agentStop`.
- `sessionEnd` ends the session and every recorded descendant.
- All three always pass. A failed `end-session` is a diagnostic only, and the C6 commit-set
  remediation in the core is the backstop.

## Fail-closed entry

`adapter.py hook` reads stdin as UTF-8, with or without a BOM.

- **Bootstrap failure** (as for every adapter): any failure escaping the hook handling exits
  **2** with a stderr diagnostic. This covers:
  - a missing or non-directory framework root (`--framework-dir`, else `FRAMEWORK_DIR`);
  - an unreadable or malformed `<fw>/.env` (parsed exactly as the core `EnvLoader` does);
  - an off-contract or empty-probe binding (`adapters/binding_contract.py`);
  - an unparsable payload;
  - an unexpected crash.

  The runtime turns exit 2 into a **deny** at `preToolUse` and into a user-visible warning at
  every other event. That is the right severity, because only `preToolUse` can veto. Exit 2 is
  chosen even for a session without a record, because a broken bootstrap is exactly what keeps
  H0 from creating that record. A broken install therefore blocks tool use in every folder
  where the plugin's hooks fire until it is repaired. That is visible and fail-closed, never
  silent.
- **A readable but malformed tool-call batch** in a governed session is denied with a reason.
- **Harness failures** (no report, non-zero exit, stderr) become fixed text such as
  "check-step-authorization unavailable" and deny the write. The raw text goes to stderr only.
  Error reports (`inquiry-error`, `state-error`, `system-error`) deny, and the core's own
  `code — message` is relayed, as in the VS Code adapter.

## Rendered registration — `install.py`

The source of truth is [`hooks.yaml`](../../../adapters/github-copilot-cli/hooks.yaml) (global)
and [`agent-hooks.yaml`](../../../adapters/github-copilot-cli/agent-hooks.yaml) (scoped H0).
`install.py` renders both, with absolute machine paths. The output is generated and never
committed.

| Option | Meaning |
| --- | --- |
| `--framework-dir` (required) | The framework **checkout** (e.g. `saf-agentic-organization`, holding `.env`, `workflows/`, `instructions/`, `skills/`, `workspace/`), NOT the rendered bundle. Every hook's `cwd` and `FRAMEWORK_DIR`. |
| `--workspace-dir` | Override `FRAMEWORK_WORKSPACE_DIR` (absolute) — e.g. a portfolio repository outside the checkout. Written into the global hooks' `env` AND passed as `--workspace-dir` (scoped hooks cannot carry `env`). |
| `--bundle-dir` | The framework's rendered Copilot plugin bundle. Global hooks go to `<bundle>/hooks.json` (or the path a string `hooks` key in `plugin.json` names). The scoped H0 block goes into `<bundle>/agents/<orchestrator>.agent.md`. |
| `--hooks-target plugin\|repo\|user\|none` | Where the global hook file goes: the bundle (default with `--bundle-dir`), `<target-repo>/.github/hooks/safe-harness-copilot-cli.json`, or `$COPILOT_HOME`/`~/.copilot/hooks/…`. |
| `--python` | The absolute interpreter recorded in every entry (`exec`, and `SAF_HARNESS_PYTHON` in `env`). Defaults to the one running `install.py`, which should be the harness venv. The install is refused unless it imports the harness requirements (`yaml`, `jsonschema`, `celpy`). |
| `--hook-style exec\|shell` | `exec` + `args` (default: no shell at all), or `powershell` + `bash` command strings. |
| `--agents-dir`, `--agents-dest`, `--workflows-dir`, `--skip-agents`, `--dry-run` | As named. |

- **Global entry:** `type: command`, `exec: <python>`,
  `args: [<adapter.py>, hook, --event, <e>, --framework-dir, <fw>, (--workspace-dir, <ws>)]`,
  `cwd: <fw>`, and `env` holding `FRAMEWORK_DIR` plus every `FRAMEWORK_*_DIR` (from
  `<fw>/.env`, overridden by the process environment and `--workspace-dir`),
  `SAF_HARNESS_PYTHON=<python>`, `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, and `timeoutSec`.
  The hook therefore bootstraps from its registration alone, because the app runs hooks with
  its own environment and not the operator's shell (no `setx`).
- **Scoped block:** inserted inside the frontmatter, between the managed markers
  `# >>> safe-harness copilot-cli H0 … >>>` and `# <<< safe-harness copilot-cli H0 <<<`.
  It has `command` (POSIX-quoted) and `windows` (`& '<python>' …`) with `--agent <slug>`.
  Re-running replaces the block. An unmanaged `hooks:` key is refused.
- **Orchestrators** are every `orchestrator:` named by `<fw>/workflows/**/*.workflow.conf.yaml`.
  A missing agent file refuses the install.
- **All or nothing:** every output is rendered and validated before any file is written.

## Limited or unsupported boundaries, and their fallbacks

| Boundary / guarantee | Limitation in this runtime | Fallback |
| --- | --- | --- |
| Hook timeout | A timed-out `preToolUse` **fails open**. | Generous `timeoutSec` (120 s) on gating events. Harness calls are local and fast. H5 still validates and reverts after the fact. |
| Shell writes (I9) | `powershell`/`bash` is an unmediated write channel: the guard is textual and advisory (a variable or an encoded command evades it). | Workspace paths named in a shell command are denied. H5 does not see shell writes, so the core's commit-set check (C6) at the next `check-step-artifact`/`end-session` is the backstop. Document "write through `create`/`edit`" in the agents. |
| Folder trust | Repository and agent-scoped hooks do not run in an untrusted folder. H0 never fires, so the session stays ungoverned. | Install prerequisite: trust the framework folder. Prefer plugin hooks (global boundaries) over repository hooks. A harness command in an ungoverned session is denied with a diagnosis. |
| H0 inside a subagent | It is not verified whether agent-scoped hooks fire for an orchestrator dispatched as a subagent. | H1 opens the step session from the global `subagentStart` anyway. The hint is part of every governed context. |
| H1 `not-applicable` | A subagent whose dispatcher is ungoverned, or a harness `not-applicable` answer, leaves the subagent ungoverned (parity with VS Code). | C6 commit-set remediation. |
| `modifiedArgs` semantics | It is not verified whether `modifiedArgs` without `permissionDecision` is honored, or how it applies to a batch. | A harness command must be alone in its batch. If `modifiedArgs` were ignored, the original command runs unattributed and the harness refuses it for the missing `--session-id`, which fails closed. |
| `apply_patch` | Not offered by the app (1.0.102). | Bound anyway. Unparseable patch → deny. |
| `task` `mode: background` | The step returns before it ends, so H6 cannot evaluate it. | Denied in governed sessions. |
| H6 enforcement | `postToolUse` can only inform, not undo, a returned step. | The `additionalContext` directive to re-resolve. Future: `subagentStop` `decision: block` + `reason` can force the subagent to continue. |
| `exec` entries | Documented, but not yet observed executing in the app. | `--hook-style shell` renders `powershell`/`bash` strings with the same arguments. |
| Latency | Plugin hooks start Python on every tool call in every folder. | Ungoverned firings return after one state lookup. Use a repository or project-scoped install when this matters. |

## Inconsistencies found while binding this host

- **Artifact schema `$id`.** Framework artifact schemas `$ref`
  `gsmarc://saf/harness/contracts/artifact/v1`, while the harness publishes
  `contracts/artifact.schema.json` as `gsmarc://saf/contracts/artifact/v1`.
  `check-step-artifact` then fails with `Unresolvable`, which H5 relays as "unavailable". This
  is a cross-repository defect, outside this adapter.
- The shared `adapters/dispatch.sh` is a bash script. This adapter does not use it: every entry
  runs `adapter.py` through an explicit interpreter, so Windows needs no bash. See
  [`../../../adapters/README.md`](../../../adapters/README.md).
