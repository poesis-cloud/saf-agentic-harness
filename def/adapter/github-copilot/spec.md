# Adapter — `github-copilot`

The host binding for the **GitHub Copilot agent runtime**, the one runtime three hosts share:

- the **GitHub Copilot CLI**;
- the **GitHub Copilot app**, which embeds it;
- **VS Code** with the chat **Session Target: Copilot**, which runs the same runtime and hook
  format. VS Code's other runtime, *Local*, is not supported: in plugin mode a Local-runtime
  payload is detected and refused (see [Local runtime](#local-runtime)).

This document is the adapter's canonical specification. It binds the runtime's **hooks** to
the boundaries of [`../../core/spec.md`](../../core/spec.md) (Boundary Normalization), and it
is the harness's only host binding. H0–H7 name those boundaries as bound in
[Hook → boundary binding](#hook--boundary-binding). The harness function contracts are the
core's and are not restated here. The class model is
[`adapter-src-classes.puml`](adapter-src-classes.puml).

**Why the id `github-copilot`.** The CLI, the app and VS Code's Copilot session target run one
runtime, with one hook engine, one tool vocabulary and one custom-agent/plugin format. The
adapter binds that runtime, not a window around it, so the same adapter serves all three.
Nothing in the binding is app-only: the facts below that were observed to differ between the
standalone CLI and the app are host-conditional, and the adapter accepts both forms (each is
replayed from a live capture in the process tests).

**Two ways to install.** In **plugin mode** (see [Plugin mode](#plugin-mode)) the harness is
its own Copilot plugin, the *harness plugin*, installed from a marketplace beside the
framework's plugin; nothing in it is machine-specific. The **developer install**
(`install.py`, see [Rendered registration](#rendered-registration--installpy)) renders the
hooks with absolute paths for a harness checkout and a framework checkout. Where the two
differ, this document says so.

## Sources

| Host fact | Source |
| --- | --- |
| Hook events, input/output fields, config format, locations, timeouts, failure semantics | [Hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference), [About hooks](https://docs.github.com/en/copilot/concepts/customizations/hooks) |
| Custom agents (`*.agent.md`, frontmatter `tools`, `model`, `hooks`) | [Custom agents configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration) |
| Plugins (`plugin.json`, `hooks.json`, marketplaces) | [CLI plugin reference](https://docs.github.com/en/copilot/reference/cli-plugin-reference) |
| VS Code's two runtimes, plugin hooks, `PLUGIN_ROOT` | [VS Code hooks](https://github.com/microsoft/vscode-docs/blob/main/docs/agent-customization/hooks.md), [VS Code agent plugins](https://github.com/microsoft/vscode-docs/blob/main/docs/agent-customization/agent-plugins.md) |
| `PLUGIN_DATA` (persistent, writable, per installed plugin) | [Agent Plugins specification 1.0, §9](https://github.com/agentplugins/agent-plugins-spec/blob/main/spec/1.0.0.md), [Hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference) |
| **Actual** payload shapes, tool names, subagent identity (authoritative where they differ from the docs) | Payloads captured in the GitHub Copilot app on Windows, runtime **1.0.102**: [`tests/functional/adapter_github_copilot/fixtures/copilot-app-windows-1.0.102.jsonl`](../../../tests/functional/adapter_github_copilot/fixtures/copilot-app-windows-1.0.102.jsonl) |
| The same, on the **standalone CLI** (Linux, `copilot -p`, framework bundle loaded with `--plugin-dir`, runtime **1.0.105**) | [`tests/functional/adapter_github_copilot/fixtures/copilot-cli-linux-1.0.105.jsonl`](../../../tests/functional/adapter_github_copilot/fixtures/copilot-cli-linux-1.0.105.jsonl) |

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
   - The concrete file tools are resolved by the host from the agent's declared `tools`
     aliases, per model and per runtime version; the harness cannot observe or configure that
     mapping. Observed on CLI 1.0.105: Claude-family models get `create`/`edit` (`create`
     refuses a missing parent folder ("Parent directory … does not exist. You need to create
     it before creating the file"), and `edit` needs an existing file); GPT-5.x get
     `apply_patch` (no `create`/`edit`, but a freeform `apply_patch`, whose `toolArgs` is the
     raw patch string; its `*** Add File:` creates missing folders). Observed on app runtime
     1.0.102: for an agent declaring `edit`, a `gpt-5.3-codex` `task` subagent reported only
     `view`, `rg`, `glob`, `powershell*` and `skill`, with no write tool. Therefore
     [`models.yaml`](../../../adapters/github-copilot/models.yaml) binds only models
     verified to receive a write tool on this host (see [Model binding](#model-binding)).
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
9. **Plugin hooks (documented).** A plugin hook runs with the **workspace** as its working
   directory, not the plugin's folder, and receives the plugin's root in the environment
   variable `PLUGIN_ROOT` and its persistent, writable data folder in `PLUGIN_DATA`. The
   data folder survives plugin updates; the root is replaced on every update and is read-only
   in a sandboxed session. VS Code's hook parser takes `command`, `bash`, `powershell` and the
   per-OS overrides, but **no `exec`**, so a portable plugin registration uses shell strings.
   A plugin in the Agent Plugins 1.0 format keeps its hooks in its Copilot extension
   directory, `com.github.copilot/hooks/hooks.json`. Nothing installs Python or Python
   packages for a git-sourced plugin, on any host.
10. **VS Code's runtimes (documented).** VS Code with **Session Target: Copilot** runs this
    runtime, with its lowerCamel events and payloads (`toolName`/`toolArgs`). VS Code's
    **Local** runtime also loads plugin hooks and accepts this runtime's event names, but
    still sends its own payloads: snake_case fields (`tool_name`, `tool_input`, `session_id`)
    and a `hookEventName`/`hook_event_name` field. A plugin hook can therefore receive a
    Local-runtime payload, and must tell the two families apart.

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

## Adapter architecture

One hook firing crosses four seams. Each seam has one governing contract.

1. **stdin:** the host event JSON, governed by this adapter's
   [`contracts/hook-stdin.schema.json`](../../../adapters/github-copilot/contracts/hook-stdin.schema.json).
2. **entry:** the registered command reaches `adapter.py hook --event <e>`, through the
   plugin-mode shim and launcher or through the interpreter `install.py` records. This
   seam only shapes argv and has no contract of its own.
3. **adapter → core:** this adapter's own code does five things:
   - classifies the event through `tools.yaml`;
   - resolves the session;
   - sequences the calls: registration first, one invocation per path, abort on failure;
   - invokes the harness function commands (`harness.py`, one command per function,
     host-blind), each under `contracts/api/<function>.input|output.schema.json`;
   - lets every completed invocation journal per `contracts/log-entry.schema.json`.
4. **stdout:** the host decision this adapter renders from the reports, governed by
   [`contracts/hook-stdout.schema.json`](../../../adapters/github-copilot/contracts/hook-stdout.schema.json).
   Decisions are structured stdout on exit 0. Exit 2 is only the hard-failure fallback
   (see [Fail-closed entry](#fail-closed-entry)).

- **The adapter depends on the command API only.** It never depends on the core's
  `services`, `stores` or `config`, and never reads `ConfigLoader`, the access control
  list or the session log. Framework-agent gating (C7) and step-resolution correlation are
  validated inside the invoked commands, never pre-checked by the adapter. Session
  identification uses only host-observed event data plus the adapter's own private
  records.
- **The adapter owns everything host-aware:** event classification, session tracking,
  orchestration, rendering reports into host decisions, its own declarative YAML
  (`hooks.yaml`, `agent-hooks.yaml`, `tools.yaml`, `models.yaml`) loaded with its own
  tools, and the two seam contracts. Nothing host-specific leaks into the function
  commands or their contracts (C4).
- **The binding is fail-fast on every hook load.** `tools.yaml` and `models.yaml`
  validate against `contracts/conf/adapters/*.conf.schema.json` through
  `adapters/binding_contract.py`. An empty probe list that a declared gate depends on is
  refused, because it would classify every firing as a pass-through (a silent fail-open).

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
- **Records** live outside every repository, in the adapter's **state folder**:
  `SAF_HARNESS_STATE_DIR`, else `$PLUGIN_DATA/state` when `PLUGIN_DATA` is set (plugin mode),
  else `%LOCALAPPDATA%\saf-agentic-harness\github-copilot` on Windows, else
  `$XDG_STATE_HOME/saf-agentic-harness/github-copilot` (`XDG_STATE_HOME` defaulting to
  `~/.local/state`). A corrupt record is an error: `preToolUse` denies.
- **Each record is stamped** with the governed workspace and the harness version that opened
  it. The workspace is fixed when the record is created (H0 or H1) and every later firing of
  the session uses the recorded one: in plugin mode it is resolved from the session's folder
  ([Workspace resolution](#workspace-resolution)), in the developer install it is the
  registered layout's. A firing whose installed harness version differs from the record's is
  denied with "the plugin was updated; start a new session" (core:
  [Harness compatibility](../../core/spec.md#harness-compatibility)); H7 still ends that
  session, since core function 11 is exempt from the version check.
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
- **Invariant:** the announce reads no session state and writes none. It fires for summarizer
  sessions too. In plugin mode it follows the registration and provisioning entries (see
  [Plugin mode](#plugin-mode)), and announces only in a SAF workspace.

### H0 — userPromptSubmitted / userPromptTransformed (agent-scoped)

- **Pre:** `--agent` is present (scoped hook) and `sessionId` is non-empty. Otherwise pass.
- **Plugin mode:** the scoped entry also carries `--framework <name>`. Before anything else, H0
  writes the session's intent record (see [Fail-closed scope](#fail-closed-scope)). It then
  resolves the workspace (see [Workspace resolution](#workspace-resolution)); in a folder that
  is not a SAF workspace of that framework, H0 records nothing and its context is "this folder
  is not a SAF workspace; ask me to initialise it".
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
   holds (core function 8, invariants 7–8). Claims are per path: when the batch is denied as
   a whole, the locks claimed for its allowed paths persist until the step instance is
   promoted, and the retried write finds them already held by its step instance.

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
6. **I9, the shell guard** (the binding's `guardedShellTools` class): any other shell
   command naming the workspace (by name, relative path or absolute prefix) or `logs/` →
   deny. Read-only commands are included. The guard is textual and advisory: C6's graded
   guarantee (detect-and-remediate plus the CI gate) remains the integrity claim. The
   guard closes the routine escape, not the adversarial one.
   - A relative path token resolves against the payload's `cwd`. A token counts as a path
     when it has a separator or a leading `.`, when it exists, or when it follows `--`.
   - Git pathspecs are path references: every token after `--`, and the `<path>` of a
     `<rev>:<path>` object name (e.g. `git show HEAD:<path>`), resolved from the workspace
     when `cwd` is inside it.
   - The value of a `-option=value` token is checked like a token.

### H5/H6 — postToolUse / postToolUseFailure

- **H5** runs only on `postToolUse` of a write tool in a governed session:
  `check-step-artifact --artifact-path <each target>`.
  - `valid` → pass. A changed set is committed onto the step instance's step ref, never
    onto the workspace branch: it enters workspace state only when H6's function 10
    promotes the step instance. A set byte-identical to the step ref tip commits nothing
    (`commitId: null`, core function 9, invariant 4).
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
  - **A refused sub-agent's return does not conclude the step.** A dispatch return whose
    sub-agent is the session of a refused registration (function 0 returned an error, e.g.
    `worker-session-bound`), or that the adapter never registered as the step's worker, does
    NOT trigger `check-step-postconditions`: the step stays in flight for its bound worker
    session, and that worker's return concludes it (core function 10, Trigger). The
    `postToolUse(task)` names no sub-agent, but `subagentStart` and `subagentStop` do
    (`agentId`). When H1 does not register the sub-agent as the step's worker, it records a
    pending marker for it under the dispatcher; that sub-agent's own `subagentStop` arms the
    marker; H6 consumes one armed marker instead of evaluating. A sub-agent's `subagentStop`
    fires before the dispatcher's `postToolUse(task)` of that dispatch (host fact 6), so a
    skipped return always follows the stop of a sub-agent that was never the step's worker:
    while the bound worker runs, every return is skipped, and the bound worker's return is
    evaluated as soon as no such stopped sub-agent's return is outstanding. One marker file
    per sub-agent, beside the session records, moved and consumed atomically. Closing the
    dispatcher's session, or a new turn on its host session, drops its markers.
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
  - a missing or non-directory framework root (`--framework-dir`, else `FRAMEWORK_DIR`; in
    plugin mode, the registered root of the framework the workspace marker names);
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
  H0 from creating that record. A broken developer install therefore blocks tool use in every
  folder where its hooks fire until it is repaired. That is visible and fail-closed, never
  silent. **Plugin mode** narrows this rule to the sessions governance applies to (see
  [Fail-closed scope](#fail-closed-scope)).
- **A readable but malformed tool-call batch** in a governed session is denied with a reason.
- **Harness failures** (no report, non-zero exit, stderr, a call stopped at the hook
  deadline) become fixed text such as
  "check-step-authorization unavailable" and deny the write. The raw text goes to stderr only.
  Error reports (`inquiry-error`, `state-error`, `system-error`) deny, and the core's own
  `code — message` is relayed.

## Model binding

[`models.yaml`](../../../adapters/github-copilot/models.yaml) maps every model-profile slug
to a model id this host offers. On **every** hook load, the bootstrap reads the framework model
catalogue (`<fw>/conf/model-profiles.conf.yaml`) and refuses a catalogue that names a profile
slug `models.yaml` does not bind (core function 4: unroutable configurations die at load). The
refusal is a bootstrap failure (exit 2, the unbound slugs listed on stderr), so `preToolUse`
denies. A missing or unreadable catalogue is refused the same way. Nothing is cached from
install time: the host's model list can change after install, so `models.yaml` must track
the models the host currently offers, and the check re-reads both files on each firing. The
runtime passes no model list to hooks (none in any captured payload), so the adapter cannot
check `models.yaml` against the live host list itself.

## Plugin mode

In plugin mode the harness ships as its own Copilot plugin, the **harness plugin**, beside
the framework's plugin, the **framework plugin**. Both are installed from a marketplace into
a host-managed, read-only folder that every update replaces. Nothing in either plugin holds a
machine path: every path is derived at runtime.

### The harness plugin

The harness plugin is in the **Agent Plugins 1.0** format
([specification](https://github.com/agentplugins/agent-plugins-spec/blob/main/spec/1.0.0.md)),
which the Copilot CLI, the app and VS Code all read. Its root (`PLUGIN_ROOT`) holds:

| Path | Content |
| --- | --- |
| `plugin.json` | the manifest: `$schema: https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`, `name: saf-agentic-harness`, the harness `version`, `description`, `license`, `repository`. The schema is closed: no component path keys, no other top-level field. |
| `com.github.copilot/hooks/hooks.json` | the global hooks, as shell strings only (below), in the Copilot extension directory |
| `VERSION` | the harness version, one line |
| `requirements.lock` | the runtime requirements, hash-locked for every supported platform |
| `launcher.py` | the stdlib-only launcher |
| `bin/saf-hook.sh`, `bin/saf-hook.ps1` | the shims every hook entry runs |
| `bin/register.sh`, `bin/register.ps1` | the `sessionStart` registration, shell only |
| `bin/launcher.sh`, `bin/launcher.ps1` | the stable launcher, copied into the registry |
| `harness.py`, `src/`, `contracts/`, `adapters/github-copilot/`, `adapters/binding_contract.py` | the harness runtime |
| `LICENSE`, `README.md` | the license and the install instructions |

The manifest validates against the official schema
(`schemas/1.0.0/plugin.schema.json` of the specification) in the harness CI.

### The registry

The two plugins find each other through a **per-user registry**, a folder outside every
repository and every plugin: `SAF_AGENTIC_REGISTRY_DIR`, else
`%LOCALAPPDATA%\saf-agentic\registry` on Windows, else
`$XDG_STATE_HOME/saf-agentic/registry` (`XDG_STATE_HOME` defaulting to `~/.local/state`).
Its format is a contract between the harness and every framework plugin:

| Entry | Written by | Content |
| --- | --- | --- |
| `harness/<version>/root` | the harness plugin's `sessionStart` | the harness plugin's root, as an absolute path |
| `harness/<version>/data` | the harness plugin's `sessionStart` | the harness plugin's data folder (its `PLUGIN_DATA`), as an absolute path |
| `harness/latest` | the harness plugin's `sessionStart` | the version that registered last |
| `harness/launcher.sh`, `harness/launcher.ps1` | the harness plugin's `sessionStart` | the stable launcher |
| `frameworks/<name>/root` | the framework plugin's `sessionStart` | the framework plugin's root, as an absolute path; `<name>` is its plugin name |
| `sessions/<host session id>` | the harness launcher, at `sessionStart` and H1 | the harness version serving that host session |

- Every entry is a UTF-8 text file holding one line; readers strip surrounding whitespace.
  Every write goes to a temporary file in the same folder, then is renamed over the entry,
  so a reader never sees a partial entry. The last writer wins.
- Both plugins register with a **shell-only** script (`sh` on POSIX, PowerShell on Windows),
  never Python, so a registration never depends on the runtime it helps find.
- Entries are keyed by version, so parallel harness versions (for example one installed by
  the CLI and another by VS Code) never overwrite each other's root.
- The **stable launcher** is the one entry point a framework plugin calls. Every harness
  version installs the same launcher behavior: it reads the host session id from the
  payload, takes the version recorded for it under `sessions/` (else `harness/latest`), and
  runs that version's `bin/saf-hook` shim with the payload and its arguments, with
  `PLUGIN_ROOT` and `PLUGIN_DATA` set to that version's registered root and data folder. A
  scoped hook runs inside the framework plugin, whose own variables are not the harness's;
  this way a session's scoped hooks run the same harness version, on the same state folder,
  as its global hooks.
- `launcher.py` deletes a host session's `sessions/` entry at its `sessionEnd`, itself and
  before the adapter runs, so the deletion never depends on the runtime. A leftover entry is
  harmless, because host session ids are unique.

**The framework's scoped H0.** A framework orchestrator's agent-scoped H0 entries call the
stable launcher, with the framework's plugin name and the orchestrator's slug:

- `command`: `sh "${SAF_AGENTIC_REGISTRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/saf-agentic/registry}/harness/launcher.sh" <event> --framework <name> --agent <slug>`
- `windows`: `& "$(if ($env:SAF_AGENTIC_REGISTRY_DIR) { $env:SAF_AGENTIC_REGISTRY_DIR } else { Join-Path $env:LOCALAPPDATA 'saf-agentic\registry' })\harness\launcher.ps1" <event> --framework <name> --agent <slug>`

where `<event>` is `userPromptSubmitted` or `userPromptTransformed`. The framework plugin
renders these entries; the harness specifies them here.

**An unpopulated registry fails closed.** Hosts do not order the `sessionStart` hooks of two
plugins. When a firing in a governance context (see [Fail-closed scope](#fail-closed-scope))
needs a registry entry that is not there yet, typically the framework's root in the first
session after an install, it is denied with "the SAF plugins are not registered yet: start a
new session".

### Registration and the launcher

Every global hook entry is a shell string, never `exec`, and reaches the plugin through the
`PLUGIN_ROOT` environment variable:

```json
{ "type": "command",
  "bash": "sh \"$PLUGIN_ROOT/bin/saf-hook.sh\" preToolUse",
  "powershell": "& \"$env:PLUGIN_ROOT\\bin\\saf-hook.ps1\" preToolUse",
  "timeoutSec": 120 }
```

`sessionStart` has two entries, in order: `bin/register` (the registry), then the shim
(provisioning and the announce), with a `timeoutSec` of 300.

- **The shim** finds an interpreter of Python ≥ 3.10, in order: `SAF_HARNESS_PYTHON`, then
  `py -3` (Windows), `python3`, `python`. It runs `launcher.py <event> [arguments]` with
  the payload on stdin. When no interpreter is found, the shim decides alone, in shell,
  without Git: it denies a `preToolUse` (exit 2, the remedy on stderr) when the session
  folder or one of its ancestors holds `.saf/workspace.yaml`, and passes otherwise.
- **`launcher.py`** imports the standard library only and parses on every supported Python.
  It derives every path at runtime:
  - the adapter's state folder: `$PLUGIN_DATA/state` (see [Session identity](#session-identity));
  - the governed workspace and the framework (see [Workspace resolution](#workspace-resolution));
  - the framework layout, from `<framework root>/.env`, as for every adapter. No
    `FRAMEWORK_*` value is written into a hook entry.

  It applies the [fail-closed scope](#fail-closed-scope) and the
  [Local-runtime refusal](#local-runtime). It then runs the provisioned interpreter on
  `adapters/github-copilot/adapter.py hook --event <e> --framework-dir <framework root>
  --workspace-dir <workspace>`, with the payload and its stdout, stderr and exit status
  passed through unchanged.

### Python provisioning

At `sessionStart`, the launcher provisions the harness runtime. Provisioning never runs in
any other event: a `preToolUse` timeout fails open, so a gating event never waits for an
install.

- **Where.** A virtual environment under `$PLUGIN_DATA/venv/` (else under the state folder),
  keyed by `<harness version>-<first 12 hex digits of the SHA-256 of requirements.lock>-<Python ABI tag>-<platform tag>`.
  A new harness version, lock, interpreter or platform therefore gets its own environment.
- **How.** With `uv` when it is on `PATH` (`uv venv`, then `uv pip install`), else with the
  standard library's `venv` and `pip`. Both install from `requirements.lock` with
  `--require-hashes` and binaries only (`--only-binary=:all:`). `PIP_INDEX_URL` and
  `UV_INDEX_URL` are honored, so a mirror works.
- **Ready.** The last step writes a `.ready` stamp into the environment. An environment
  without the stamp is never used: it is deleted and rebuilt. An exclusive OS file lock on
  the environment serializes concurrent sessions; a session that waits for it reuses the
  environment the other built.
- **Skip.** With `SAF_HARNESS_PYTHON` set, nothing is provisioned: that interpreter is used
  as is, and must import the harness requirements.
- **Until ready**, every gating event in a governance context is denied with the remedy;
  outside one, events pass. A failed provisioning is reported once, as the `sessionStart`
  warning.
- **The lock** is generated in the harness CI with
  `uv pip compile --universal --generate-hashes`, so it covers every supported platform.

### Workspace resolution

In plugin mode the governed workspace is resolved at runtime from the session's folder, not
from the installation.

- **The marker.** A folder is a **SAF workspace** when its Git top-level (`git rev-parse
  --show-toplevel` from the payload's `cwd`) holds `.saf/workspace.yaml` **committed** in
  `HEAD`. The committed content is read, and validates against
  [`contracts/conf/workspace-marker.schema.json`](../../../contracts/conf/workspace-marker.schema.json):

  ```yaml
  framework: poesis-saf-agentic-organization   # the framework plugin's name (a slug)
  root: portfolio                              # the workspace root: a top-level node slug of the framework's workspace layout
  ```

  Both properties are required and no other property is allowed. The workspace
  (`FRAMEWORK_WORKSPACE_DIR`) is that Git top-level. The framework root (`FRAMEWORK_DIR`) is
  the registered root of the plugin `framework` names. A `root` that is not a top-level node
  slug of the framework's `conf/workspace.conf.yaml` is a configuration error, and fails
  closed.
- **Fixed per session.** The workspace is resolved when the session's record is created (H0
  for an orchestrator session; H1 records the dispatcher's workspace for a worker session) and
  stored in the record. Later firings never re-resolve it.
- **One framework per workspace.** A firing governs a workspace only for the framework its
  marker names: H0 carrying `--framework <name>` for another framework treats the folder as
  unmarked.
- **An unmarked folder** is ungoverned. An orchestrator selected there gets, as its H0
  context, "this folder is not a SAF workspace; ask me to initialise it", and its session
  stays ungoverned, so the framework's initialisation can write the marker.
- **The developer install** does not read the marker: its workspace is `--workspace-dir`,
  else the layout's `FRAMEWORK_WORKSPACE_DIR`.

### Fail-closed scope

In plugin mode, the hooks fire in every folder of every session. The harness therefore fails
closed only where governance applies. A session is in a **governance context** when its
folder is a SAF workspace, or when an orchestrator was selected in it: H0 writes a
stdlib-only **intent record** for the host session into the state folder before anything
else can fail. `launcher.py` deletes it at the session's `sessionEnd`, itself and before the
adapter runs, so the deletion never depends on the runtime.

The **runtime is unavailable** when any of these holds:

- no Python ≥ 3.10 is found;
- the provisioned environment is not ready;
- Git is missing or older than 2.38;
- a registry entry the firing needs is missing;
- the framework or the harness configuration fails to load, including the compatibility
  check (core: [Harness compatibility](../../core/spec.md#harness-compatibility)).

| Situation | While the runtime is unavailable |
| --- | --- |
| No governance context | **Pass** (exit 0, empty). `sessionStart` warns once, naming the problem and its remedy. |
| An orchestrator was selected | Every later `preToolUse` of the session is **denied** with the exact remedy. |
| A SAF workspace | Every `preToolUse` is **denied** with the exact remedy, shell commands included (I9). |

A denial from `launcher.py` is a structured `deny` whose reason names the problem and the
remedy, for example "Git ≥ 2.38 required (found 2.34)". A denial from the shim alone is exit 2
with the same text on stderr. Once the runtime is available, every rule of this document
applies unchanged. The developer install keeps the stricter rule of
[Fail-closed entry](#fail-closed-entry): a bootstrap failure denies in every folder.

### Local runtime

`launcher.py` tells the two payload families apart before anything else (host fact 10). A
payload carrying `tool_name`, `tool_input`, `session_id`, `hookEventName` or
`hook_event_name` is VS Code's **Local** runtime; any other is this runtime. In a governance
context, a Local-runtime `preToolUse` is refused with exit 2 and "select Session Target:
Copilot" on stderr; exit 2 blocks in both runtimes, and the Local runtime's output contract
is not this adapter's. Every other Local-runtime firing, and every one outside a governance
context, passes without running the adapter.

## Rendered registration — `install.py`

This is the **developer install**, for a harness checkout and a framework checkout. Plugin
mode does not use it.

The source of truth is [`hooks.yaml`](../../../adapters/github-copilot/hooks.yaml) (global)
and [`agent-hooks.yaml`](../../../adapters/github-copilot/agent-hooks.yaml) (scoped H0).
`install.py` renders both, with absolute machine paths. The output is generated and never
committed.

| Option | Meaning |
| --- | --- |
| `--framework-dir` (required) | The framework **checkout** (e.g. `saf-agentic-organization`, holding `.env`, `workflows/`, `instructions/`, `skills/`, `workspace/`), NOT the rendered bundle. Every hook's `cwd` and `FRAMEWORK_DIR`. |
| `--workspace-dir` | Override `FRAMEWORK_WORKSPACE_DIR` (absolute) — e.g. a portfolio repository outside the checkout. Written into the global hooks' `env` AND passed as `--workspace-dir` (scoped hooks cannot carry `env`). |
| `--bundle-dir` | The framework's rendered Copilot plugin bundle. Global hooks go to `<bundle>/hooks.json` (or the path a string `hooks` key in `plugin.json` names). The scoped H0 block goes into `<bundle>/agents/<orchestrator>.agent.md`. |
| `--hooks-target plugin\|repo\|user\|none` | Where the global hook file goes: the bundle (default with `--bundle-dir`), `<target-repo>/.github/hooks/safe-harness-github-copilot.json`, or `$COPILOT_HOME`/`~/.copilot/hooks/…`. |
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
  `# >>> safe-harness github-copilot H0 … >>>` and `# <<< safe-harness github-copilot H0 <<<`.
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
| Shell writes (I9) | `powershell`/`bash` is an unmediated write channel: the guard is textual and advisory (a variable or an encoded command evades it). | Workspace paths named in a shell command are denied. H5 does not see shell writes, so the core bounds them: no harness commit carries their bytes, the harness advances workspace state only by promotion, and a dirtied unclaimed path fails function 8's staging-baseline check. A foreign Git commit can still carry them into the local workspace branch, outside the harness's guarantee (C6); C6's detect-and-remediate plane owns cleanup. Document "write through `create`/`edit`" in the agents. |
| Folder trust | Repository hooks do not run in an untrusted folder (both hosts); the boundaries they register never fire, so the session stays ungoverned. | With `--hooks-target repo`, trust the folder (`--trust-folder` on the CLI). Prefer plugin hooks, which run regardless. A harness command in an ungoverned session is denied with a diagnosis. |
| H0 inside a subagent | Agent-scoped hooks DO fire for an orchestrator dispatched as a subagent (observed, app 1.0.102), after `subagentStart`. | H0 leaves a worker session to H1 and never opens a root for an agent a governed session dispatched. The hint is part of every governed context. |
| H1 `not-applicable` | A subagent whose dispatcher is ungoverned, or a harness `not-applicable` answer, leaves the subagent ungoverned. An error answer does not: the subagent is recorded unregistered and its writes and dispatches are denied. | C6 detect-and-remediate plane. |
| H4 result channel | The harness answer reaches the agent as a hook DENIAL of its shell call. On the standalone CLI (1.0.105) the model receives the tool result `Denied by preToolUse hook: <reason>`, the reason verbatim; the app's wrapper is not byte-verified. A model may read "denied" as failure and retry. | The reason starts with `SAFe agentic harness RESULT` and says it is the successful answer; the injected hint and the org's orchestrator prose say the same. A re-issued request is answered again (resolve functions do not advance on their own), so a retry costs a turn but changes nothing. |
| H4 on hook timeout | A timed-out `preToolUse` fails open, so the agent's own line would run in its shell. | That line has no `--session-id` and the agent shell has no `FRAMEWORK_DIR`: the harness refuses it at its exit plane (`FRAMEWORK_DIR is unset`, exit 2) and nothing is journaled. |
| `apply_patch` | Offered instead of `create`/`edit` to GPT-5.x models only (CLI 1.0.105); `toolArgs` is the raw patch string. | Bound as a patch tool: every header target is authorized; an unparseable patch → deny. |
| Missing parent folders | `create` refuses a missing parent folder; `edit` needs an existing file. | Host adaptation at H3: the hook creates the folders of an allowed `create` target inside the workspace (folders only, no commit). |
| `task` `mode: background` | The step returns before it ends, so H6 cannot evaluate it. | Denied in governed sessions. |
| H6 enforcement | `postToolUse` can only inform, not undo, a returned step. | The `additionalContext` directive to re-resolve. Future: `subagentStop` `decision: block` + `reason` can force the subagent to continue. |
| `exec` entries | Observed executing in the standalone CLI (1.0.105, plugin `hooks.json`); not yet observed in the app. | `--hook-style shell` renders `powershell`/`bash` strings with the same arguments. |
| Plugin agent names | The runtime namespaces plugin agents `<plugin>:<agent>`. | `hostAgentNamespaceSeparator` strips the namespace before `start-session`. Agents must dispatch the namespaced `agent_type`; that is the framework bundle's prose, not the adapter's. |
| Human gate in `copilot -p` | Non-interactive prompt mode offers no `ask_user`. | None in the adapter: a framework whose steps need a human gate must run interactively. |
| Latency | Plugin hooks fire on every tool call in every folder. | In plugin mode the shim answers in shell, without starting Python, when the folder holds no marker and the session has no intent record. Ungoverned firings that reach the adapter return after one state lookup. |
| Scoped H0 without the harness plugin | A framework orchestrator's scoped H0 calls the stable launcher in the registry. When the harness plugin is not installed, disabled, or has never started a session, the launcher is missing: the entry fails, and `userPromptSubmitted` cannot veto. | The session stays ungoverned, as with no hooks at all. The framework's orchestrator prose refuses to dispatch without the harness context that `sessionStart` injects. |
| Plugin hooks disabled | Host policy (for example a managed-hooks-only setting) or a user setting can disable plugin hooks. Nothing fires. | None in the adapter: as above, the framework's orchestrator prose refuses without the harness context. |
| Writes into another folder | Governance is decided by the session's own folder (its Git top-level). A session started in an unmarked folder is ungoverned, even when a tool writes by absolute path into a SAF workspace elsewhere. | Work on a SAF workspace from a session opened in it. C6's detect-and-remediate plane and the workspace CI gate catch what reaches the workspace branch. |
| VS Code's Local runtime | Local payloads reach plugin hooks (host fact 10), in a format this adapter does not bind. | Refused in a governance context with "select Session Target: Copilot" (see [Local runtime](#local-runtime)). |

## Mediated-attribution status

Functions 3–4 (`resolve-step`, `resolve-step-model`) are run by the `preToolUse` hook
itself: the session comes from the event payload's `sessionId` through the adapter's
private records, and nothing model-authored is stamped, rewritten or executed. The agent's
command line is only a structurally recognized request (rules above), and model-authored
attribution in it is denied outright. This satisfies the core's mediated attribution rule
directly; the core's mediated-invocation backstop still applies inside the functions. The
core's former tool-boundary-stamp exception is retired.

## Inconsistencies found while binding this host

- **Artifact schema `$id`.** Framework artifact schemas `$ref`
  `gsmarc://saf/harness/contracts/artifact/v1`, while the harness publishes
  `contracts/artifact.schema.json` as `gsmarc://saf/contracts/artifact/v1`.
  `check-step-artifact` then fails with `Unresolvable`, which H5 relays as "unavailable". This
  is a cross-repository defect, outside this adapter.
