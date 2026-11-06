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
runtime, not a window around it. A user of the CLI alone installs the same adapter. Nothing in
the binding is app-only: the facts below that were observed to differ between the standalone
CLI and the app are host-conditional, and the adapter accepts both forms (each is
replayed from a live capture in the process tests).

## Sources

| Host fact | Source |
| --- | --- |
| Hook events, input/output fields, config format, locations, timeouts, failure semantics | [Hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference), [About hooks](https://docs.github.com/en/copilot/concepts/customizations/hooks) |
| Custom agents (`*.agent.md`, frontmatter `tools`, `model`, `hooks`) | [Custom agents configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration) |
| Plugins (`plugin.json`, `hooks.json`, marketplaces) | [CLI plugin reference](https://docs.github.com/en/copilot/reference/cli-plugin-reference) |
| **Actual** payload shapes, tool names, subagent identity (authoritative where they differ from the docs) | Payloads captured in the GitHub Copilot app on Windows, runtime **1.0.102**: [`tests/functional/adapter_copilot_cli/fixtures/copilot-app-windows-1.0.102.jsonl`](../../../tests/functional/adapter_copilot_cli/fixtures/copilot-app-windows-1.0.102.jsonl) |
| The same, on the **standalone CLI** (Linux, `copilot -p`, framework bundle loaded with `--plugin-dir`, runtime **1.0.105**) | [`tests/functional/adapter_copilot_cli/fixtures/copilot-cli-linux-1.0.105.jsonl`](../../../tests/functional/adapter_copilot_cli/fixtures/copilot-cli-linux-1.0.105.jsonl) |

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
   `postToolUse`'s `additionalContext` is capped at 10 KB (joined across hooks). A
   `sessionStart`/`subagentStart` `additionalContext` is bounded only by the 10 MiB
   hook-output limit, so H1 inlines its step context whole.
3. **Failure semantics.** A crash or a non-zero exit DENIES at `preToolUse`. A **timeout fails
   OPEN**.
4. **`preToolUse` shape is host-conditional.**
   - Standalone CLI (1.0.105): the documented SINGLE-call shape
     `{"sessionId", "timestamp", "cwd", "toolName", "toolArgs"}`, one firing per tool call,
     even for parallel calls.
   - App (1.0.102): BATCHED, `{"sessionId", "cwd", "toolCalls": [{"id", "name", "args"}…]}`.
     One firing may carry several calls, and there is no timestamp.
   - The binding reads both (`toolKeys`/`inputKeys` and `toolCallsKeys`). `postToolUse` /
     `postToolUseFailure` are single-call in both: `toolName`, `toolArgs`,
     `toolResult{resultType, textResultForLlm}` or `error`, plus `timestamp`.
5. **Tools.**
   - Files: `view`, `create` (`path`, `file_text`), `edit` (`path`, `old_str`, `new_str`).
   - Shell: `powershell` on Windows, `bash` elsewhere (`command`, `description`), plus
     `read_bash`/`stop_bash`/`list_bash` on the CLI.
   - Search: `grep`, `glob`.
   - Dispatch: `task` (`agent_type`, `name`, `description`, `prompt`, optional `model`/`mode`).
   - Also `skill`, MCP tools, and `ask_user` in an interactive session. The standalone CLI's
     non-interactive prompt mode (`copilot -p`) offers **no `ask_user`** (observed, 1.0.105).
   - The file tools depend on the MODEL (observed, CLI 1.0.105). Claude-family models get
     `create`/`edit`; `create` refuses a missing parent folder ("Parent directory … does not
     exist. You need to create it before creating the file"), and `edit` needs an existing
     file. GPT-5.x models get no `create`/`edit` but a freeform `apply_patch`, whose
     `toolArgs` is the raw patch string; its `*** Add File:` creates missing folders.
   - Paths are absolute host paths (`C:\Users\…`, `/home/…`).
6. **Subagents.** `subagentStart`/`subagentStop` carry the subagent's
   `agentId`/`agentType`/`agentName` and, in `parentAgentId`, the DISPATCHER's id. For a
   first-level subagent `sessionId` is the dispatcher too. For a subagent dispatched BY a
   subagent, `sessionId` is an id no other event carries (observed, app 1.0.102): only
   `parentAgentId` names the dispatcher. Inside the subagent, EVERY event carries
   `sessionId = agentId`, plus `parentAgentId`. `subagentStart` fires before the subagent's
   first `userPromptSubmitted`. Observed order at a step's return (both hosts):
   subagent `agentStop`, then `subagentStop`, then the dispatcher's `postToolUse(task)`. The
   `task` result text is elided by the runtime.
   **Plugin agents are namespaced** `<plugin-name>:<agent>`: in `task` `agent_type` (a bare
   name is refused as "Unknown agent_type"), in `agentType`/`agentName`, and in `--agent`.
   Only `agentDisplayName` is bare. The binding's `hostAgentNamespaceSeparator: ":"` makes H1
   open the worker session for the framework agent (the part after the last `:`), never for
   the namespaced id. Repository agents (`.github/agents/`) carry no namespace.
7. **Background sessions (app only).** The app spawns short title-summarizer sessions in the
   same folder. They fire only the global hooks (`sessionStart`, `userPromptSubmitted`,
   `agentStop`, `sessionEnd`), with prompts such as "Summarize this assistant message". None
   were observed in the standalone CLI. Either way they are ungoverned pass-throughs.
8. **Folder trust (both hosts).** Repository hooks (`.github/hooks/`) load only in a trusted
   folder: the app's trust, or the CLI's trust prompt / `--trust-folder`. In an untrusted
   folder they silently do not run. Plugin hooks and a plugin agent's scoped hooks DO run in
   an untrusted folder (observed, standalone CLI 1.0.105, `-p`).

## Hook → boundary binding

| Runtime event (registration) | Boundary | Harness function(s) | Decision on stdout |
| --- | --- | --- | --- |
| `sessionStart` (global) | — (announce) | none | `additionalContext`: framework root, workspace root, the exact harness command lines. Stateless. |
| `userPromptSubmitted` (**agent-scoped**, orchestrators) | **H0** session-started | `start-session` (turn); skipped in a worker session and for an agent a governed session dispatched | none (the runtime ignores this event's output) |
| `userPromptTransformed` (**agent-scoped**, orchestrators) | **H0** context injection | `start-session` if still needed; `resolve-workflow-instructions`, `resolve-workflow-skills` | `modifiedTransformedPrompt` = hint + context + original |
| `subagentStart` (global) | **H1** step-started | `start-session` (step, parent = dispatcher: `parentAgentId`, else `sessionId`), `resolve-step-instructions`, `resolve-step-skills` | `additionalContext` = hint + step context |
| `preToolUse` · `task` (global) | **H2** step-starting | `check-step-preconditions` | deny on fail; deny `mode: background` |
| `preToolUse` · `create`/`edit`/`str_replace_editor`/`apply_patch` | **H3** write-starting | `check-step-authorization` per target path | deny unless EVERY target of EVERY write in the batch is allowed. An unextractable target is denied. |
| `preToolUse` · `powershell`/`bash` naming `harness.py` | **H4** agent harness request (hook-executed) | `resolve-step` / `resolve-step-model`, run BY the hook for the observed session | deny whose reason is `SAFe agentic harness RESULT (<fn>): …` + the report JSON; the shell never runs the line. Anything else that mentions `harness.py` → deny `SAFe agentic harness REJECTED: …`. Never `modifiedArgs`. |
| `preToolUse` · other `powershell`/`bash` | I9 shell guard | none | deny when the command names the workspace or `logs/` |
| `postToolUse` · write tool | **H5** write-ended | `check-step-artifact` | `modifiedResult` + `additionalContext` on revert |
| `postToolUse` / `postToolUseFailure` · `task` | **H6** step-ended | `check-step-postconditions` (dispatcher's session) | `additionalContext` on fail |
| `postToolUse` · any (first one only, if H0 context never landed) | H0 fallback | `resolve-*-instructions/skills` | `additionalContext` with load directives |
| `subagentStop` (global) | **H7** worker session ended | `end-session` (step) | none |
| `agentStop` (global) | **H7** turn session ended | `end-session` (the stopping agent's own session) | none |
| `sessionEnd` (global) | **H7** closure | `end-session` for the session and every descendant still open | none |
| `errorOccurred` (global) | diagnostic | none | none (stderr only) |

**The adapter never edits an agent's command.** An agent's harness command is a REQUEST:
the `preToolUse` hook runs the function itself, under the hook's own `env` (the same
`FRAMEWORK_*` layout every other boundary uses), and answers with the report. Agent shells
therefore need no `FRAMEWORK_*` variables (no `setx`), and the host's permission layer is never
consulted for a harness command, because a hook deny comes before it: no approval rule is
needed, also in autopilot.

## Session identity

**Mapping to the core's sessions.** The core defines its two session kinds host-agnostically —
an **orchestrator session** drives at most one workflow instance, a **worker session**
executes exactly one step instance — and leaves the mapping from host signals to each
adapter. On this host: an orchestrator session (the turn session below) opens at each user
prompt to a framework orchestrator (H0) and lasts until that orchestrator returns to the
user; a worker session opens at each subagent dispatch (H1) and lasts until the subagent
returns to the orchestrator.

- **Turn session (H0):** `<sessionId>-t<timestamp>`. It is created only by an orchestrator's
  scoped hook, which supplies `--agent <slug>`; nothing in a payload names the selected agent.
  The record is keyed by `sessionId`.
- **Worker session (H1):** the subagent's `agentId`, which is exactly the `sessionId` every event
  inside the subagent carries. Attribution at the tool boundary is therefore native: no
  `parentToolCallId` correlation is needed.
- **Records** live outside every repository, under `SAF_HARNESS_STATE_DIR`, else
  `%LOCALAPPDATA%\saf-agentic-harness\github-copilot-cli`, else
  `$XDG_STATE_HOME/saf-agentic-harness/github-copilot-cli`. A corrupt record is an error:
  `preToolUse` denies.
- **Unregistered sessions** have a record whose `start-session` answered an error or no
  report. Only an explicit `not-applicable` means ungoverned (core rule 2), so these fail
  closed: every write, dispatch and harness request is denied with the recorded failure, I9
  still guards the shell, H5/H6 do not run, and closure deletes the record without
  `end-session` (no harness session exists). A turn retries at its next prompt firing.
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
  `& <python> <harness.py> resolve-step --workflow-slug <workflow-slug>` and
  `… resolve-step-model` (a path is quoted only when it holds a space or a shell character).
  It also states that the shell does not run the line, that the answer arrives as a hook
  denial starting `SAFe agentic harness RESULT` which is the successful result to act on
  (never to re-issue), and that only `SAFe agentic harness REJECTED` means a refused request.
- **Invariant:** it reads no state and writes none. It fires for summarizer sessions too.

### H0 — userPromptSubmitted / userPromptTransformed (agent-scoped)

- **Pre:** `--agent` is present (scoped hook) and `sessionId` is non-empty. Otherwise pass.
- **Worker sessions are H1's.** An orchestrator agent acting a step still fires its scoped hook.
  When the record for `sessionId` is a step (`origin: step`), H0 passes: no `start-session`,
  no workflow context. When there is no record but `parentAgentId` names a session that has
  one, the agent was opened by a governed session's dispatch (core: `parentSessionId` is "the
  session whose action opened it"), so it is never a root turn: H0 passes with a diagnostic
  and the agent stays ungoverned (H1 answered `not-applicable` for it, or never fired; an
  H1 error leaves an unregistered step record instead, so H0 never reaches this case).
- **Effect:** on the first firing with no record, run `start-session --agent <slug>
  --session-id <sessionId>-t<timestamp>`. On `started`, write the record. On
  `not-applicable` (not an orchestrator per the framework), write nothing and pass. On any
  other answer (an error report, or no report) write an UNREGISTERED record carrying the
  failure: the session is not ungoverned, its writes and dispatches are denied (see
  *Unregistered sessions*), and the next prompt firing retries the registration.
- **Context:** at `userPromptTransformed`, while `contextInjected` is false, resolve the
  workflow instructions and skills, inline the instruction bodies and add skill load
  directives. Return `modifiedTransformedPrompt` = hint + context + the runtime's transformed
  prompt. Mark the record injected only when both resolutions answered `resolved` and the
  context was delivered whole (see *Context size* below).

### H1 — subagentStart

- **Pre:** the dispatcher has a record, and `agentId`/`agentType` are present. The dispatcher is
  the host-observed `parentAgentId`, falling back to `sessionId` when it is absent; `sessionId`
  alone is wrong for a nested subagent (host fact 6).
  Otherwise pass, which leaves the subagent ungoverned: see *Limited boundaries*.
- **Effect:** `start-session --agent <agentType> --session-id <agentId> --parent-session-id
  <dispatcher session>`. On `started`, record it with `origin: step` and return
  `additionalContext` = hint + step context. On `not-applicable` (a foreign or non-framework
  dispatch), record nothing and pass. On any other answer, `worker-session-bound` included,
  or when the dispatcher's own record is unregistered, record the step UNREGISTERED
  (`origin: step`) and pass.
- **Context size:** the runtime caps only `postToolUse`'s `additionalContext` at 10 KB; H0's
  `modifiedTransformedPrompt` and H1's `additionalContext` are bounded by the 10 MiB
  hook-output limit. Instruction bodies are inlined when the output fits that limit, else
  rendered as load directives; a context that fits neither is not injected. The record is
  marked injected only when its whole context was delivered, so the H0 fallback (or the
  next firing) still delivers what is missing. The fallback marks it only when its
  directives fit the 10 KB cap whole.

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
     Lines split on `\n` only and header paths are taken verbatim, as the host writes
     them; an empty path, one with surrounding whitespace (a CR included), or a
     header-like line not in the canonical form (`*** <Kind> File:` plus one space) makes the patch
     unextractable.

   No target, a non-object argument, or an unparseable patch → **deny (fail closed)**.
   Otherwise run `check-step-authorization` for every target path, relativized to the
   workspace (Windows drive letters and separators are normalized; the comparison is
   case-insensitive). Any `denied` → deny. Function 8 claims each allowed path's artifact
   lock for the acting step instance, and denies a write made outside an in-flight step
   instance — from a turn (orchestrator) session — or to a path another step instance's lock
   holds (core function 8, invariants 7–8).

   **Host adaptation (missing folders).** `create` refuses a missing parent folder and no
   tool every model gets creates one, so an actor would fall back to a shell `mkdir`, which
   I9 denies. When the WHOLE firing is allowed, the hook creates the missing parent folders
   of each `create`/`str_replace_editor create` target the harness answered `allowed`,
   resolved against `cwd`, only inside the workspace and only for a path of the local OS
   flavor. Containment is enforced on the real filesystem, not only lexically: creation
   starts at the workspace's real path and goes one folder at a time, and any component that
   is a symlink, a junction or not a folder refuses it (a link could carry `mkdir` outside the
   workspace). It writes folders only: no content, no commit (git does not track empty folders),
   and the write itself still goes through the tool, H5 and the core. Patch targets are
   skipped (`*** Add File:` creates its own). A folder that cannot be created is a hook
   diagnostic; the tool then fails as it would have.
5. **H4 (hook-executed):** a `powershell`/`bash` command mentioning `harness.py` is a request
   to the hook, never a command to run. It must FULLMATCH one plain invocation:
   `[&] <interpreter> [-3[.x]] <harness.py> <function> [--flag value]…`, with no `;`, `|`,
   `&&`, redirection, `$()`, backtick or `$env:`.
   - The interpreter must be any `python`, `python3`, `python3.x` or `py`, bare or a path.
   - The harness path must resolve to the INSTALLED `harness.py`.
   - A quoted path takes exactly the forms the hint renders for the host's shell, decoded
     by that shell's rules: PowerShell `'…'` with an apostrophe doubled (`'o''neil'`),
     POSIX `'…'` with an apostrophe as `'"'"'`, or `"…"` in either.
   - The function must be `resolve-step` or `resolve-step-model`.
   - `--session-id`/`--parent-session-id` must be absent.
   - Every flag takes one value, given once.
   - It must be the only call in its batch.

   Anything else → deny, reason `SAFe agentic harness REJECTED: …` with the accepted line,
   and no harness function runs. An accepted request is RUN BY THE HOOK: the agent's flags
   become the inquiry (`--workflow-slug x` → `workflowSlug: x`), `sessionId` is the session
   the record for the payload's `sessionId` names (the turn session, or the worker session in
   a subagent), `parentSessionId` is the parent that record tracks (`null` for a turn
   session, the orchestrator session that H1 observed opening a worker session), and the
   function runs through the same command runner, interpreter and
   `FRAMEWORK_*` environment as every other boundary. The hook then answers
   `permissionDecision: deny` with the reason
   `SAFe agentic harness RESULT (<function>): <directive>` + a blank line + the report as the
   harness prints it (compact JSON). The directive says this is the successful answer, that
   the hook ran the function for the session and the shell did not, and that the agent must
   act on it and not re-issue the command. An error report is a result like any other. A
   harness refusal on its exit plane (a last stderr line `harness: …`, e.g. an unknown flag)
   is relayed as `REJECTED: harness: …`; any other failure becomes the fixed text "the
   harness could not answer", with the raw output on stderr only. `modifiedArgs` is never
   emitted (the stdout contract has no such shape).

   **Why a deny reason.** For `preToolUse`, the runtime takes only `permissionDecision`,
   `permissionDecisionReason` ("reason shown to the agent") and `modifiedArgs`; it has no
   `additionalContext`. `modifiedResult` exists only on `postToolUse`, which needs the tool
   to have run. So the deny reason is the only channel that returns text to the agent
   without editing or running its command. It is also immune to the permission layer and to
   autopilot's unattended denials, because the hook decides first.
6. **I9:** any other shell command naming the workspace (by name, relative path or absolute
   prefix) or `logs/` → deny. Read-only commands are included.
   - A relative path token resolves against the payload's `cwd`. A token counts as a path
     when it has a separator or a leading `.`, when it exists, or when it follows `--`.
   - Git pathspecs are path references: every token after `--`, and the `<path>` of a
     `<rev>:<path>` object name (e.g. `git show HEAD:<path>`), resolved from the workspace
     when `cwd` is inside it.
   - The value of a `-option=value` token is checked like a token.

### H5/H6 — postToolUse / postToolUseFailure

- **H5** runs only on `postToolUse` of a write tool in a governed session:
  `check-step-artifact --artifact-path <each target>`.
  - `valid` → pass. The set is committed onto the step instance's step ref, never onto the
    workspace branch: it enters workspace state only when H6's function 10 promotes the
    step instance.
  - `reverted` → the harness has already restored the files (from the step ref tip or
    `HEAD`) or deleted them. Return
    `modifiedResult` ("write rejected: …") and `additionalContext` with the retry directive.
  - any other status (an error report) → `modifiedResult` ("write outcome unknown: …") and
    `additionalContext` naming the error. Its commit and restoration state are both unknown
    (an error such as `log-append-failed` can follow the commit, since the harness commits
    before it journals), so neither is claimed: the actor is directed to inspect the
    artifact before retrying.
- **H6** runs on `postToolUse` *or* `postToolUseFailure` of `task` in the dispatcher's session:
  `check-step-postconditions`. It is also the acceptance point: on `pass`, function 10
  promotes the step instance (the workspace branch fast-forwards to the evaluated step state,
  its locks are released); on `fail`, workspace state is untouched. On `fail`, return
  `additionalContext` with the failing
  conditions, then `[artifact delivery] fail — <artifactDelivery.failureMessage>` when
  delivery failed, and the "re-resolve" directive. The step's result text is NOT read; the runtime
  elides it.
- **H0 fallback:** if the session's context never landed (no `userPromptTransformed` firing),
  the first `postToolUse` appends it as short load directives, which fit the 10 KB cap.

### H7 — agentStop / subagentStop / sessionEnd

- `agentStop` ends the stopping agent's own session (`sessionId`): a turn, or a step.
- `subagentStop` ends the worker session named by `agentId`. This is idempotent with the
  subagent's own `agentStop`.
- `sessionEnd` ends the session and every recorded descendant.
- All three always pass. A failed `end-session` is a diagnostic only, and the core's C6
  detect-and-remediate plane is the backstop.

## Fail-closed entry

`adapter.py hook` reads stdin as UTF-8, with or without a BOM.

- **Bootstrap failure** (as for every adapter): any failure escaping the hook handling exits
  **2** with a stderr diagnostic. This covers:
  - a missing or non-directory framework root (`--framework-dir`, else `FRAMEWORK_DIR`);
  - an unreadable or malformed `<fw>/.env` (parsed exactly as the core `EnvLoader` does);
  - an off-contract or empty-probe binding (`adapters/binding_contract.py`);
  - a framework model catalogue (`<fw>/conf/model-profiles.conf.yaml`) that is missing,
    unreadable, or names a profile slug `models.yaml` does not bind (see
    [Model binding](#model-binding));
  - an unparsable payload;
  - an unexpected crash.

  The runtime turns exit 2 into a **deny** at `preToolUse` and into a user-visible warning at
  every other event. That is the right severity, because only `preToolUse` can veto. Exit 2 is
  chosen even for a session without a record, because a broken bootstrap is exactly what keeps
  H0 from creating that record. A broken install therefore blocks tool use in every folder
  where the plugin's hooks fire until it is repaired. That is visible and fail-closed, never
  silent.
- **A readable but malformed tool-call batch** in a governed session is denied with a reason.
- **Harness failures** (no report, non-zero exit, stderr, a call stopped at the hook
  deadline) become fixed text such as
  "check-step-authorization unavailable" and deny the write. The raw text goes to stderr only.
  Error reports (`inquiry-error`, `state-error`, `system-error`) deny, and the core's own
  `code — message` is relayed, as in the VS Code adapter.

## Model binding

[`models.yaml`](../../../adapters/github-copilot-cli/models.yaml) maps every model-profile slug
to a model id this host offers. On **every** hook load, the bootstrap reads the framework model
catalogue (`<fw>/conf/model-profiles.conf.yaml`) and refuses a catalogue that names a profile
slug `models.yaml` does not bind (core function 4: unroutable configurations die at load). The
refusal is a bootstrap failure (exit 2, the unbound slugs listed on stderr), so `preToolUse`
denies. A missing or unreadable catalogue is refused the same way. Nothing is cached from
install time: the host's model list can change after install, so `models.yaml` must track
the models the host currently offers, and the check re-reads both files on each firing. The
runtime passes no model list to hooks (none in any captured payload), so the adapter cannot
check `models.yaml` against the live host list itself.

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
  The hook therefore bootstraps from its registration alone, because the host runs hooks with
  its own environment and not the operator's shell (no `setx`).
- **Scoped block:** inserted inside the frontmatter, between the managed markers
  `# >>> safe-harness copilot-cli H0 … >>>` and `# <<< safe-harness copilot-cli H0 <<<`.
  It has `command` (POSIX-quoted) and `windows` (`& '<python>' …`) with `--agent <slug>`.
  Scoped hooks cannot carry `env`, so every resolved `FRAMEWORK_*_DIR` (the same values the
  global entries' `env` holds, environment overrides included) is passed as
  `--layout FRAMEWORK_<X>_DIR=<value>`; the adapter refuses (exit 2) any other `--layout`
  variable. H0 therefore opens the session in the layout every other boundary uses.
  Re-running replaces the block. An unmanaged `hooks:` key is refused.
- **Orchestrators** are every `orchestrator:` named by `<fw>/workflows/**/*.workflow.conf.yaml`.
  A missing agent file refuses the install.
- **All or nothing:** every output is rendered and validated before any file is written.
  The outputs are then staged next to their destinations and replace them one by one; a
  failed write restores every destination already replaced (or removes it, if it is new)
  and is reported as a refusal, so the previous registration stays intact.

## Limited or unsupported boundaries, and their fallbacks

| Boundary / guarantee | Limitation in this runtime | Fallback |
| --- | --- | --- |
| Hook timeout | A timed-out `preToolUse` **fails open**. | Generous `timeoutSec` (120 s) on gating events. Every harness call of a firing is bounded by a deadline inside the event's registered `timeoutSec` (by 25 %, at least 5 s): a call that would start or run past it is stopped, its whole process tree killed (git children included: a process group on POSIX, `taskkill /T` on Windows) so no commit or revert outlives it, and is a harness failure, so the boundary DENIES while the host still honors the answer. Only a stall before the adapter runs (interpreter start) can still reach the host timeout. H5 still validates and reverts after the fact. |
| Shell writes (I9) | `powershell`/`bash` is an unmediated write channel: the guard is textual and advisory (a variable or an encoded command evades it). | Workspace paths named in a shell command are denied. H5 does not see shell writes, so the core bounds them: their bytes land only in the working tree, never in a commit or in workspace state (only promotion advances it), and a dirtied unclaimed path fails function 8's staging-baseline check; C6's detect-and-remediate plane owns cleanup. Document "write through `create`/`edit`" in the agents. |
| Folder trust | Repository hooks do not run in an untrusted folder (both hosts); the boundaries they register never fire, so the session stays ungoverned. | With `--hooks-target repo`, trust the folder (`--trust-folder` on the CLI). Prefer plugin hooks, which run regardless. A harness command in an ungoverned session is denied with a diagnosis. |
| H0 inside a subagent | Agent-scoped hooks DO fire for an orchestrator dispatched as a subagent (observed, app 1.0.102), after `subagentStart`. | H0 leaves a worker session to H1 and never opens a root for an agent a governed session dispatched. The hint is part of every governed context. |
| H1 `not-applicable` | A subagent whose dispatcher is ungoverned, or a harness `not-applicable` answer, leaves the subagent ungoverned (parity with VS Code). An error answer does not: the subagent is recorded unregistered and its writes and dispatches are denied. | C6 detect-and-remediate plane. |
| H4 result channel | The harness answer reaches the agent as a hook DENIAL of its shell call. On the standalone CLI (1.0.105) the model receives the tool result `Denied by preToolUse hook: <reason>`, the reason verbatim; the app's wrapper is not byte-verified. A model may read "denied" as failure and retry. | The reason starts with `SAFe agentic harness RESULT` and says it is the successful answer; the injected hint and the org's orchestrator prose say the same. A re-issued request is answered again (resolve functions do not advance on their own), so a retry costs a turn but changes nothing. |
| H4 on hook timeout | A timed-out `preToolUse` fails open, so the agent's own line would run in its shell. | That line has no `--session-id` and the agent shell has no `FRAMEWORK_DIR`: the harness refuses it at its exit plane (`FRAMEWORK_DIR is unset`, exit 2) and nothing is journaled. |
| `apply_patch` | Offered instead of `create`/`edit` to GPT-5.x models only (CLI 1.0.105); `toolArgs` is the raw patch string. | Bound as a patch tool: every header target is authorized; an unparseable patch → deny. |
| Missing parent folders | `create` refuses a missing parent folder; `edit` needs an existing file. | Host adaptation at H3: the hook creates the folders of an allowed `create` target inside the workspace (folders only, no commit). |
| `task` `mode: background` | The step returns before it ends, so H6 cannot evaluate it. | Denied in governed sessions. |
| H6 enforcement | `postToolUse` can only inform, not undo, a returned step. | The `additionalContext` directive to re-resolve. Future: `subagentStop` `decision: block` + `reason` can force the subagent to continue. |
| `exec` entries | Observed executing in the standalone CLI (1.0.105, plugin `hooks.json`); not yet observed in the app. | `--hook-style shell` renders `powershell`/`bash` strings with the same arguments. |
| Plugin agent names | The runtime namespaces plugin agents `<plugin>:<agent>`. | `hostAgentNamespaceSeparator` strips the namespace before `start-session`. Agents must dispatch the namespaced `agent_type`; that is the framework bundle's prose, not the adapter's. |
| Human gate in `copilot -p` | Non-interactive prompt mode offers no `ask_user`. | None in the adapter: a framework whose steps need a human gate must run interactively. |
| Latency | Plugin hooks start Python on every tool call in every folder. | Ungoverned firings return after one state lookup. Use a repository or project-scoped install when this matters. |

## Mediated-attribution status

Functions 3–4 (`resolve-step`, `resolve-step-model`) are run by the `preToolUse` hook
itself: the session comes from the event payload's `sessionId` through the adapter's
private records, and nothing model-authored is stamped, rewritten or executed. The agent's
command line is only a structurally recognized request (rules above), and model-authored
attribution in it is denied outright. This satisfies the core's mediated attribution rule
directly; the core's mediated-invocation backstop still applies inside the functions. The
VS Code adapter uses the same hook-executed design (its I4 is resolved), so the core's former
tool-boundary-stamp exception has been retired.

## Inconsistencies found while binding this host

- **Artifact schema `$id`.** Framework artifact schemas `$ref`
  `gsmarc://saf/harness/contracts/artifact/v1`, while the harness publishes
  `contracts/artifact.schema.json` as `gsmarc://saf/contracts/artifact/v1`.
  `check-step-artifact` then fails with `Unresolvable`, which H5 relays as "unavailable". This
  is a cross-repository defect, outside this adapter.
- The shared `adapters/dispatch.sh` is a bash script. This adapter does not use it: every entry
  runs `adapter.py` through an explicit interpreter, so Windows needs no bash. See
  [`../../../adapters/README.md`](../../../adapters/README.md).
