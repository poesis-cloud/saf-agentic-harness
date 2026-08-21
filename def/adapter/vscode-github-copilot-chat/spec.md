# Adapter — `vscode-github-copilot-chat`

The host binding for **VS Code with the GitHub Copilot Chat extension**. This
document is the adapter's canonical specification: it binds the host's native **agent hooks**
to the harness boundaries, and specifies every hook the adapter registers — interface (stdin
In / stdout Out), output construction, preconditions, postconditions, and invariants — the
same way [`../../core/spec.md`](../../core/spec.md) specifies the harness functions. The
class model of this adapter's own code is
[`adapter-src-classes.puml`](adapter-src-classes.puml).

Everything here is grounded in the shipped hook engine, verified against
[microsoft/vscode](https://github.com/microsoft/vscode) — the Copilot Chat extension now
lives in-tree at `extensions/copilot/`, and the hook file/agent-frontmatter infrastructure in
VS Code core (`src/vs/workbench/contrib/chat/`); the former standalone
`microsoft/vscode-copilot-chat` repository is archived and must not be cited:

| Host fact | Source (microsoft/vscode) |
| --- | --- |
| Hook event vocabulary (`ChatHookType`) | `src/vscode-dts/vscode.proposed.chatHooks.d.ts` |
| Hook settings + file discovery | `src/vs/workbench/contrib/chat/common/promptSyntax/config/config.ts` (`chat.useHooks`, `chat.hookFilesLocations`), `…/service/promptsServiceImpl.ts` (`computeHooks`) |
| Hook command shape (`cwd` relative to repo root, platform overrides) | `src/vs/workbench/contrib/chat/common/promptSyntax/hookSchema.ts` |
| Agent-frontmatter hooks (`hooks:` block in `.agent.md`) | `extensions/copilot/assets/prompts/skills/agent-customization/references/agents.md`; core `promptHeaderAutocompletion.ts` / `promptValidator.ts` |
| Common stdin envelope + per-event input | `extensions/copilot/src/platform/chat/common/chatHookService.ts`, `…/common/hookCommandTypes.ts`, `extensions/copilot/src/extension/chat/vscode-node/chatHookService.ts` |
| Command execution, timeout, exit-code semantics | `extensions/copilot/src/platform/chat/node/hookExecutor.ts` |
| `SessionStart`/`SubagentStart` firing rules | `extensions/copilot/src/extension/intents/node/toolCallingLoop.ts` (`runStartHooks`) |
| `UserPromptSubmit` firing point + context handling | `extensions/copilot/src/extension/prompt/node/defaultIntentRequestHandler.ts` (`runWithToolCalling`) |
| `additionalContext` injection semantics | `extensions/copilot/src/extension/prompts/node/agent/agentPrompt.tsx` (`AdditionalHookContextPrompt`), `…/prompts/node/panel/toolCalling.tsx` (`appendHookContext`) |
| `PreToolUse`/`PostToolUse` result collapsing | `extensions/copilot/src/extension/chat/vscode-node/chatHookService.ts` |

## Summary

- [Host hook engine — verified facts](#host-hook-engine--verified-facts) — including the
  [required VS Code settings](#required-vs-code-settings), the
  [context-injection semantics](#context-injection-semantics), and
  [hooks at a glance](#hooks-at-a-glance) — one synthetic In/Out JSON pair per hook
- [Boundary binding](#boundary-binding) — host event → harness boundary → functions
- [Agent-session semantics](#agent-session-semantics) — 1 agent session = 1 execution until
  return; turns, steering, workflow end-to-end coherence
- [Session identity binding](#session-identity-binding)
- [Session correlation scenarios](#session-correlation-scenarios) — every case a hook firing's
  session attribution can land in, and why
- [The hooks](#the-hooks) — one specification per hook:
  - [H0 agent-scoped `UserPromptSubmit`](#h0-agent-scoped-userpromptsubmit--session-started) — session-started (0, 1, 2)
  - [H1 `SubagentStart`](#h1-subagentstart--step-started) — step-started (0, 6, 7)
  - [H2 `PreToolUse` — dispatch class](#h2-pretooluse--dispatch-class--step-starting) — step-starting (5)
  - [H3 `PreToolUse` — write class](#h3-pretooluse--write-class--write-starting) — write-starting (8)
  - [H4 `PreToolUse` — harness-command class](#h4-pretooluse--harness-command-class--mediated-attribution) — mediated attribution (3–4)
  - [H5 `PostToolUse` — write class](#h5-posttooluse--write-class--write-ended) — write-ended (9)
  - [H6 `PostToolUse` — dispatch class](#h6-posttooluse--dispatch-class--step-ended) — step-ended (10)
  - [H7 `SubagentStop` / `Stop`](#h7-subagentstop--stop--session-ended-best-effort-closure) — session-ended, best-effort closure (11)
- [Invocation plumbing and contract layering](#invocation-plumbing-and-contract-layering) —
  the host → dispatch → harness → host chain, which contract governs each seam, and the
  dispatch-as-CLI verdict
- [Rendered registration](#rendered-registration) — the workspace `.github/hooks/*.json` file
  plus the orchestrator `.agent.md` frontmatter block
- [Inconsistencies with the harness spec](#inconsistencies-with-the-harness-spec) — findings
  fed back into [`../../core/spec.md`](../../core/spec.md); all are now **resolved or
  integrated** except the permanently tracked items (I4's host-native attribution surface,
  I13(a)/(d) host verifications).

---

## Host hook engine — verified facts

Every adapter statement below relies on exactly these host behaviors.

**Event vocabulary** — the VS Code Agent-hooks docs list **8 firing events**:
`SessionStart` · `UserPromptSubmit` · `PreToolUse` · `PostToolUse` · `PreCompact` ·
`SubagentStart` · `SubagentStop` · `Stop`. The extension's `ChatHookType` (proposed API v6)
additionally names `SessionEnd` and `ErrorOccurred` — present in code, **not documented to
fire on this host's native path**: the adapter never relies on them.

**Agent-scoped hooks** — besides workspace files, a `hooks:` block in a custom agent's
`.agent.md` frontmatter registers hook commands **scoped to that agent**, for any of the 8
events: the command fires only while that agent is the active one (core validates and
autocompletes the frontmatter block; format identical to standalone hook files). This is the
only host mechanism that ties an event to an agent identity at the top level — `SessionStart`
and `UserPromptSubmit` payloads never name the active agent, and `SessionStart` fires once
per whole chat conversation regardless of which agent is selected. H0 is built on it.

### Required VS Code settings

The adapter is inert unless the user's VS Code carries these settings (workspace-recommended
via `.vscode/settings.json`; the harness's fail-fast configuration load SHOULD validate their
presence as part of the adapter binding):

| Setting | Required value | Why |
| --- | --- | --- |
| `chat.useHooks` | `true` | Master switch — hook files are discovered but **not executed** without it. |
| `chat.hookFilesLocations` | must include `.github/hooks` (host default — verify not overridden) | Discovery of the rendered workspace hooks file. |
| `chat.useClaudeHooks` | not required (`false` acceptable) | Claude-format hook files — unused by this adapter (Copilot-format only). |

Hooks are a **preview** feature: setting names may drift; the harness's fail-fast
adapter-binding validation at instantiation (spec — Internal validation) is the drift
detector.

### Context-injection semantics

How `additionalContext` concretely reaches the model — verified, and binding for H0/H1's
output construction:

- `SessionStart` / `SubagentStart` / `UserPromptSubmit` `additionalContext` strings are
  concatenated and rendered **into the current request's prompt** by the agent prompt's
  `AdditionalHookContextPrompt` element, as plain text prefixed
  `Additional instructions from hooks:` — for a subagent, inside its first user message
  (`AgentUserMessageProps.additionalHookContext`).
- `PreToolUse` / `PostToolUse` `additionalContext` strings are appended **to that tool
  call's result** as `<PreToolUse-context>` / `<PostToolUse-context>` tags.
- Injection is therefore **plain prompt text, scoped to the current request (turn)** — it
  does NOT persist across turns, and it does NOT engage the host's native instructions/skills
  machinery (no `applyTo` matching, no skill registration, no progressive disclosure).
  Consequences: (a) session-scoped context MUST be re-injected every turn — which the
  per-turn agent-session semantics require anyway; (b) skill injection must be a **load
  directive** (the resolved `<slug>.skill.md` paths plus the instruction to read them),
  never an inline dump — inlining would defeat the skills' own lazy-loading design and
  bloat every turn's prompt; instruction refs are small and are inlined.

**Discovery** — the host collects hook configuration files from the folders configured in
`chat.hookFilesLocations` (default includes `.github/hooks`) plus user-profile locations; all
collected hooks run — no override between sources.

**Registration shape** — a JSON object `{ "hooks": { "<Event>": [ <command>, … ] } }`. Each
command supports `type` (must be `"command"`), `command`, platform overrides
(`windows`/`linux`/`osx`, plus `bash`/`powershell` in core's schema), `cwd`, `env`, `timeout`
(seconds, **default 30**; SIGKILL escalation 5 s after timeout). There is **no per-tool
matcher** in this format: a `PreToolUse` / `PostToolUse` entry fires for **every** tool call
— tool discrimination is the adapter's job (this adapter's `tools.yaml`).

**Execution** — the command is spawned through the user's shell from `cwd` — which core's hook
schema resolves **relative to the repository root**, and which **defaults to `$HOME`** when
absent — so every entry MUST pin `cwd`. The event payload is JSON on stdin (URI values
serialized to filesystem paths); the hook answers with JSON on stdout.

**`UserPromptSubmit` firing point** — executed once per chat request (turn), at request
start, after the start hooks (`SessionStart`/`SubagentStart`) and before the tool-calling
loop runs; a `decision: "block"` aborts the request (`HookAbortError`). Whether a
**steering** message submitted while a request is still running fires `UserPromptSubmit` on
the native panel path is **not yet verified** (the CLI/Claude session paths have distinct
steering queues) — H0's turn rule is defined so both behaviors are safe (see
[Agent-session semantics](#agent-session-semantics)).

**Common stdin envelope** — merged under every event's own fields:

```json
{
  "timestamp": "2026-07-11T14:32:07.000Z",
  "hook_event_name": "PreToolUse",
  "session_id": "6a3f…-chat-session-guid",
  "transcript_path": "/home/user/…/transcript.jsonl",
  "cwd": "/abs/path/of/the/hook-command-cwd"
}
```

`session_id` is the **host chat session id** (present whenever the host has one — it flushes
the transcript before the hook runs); `transcript_path` when a transcript exists; `cwd` only
when the hook entry declares one.

**Exit-code semantics** (`NodeHookExecutor`):

| Exit | Meaning | Effect |
| --- | --- | --- |
| `0` | success | stdout parsed as JSON if valid; structured fields honored |
| `2` | blocking error | stderr is shown **to the model**; the action is blocked |
| other | non-blocking error | warning to the **user only**; **the action proceeds** |
| timeout / spawn failure / kill | non-blocking | **the action proceeds** |

⚠ The host **fails open**: only a completed hook (exit 0 with a structured deny, or exit 2)
blocks anything. See inconsistency I6.

**Structured output** — common fields `continue`, `stopReason` (short-circuits remaining
hooks), `systemMessage` (user-visible warning); event-specific fields per hook below.
Multi-hook collapsing: `PreToolUse` decisions collapse most-restrictive-wins
(`deny > ask > allow`), the **last** `updatedInput` wins (schema-validated against the tool's
input schema — invalid is discarded), `additionalContext` concatenates across hooks.
A `hookEventName` in the output that mismatches the running hook type causes the output to be
stripped (one-way compatibility: a `Stop` output is accepted under `SubagentStop`, a
`SessionStart` output under `SubagentStart`).

### Hooks at a glance

A synthetic, one-look summary of all 8 registered hooks — what each means, the host event
contract it receives (**In**), and this adapter's output contract back to the host (**Out**)
— in the same envelope style as the [`UserPromptSubmit` example above](#context-injection-semantics).
Each is the trimmed essence of its own full specification under [The hooks](#the-hooks)
below — consult H0–H7 there for registration, mechanics, preconditions, postconditions, and
invariants.

**H0 `UserPromptSubmit` (agent-scoped) — session-started.** Opens one orchestrator agent
session per chat request and injects its workflow context (functions 0–2).

**In** (stdin) — the scoping agent slug arrives as a dispatch argument, never in this payload:

```json
{ "hook_event_name": "UserPromptSubmit", "session_id": "chat-session-guid", "timestamp": "2026-07-11T14:32:07.000Z" }
```

**Out** (stdout, exit 0):

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<rendered workflow context>" } }
```

**H1 `SubagentStart` — step-started.** Registers the step session and injects its declared
instructions and skills (functions 0, 6, 7).

**In** (stdin):

```json
{ "hook_event_name": "SubagentStart", "session_id": "chat-session-guid", "agent_id": "subagent-invocation-id", "agent_type": "qa-engineer" }
```

**Out** (stdout, exit 0):

```json
{ "hookSpecificOutput": { "hookEventName": "SubagentStart", "additionalContext": "<rendered step context>" } }
```

**H2 `PreToolUse` — dispatch class — step-starting.** THE enforcement point for step
preconditions (function 5) before a `runSubagent` call executes.

**In** (stdin):

```json
{ "hook_event_name": "PreToolUse", "session_id": "chat-session-guid", "tool_name": "runSubagent", "tool_input": { "agentName": "qa-engineer", "model": "…", "prompt": "…" } }
```

**Out** (stdout, exit 0) — allow:

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "allow" } }
```

**Out** — deny:

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "check-step-preconditions fail: [report_exists] fail — no artifact matches 'review-report'" } }
```

**H3 `PreToolUse` — write class — write-starting.** Live authorization of the write's
artifact path (function 8) before the write executes.

**In** (stdin):

```json
{ "hook_event_name": "PreToolUse", "session_id": "chat-session-guid", "tool_name": "create_file", "tool_input": { "filePath": "/…/portfolio/epics/epic-payments.md", "content": "…" } }
```

**Out** (stdout, exit 0) — deny:

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "check-step-authorization denied: missing privilege: update epic (portfolio/epics/epic-payments.md)" } }
```

**H4 `PreToolUse` — harness-command class — mediated attribution.** Stamps the host-observed
session id onto a model-authored harness-CLI invocation (functions 3–4) — accepted under the
core's **narrow tool-boundary-stamp exception** (I4): fixed framework-authored shape, denies
model-authored attribution outright, backstopped by the harness's own registered-session
check, and named an explicit interim mechanism, not a permanent design.

**In** (stdin):

```json
{ "hook_event_name": "PreToolUse", "session_id": "chat-session-guid", "tool_name": "run_in_terminal", "tool_input": { "command": "harness.py resolve-step --workflow verification" } }
```

**Out** (stdout, exit 0):

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": { "command": "harness.py resolve-step --workflow verification --session-id chat-session-guid-t2026-07-11t14-32-07-000z" } } }
```

**H5 `PostToolUse` — write class — write-ended.** The commit gate (function 9) — validates
the just-landed staged write, commits or reverts it.

**In** (stdin):

```json
{ "hook_event_name": "PostToolUse", "session_id": "chat-session-guid", "tool_name": "create_file", "tool_input": { "filePath": "/…/portfolio/payments/features/feature-refunds.md" }, "tool_response": "Created file …" }
```

**Out** (stdout, exit 0) — valid:

```json
{ "continue": true }
```

**Out** — reverted:

```json
{ "decision": "block", "reason": "check-step-artifact reverted …: frontmatter.status: 'shipped' is not one of the enum values", "hookSpecificOutput": { "hookEventName": "PostToolUse", "additionalContext": "The write was discarded (restored from HEAD). Rewrite the artifact to satisfy its schema and retry." } }
```

**H6 `PostToolUse` — dispatch class — step-ended.** THE evaluation point (function 10) — the
dispatch returned; checks whether the step delivered.

**In** (stdin):

```json
{ "hook_event_name": "PostToolUse", "session_id": "chat-session-guid", "tool_name": "runSubagent", "tool_input": { "agentName": "qa-engineer" }, "tool_response": "…the subagent's final report…" }
```

**Out** (stdout, exit 0) — pass:

```json
{ "continue": true }
```

**Out** — fail:

```json
{ "decision": "block", "reason": "check-step-postconditions fail: [report_exists] fail — no artifact matches 'review-report'", "hookSpecificOutput": { "hookEventName": "PostToolUse", "additionalContext": "Per reports-handling: re-resolve (resolve-step) — the cursor returns the failed step; do not surface step details to the user." } }
```

**H7 `SubagentStop` / `Stop` — session-ended, best-effort closure.** Closes the ending
session's log (function 11) — best-effort, never surfaced to the host.

**In** (stdin) — `SubagentStop`:

```json
{ "hook_event_name": "SubagentStop", "session_id": "chat-session-guid", "agent_id": "subagent-invocation-id" }
```

**In** — `Stop`:

```json
{ "hook_event_name": "Stop", "session_id": "chat-session-guid" }
```

**Out** (stdout, exit 0) — always empty: `end-session`'s outcome is never surfaced.

---

## Boundary binding

The harness's canonical boundaries ([`../../core/spec.md`](../../core/spec.md) — Boundary
Normalization) bind to
this host as follows. Physical registrations: **5 events** in the
workspace hooks file, plus **one agent-scoped `UserPromptSubmit` entry per framework
orchestrator** (H0, in its `.agent.md`); `PreToolUse`/`PostToolUse` each carry several
**logical hooks** discriminated by `tool_name`
through `tools.yaml` (the host has no matcher — see facts above).

| Hook | Host event | Tool class | Harness boundary | Functions |
| --- | --- | --- | --- | --- |
| H0 | agent-scoped `UserPromptSubmit` (per orchestrator `.agent.md`) | — | session-started | 0, 1, 2 |
| H1 | `SubagentStart` | — | step-started | 0, 6, 7 |
| H2 | `PreToolUse` | dispatch (`runSubagent`) | step-starting | 5 |
| H3 | `PreToolUse` | write (`create_file`, …) | write-starting | 8 |
| H4 | `PreToolUse` | harness command via `run_in_terminal` | mediated attribution | (3, 4 — see H4) |
| H5 | `PostToolUse` | write | write-ended | 9 |
| H6 | `PostToolUse` | dispatch | step-ended | 10 |
| H7 | `SubagentStop`, `Stop` | — | session-ended | 11 (best-effort closure) |

Not registered: `SessionStart` (fires once per whole conversation and never names the active
agent — useless for C7; H0 supersedes it), `PreCompact` (no boundary), `SessionEnd` /
`ErrorOccurred` (not documented to fire natively — see host facts). Any
`PreToolUse`/`PostToolUse` event whose `tool_name`
matches no declared class is passed through: exit 0, empty stdout, no journal entry
(observational events are adapter telemetry, not harness functions).

**Boundary classification is structural on this host.** The harness spec discriminates
session-started from step-started by log correlation (unresolved `step-resolution` entry).
Here the host discriminates for us: a subagent session never fires the top-level events — it
fires `SubagentStart` — and H0 fires only while the scoping orchestrator agent is active. The
step-resolution correlation (validated inside start-session itself, see [Session identity
binding](#session-identity-binding)) remains necessary to resolve **which step** a step
session serves — but not to classify the boundary (see I1).

## Agent-session semantics

The harness noun this adapter binds is the **agent session**: ONE execution of ONE agent
until it **returns** — to the user when the agent is an orchestrator, to the orchestrator
when it is a dispatched subagent. The next user prompt opens ANOTHER agent session. The host
noun `session_id` is the **conversation** — it spans many agent sessions and is NOT the
harness session.

**The turn IS the agent session on this host.** A chat request (turn) is exactly one agent
execution until return, and the host runs `UserPromptSubmit` hooks **once per request, at
request start**, before the tool-calling loop — never mid-execution (verified:
`defaultIntentRequestHandler.runWithToolCalling`). So one H0 firing = one new orchestrator
agent session, with no cursor or dedup machinery needed.

**Steering prompts are NOT new agent sessions — and need no filtering rule.** A steering
message submitted while the agent is still executing does not start a new chat request;
since `UserPromptSubmit` executes only at request start, a mid-execution steer produces no
H0 firing — the agent session continues. If the host instead queues the message and
processes it as its own request after the current one returns, that IS a new agent session
— and the firing is then correct, not a duplicate. Either host behavior yields the right
semantics with the same rule: **every H0 firing opens an agent session** (native-path
steering delivery remains listed for verification — see I13 — but neither outcome changes
the rule).

**Workflow end-to-end coherence.** The framework invariant *one workflow end = return to the
user* means a workflow instance normally runs **end-to-end inside one orchestrator agent
session**: assent arrives as a prompt (opening the session), the orchestrator loops
resolve → dispatch → outcome without returning, and returns the end-to-end results — session
closed, instance's steps all journaled. This is coherent and works as intended, with two
bounded exceptions the harness already absorbs:

- **Mid-instance returns are legal.** A failing precondition whose missing state only the
  user can produce forces a return (harness function 5's caller usage) — the instance then
  continues in a LATER agent session: function 3's latest-open-instance deduction, the
  cross-log instance view, and the single-driver invariant exist exactly for this handoff.
  The invariant is the normal path, not an absolute.
- **Host continuations are new requests.** A tool-call-limit "Continue" confirmation starts
  a new request ("Please continue") — a new agent session mid-instance. Same handoff
  mechanics; no special case.

Engagement/selection turns before assent are agent sessions that drive no instance — they
journal their registration and context resolutions, nothing else.

The harness spec's Session definition now carries this exactly: a session serves at most one
workflow instance, and an instance's view spans sessions — never the converse (see I2,
resolved).

## Session identity binding

**This is this adapter's one irreducible job.** Every other responsibility here — boundary
binding, hook mechanics, rendering — exists only to serve one resolution: identify, from VS
Code's own host-specific event data, which agent session a given hook firing or mediated call
belongs to. The harness core neither performs this resolution nor needs to know anything
about the host-specific data behind it (C4) — it only ever receives the already-resolved
`sessionId` / `parentSessionId`. All ids are host-observed or adapter-minted from
host-observed data — never model-authored (harness invariant: session ids are observed or
minted by the surrounding mechanism). Sanitization: lowercase; any character outside
`[a-z0-9-]` maps to `-` (the id becomes a log filename).

| Harness field | Orchestrator agent session (turn) | Step (subagent) session |
| --- | --- | --- |
| `sessionId` | **derived per turn**: `<sanitized session_id>-t<sanitized event timestamp>` — computed purely from the stdin envelope's own `timestamp` field, zero reads of any kind (re-delivery of the same request naturally repeats the same timestamp, which start-session's own idempotency already absorbs) | sanitized `agent_id` (the subagent **invocation** id — unique per dispatch, so 1 step = 1 session holds) |
| `parentSessionId` | `null` (a user prompt opens it — a root) | the dispatching orchestrator's **current agent-session id** (resolved below) |
| actor agent | the **scoping agent** of the fired agent-scoped H0 hook — host-guaranteed, since an agent-scoped hook fires only while that agent is active (never payload-derived: no top-level payload names the agent) | `agent_type` (the subagent name → framework `agentSlug`) |

**Host-session → current-agent-session resolution.** Every hook's envelope carries a
`session_id` — confirmed **shared across the whole conversation, including nested subagent
dispatches**, on BOTH subagent paths (verified against `microsoft/vscode`: the built-in
subagent tools construct their loop's `Conversation` with the PARENT's `sessionId`
(`executionSubagentTool.ts` / `searchSubagentTool.ts`), and the full `runSubagent` chat
pipeline resolves `actualSessionId = history ?? request.sessionId ?? uuid` where a subagent
request's `request.sessionId` IS the parent conversation's id
(`chatParticipantRequestHandler.ts`; `defaultIntentRequestHandler.ts` uses that same field
as the "link back to the parent session"). The genuinely per-agent-execution id
(`subAgentInvocationId`) is never a hook `session_id` — it serves trajectory/log-file
linking only, surfacing in hooks solely as `SubagentStart`/`SubagentStop`'s `agent_id` — see
I13). The host docs' "agent session" wording notwithstanding, the envelope `session_id` is
the conversation. Because the raw id repeats, the adapter cannot resolve it with a flat
"remember one
value" pointer — `SessionTracker` keeps a **stack per `sanitized(session_id)`**, never the
harness log (which the adapter has no access to at all):

- **H0** resets the stack for this `session_id` fresh — a new turn discards whatever a prior
  turn left, and pushes the orchestrator's new turn session as its base.
- **H1** pushes the new step session on top when a dispatch opens — the previous top (the
  dispatching orchestrator's turn session) becomes the new step's `parentSessionId` before
  the push.
- **H7** (the step-closing case) pops the step session back off, restoring the dispatching
  session as current — see Session closure just below. Without this pop, the
  orchestrator's NEXT mediated call (H4) after a step closes would misattribute to the
  already-ended step session instead of its own. H7's `Stop` case instead **clears** the
  whole stack for this `session_id` — the turn is over, and forgetting immediately is what
  makes a LATER firing from a non-framework agent in the same conversation resolve to
  `None` (the correct C7 pass-through) instead of a stale session.
- **H2/H3/H4/H5/H6** only ever read the top via `resolve_current` — never push or pop.
  **Everything not pushed as a step belongs to the current top**: the orchestrator's own
  tool calls — including any non-framework subagent it spawns (H1 registers and pushes
  nothing for those) — are the orchestrator acting, and resolve to its session. That is
  attribution semantics, not a leak: a dispatch is the dispatcher's action unless the
  framework carved it into its own step session.

Deterministic: a conversation processes one request at a time, and only one step is in flight
per orchestrator session (function 3, invariant 9), so the stack never holds more than one
entry beyond its base and resolution is unambiguous at every point in the sequence.

**Session closure.** H7 (`SubagentStop`/`Stop`) resolves the ending session the same way, then
calls the harness's own `end-session` (function 11) with it (see H7, below) — a real,
journaled harness invocation now, not adapter-only bookkeeping. `SubagentStop` additionally
**pops** `SessionTracker`'s stack for this `session_id`, restoring the dispatching
orchestrator session as current (above); `Stop` **clears** the stack for this `session_id`
outright — the turn is done, and an emptied tracker is what guarantees that a later hook
firing in the same conversation while a NON-framework agent is active (no H0 fires for it,
so nothing re-registers) resolves to `None` and passes through per C7, rather than
resolving a stale framework session. Two independent defenses therefore cover the
post-turn window: the cleared tracker (adapter-side, immediate) and C8 (core-side — the
closing entry makes the core itself refuse any invocation against that `sessionId`,
regardless of what `SessionTracker` believes). The honest residual is the closure's own
best-effort nature (function 11, invariant 3): if the `Stop` hook itself never ran (host
crash, timeout — hooks fail open on this host, see I6), neither defense engaged, and a
stale resolution stays possible until the conversation's next H0 resets the stack. **If
resolution
finds no current session** for a `session_id` — never registered — no harness function is
invoked at all: the same pass-through (exit 0, empty output) already used elsewhere in this
binding for a foreign or unregistered session. H4 additionally **denies** rather than passing
through (see H4, below): its tool call IS the harness command about to
execute, so letting it run un-rewritten would invoke a harness function with no attributable
session at all.

## Session correlation scenarios

Every case a hook firing's resolved `sessionId` can land in, and why it is correct — or, in
one bounded case, not. "Registered" means `SessionTracker` holds an entry for this raw
`session_id`; "current" means the top of that entry's stack.

| # | Scenario | Resolution | Why |
| --- | --- | --- | --- |
| 1 | Two different conversations (concurrent chats/tabs) | Each resolves only its own | `SessionTracker` is keyed by the host's own `session_id` — distinct, host-assigned, unforgeable per conversation (see [Session identity binding](#session-identity-binding)) |
| 2 | Same conversation, step nested under its dispatching turn | Step session while open, dispatching turn once it closes | H1 pushes, H7's `SubagentStop` pops — the stack IS the nesting |
| 3 | Same conversation, later turn, previous turn's `Stop` ran normally | New turn's session | H0 resets the stack fresh on every firing, independent of whether `Stop` ran |
| 4 | Same conversation, `Stop` never ran (crash/timeout), NEXT activity is a framework agent | New turn's session — self-heals | `UserPromptSubmit` fires before any tool call in the turn (host-ordered); H0 resets the stack unconditionally, so the stale entry cannot survive to be read |
| 5 | Same conversation, `Stop` never ran, NEXT activity is a non-framework agent (no H0 fires for it) | **Stale session — the bounded residual gap** | Nothing resets a stack that only H0 clears/resets; C7's "unregistered → pass through" does not fire because the entry IS registered, just stale. Severity is real: the foreign agent's writes are misattributed to the stale framework session and evaluated against ITS grants — a privilege leak, not just wrong journaling (C8 narrows it only if the stale session's own log did receive an ending entry). Closes only at this conversation's next framework H0 |
| 6 | Scenario 4/5, but the dead turn left an open workflow instance | Latest-open-instance deduction continues it (function 3, invariant 8) | Same handoff mechanism as any legal mid-instance return — no special case |
| 7 | Scenario 4/5, but the dead turn left a step resolved with no outcome | Re-resolution resolves that step again | "Retry is re-resolution" (function 3, invariant 7); invariant 9's one-in-flight-step rule is scoped per session, so the dead session's dangling resolution never blocks the new one |
| 8 | A hook fires for a `session_id` never seen before (foreign agent, or framework agent before its first H0) | Pass-through, exit 0, empty output; H4 denies instead | C7 — correct, not a gap: nothing was ever registered to misattribute to |

Rows 1–4 and 6–8 are closed by construction. Row 5 is the only standing exposure, and it
requires two independent failures at once (the host never firing `Stop`, AND the very next
actor in that same conversation being a non-framework agent) — see [Session
closure](#session-identity-binding) for the double defense (adapter clear + core C8) that
narrows everything else.

---

## The hooks

### H0 agent-scoped `UserPromptSubmit` — session-started

Opens and registers one orchestrator **agent session** (function 0) and injects its workflow
context (functions 1–2) into the request. **Why not `SessionStart`:** on this host
`SessionStart` fires once per whole conversation and — like every top-level payload — never
names the active agent, so it can neither open per-turn agent sessions nor decide the C7
framework-agent gate. Instead H0 is an **agent-scoped hook**: a `hooks:` block in each
framework orchestrator's own `.agent.md` frontmatter binds `UserPromptSubmit`, which the
host runs **once per chat request while that orchestrator is the active agent** — the
scoping resolves the agent identity by construction (C7 holds structurally: no hook fires
for foreign agents), and the per-request firing point IS the agent-session boundary (see
[Agent-session semantics](#agent-session-semantics) — steering needs no filtering: it never
produces a mid-execution firing).

Every firing therefore enters the boundary: derive the turn's `sessionId` from the event's
own `timestamp` (zero reads), register, resolve, inject. Re-injecting functions 1–2's context
every agent session is not overhead — it is
**required** by the host's turn-scoped injection semantics (`additionalContext` does not
persist across requests; see [Context-injection semantics](#context-injection-semantics)).

**Registration** — NOT in the workspace hooks file: rendered into each orchestrator's
`.agent.md` frontmatter at bundle render time, the scoping agent's slug passed as a trailing
argument (host-fixed, not model-authored):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "adapters/dispatch.sh UserPromptSubmit vscode-github-copilot-chat value-management-officer",
        "cwd": "{{FRAMEWORK_DIR}}",
        "timeout": 30
      }
    ]
  }
}
```

(One such block per framework orchestrator — `value-management-officer`,
`release-train-engineer`, `scrum-master` — each naming its own slug.)

**In** (stdin)

```json
{
  "timestamp": "2026-07-11T14:32:07.000Z",
  "hook_event_name": "UserPromptSubmit",
  "session_id": "chat-session-guid",
  "transcript_path": "/…/transcript.jsonl",
  "cwd": "/abs/framework/root",
  "prompt": "…the user's message…"
}
```

The agent identity arrives as the dispatch argument (the hook's scoping agent), never from
the payload. `prompt` is ignored — the harness never reads prompt content.

**Harness invocations** (every firing; registration always first):

1. Function 0 —
   `{ "in": { "agent": "<scoping agent slug>", "sessionId": "<sanitized session_id>-t<sanitized event timestamp>", "parentSessionId": null } }`
2. Function 1 `resolve-workflow-instructions` —
   `{ "in": { "sessionId": "<same>", "parentSessionId": null } }`
3. Function 2 `resolve-workflow-skills` — same In.

**Out** (stdout, exit 0)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "<rendered workflow context>"
  }
}
```

**Output construction** — `additionalContext` is ONE string, built deterministically from the
two reports, respecting the injection semantics (plain per-request prompt text — the host's
native instructions/skills machinery is NOT engaged):

1. Each `WorkflowInstructionsReport.instructions` ref resolved against
   `FRAMEWORK_INSTRUCTIONS_DIR` and **inlined** (instruction files are small and normative),
   in report order, each under a header naming its ref.
2. Each `WorkflowSkillsReport.skills` id resolved to its `<slug>.skill.md` path under
   `FRAMEWORK_SKILLS_DIR` and emitted as a **load directive** — the resolved path plus the
   instruction to read it before acting — never an inline dump (skills are designed for lazy
   loading; inlining would bloat every request).

On a harness error: exit 0 with a `systemMessage` naming the failure — never exit 2 (this
boundary must not veto the user's message; an uninstructed orchestrator is observable in the
journal and cannot pass any later boundary).

**Preconditions**

- The hook fired agent-scoped: its scoping slug resolves to a framework orchestrator (by
  construction of the rendered `.agent.md`; a wrong slug is a `configuration-error` — the
  fail-fast configuration load validates adapter bindings).
- The required [VS Code settings](#required-vs-code-settings) are active (otherwise the hook
  never fires — an inert adapter, not an error state the hook can report).
- Functions 1–2's own preconditions (the session's agent is a framework orchestrator; the
  instruction set is keyed by it).

**Postconditions**

- The agent session's log `<workspace>/logs/<sessionId>.log.jsonl` exists — a NEW file per
  turn; registration first line; the two context resolutions journal after it (functions
  0–2's postconditions).
- The request's context contains exactly the orchestrator's inlined instructions and skill
  load directives — nothing chosen by the agent.

**Invariants**

1. Registration precedes everything at this session's level, physically (function 0
   invariant 1): 0 runs before 1–2 within the same hook handling.
2. 1 firing = 1 agent session = 1 log: the derived id is a pure function of the event's own
   `timestamp`, so a genuinely new turn never collides with an existing one, and a host
   re-delivery of the same firing reproduces the identical id (function 0 invariant 4 covers
   this: same id, no second registration).
3. An orchestrator switch mid-conversation needs no special case: the next firing is scoped
   to the newly active agent and opens its own agent session — the conversation's lineage of
   driving agents is the sequence of its registrations.
4. The hook never blocks and never mutates: context injection and journal entries are its
   only effects.
5. Mid-execution prompts (steering) never open a session: H0 can only fire at request start
   (host firing point) — there is no firing to suppress.

### H1 `SubagentStart` — step-started

Registers the step session (function 0, with parent) and injects the step's declared context
(functions 6–7) through `additionalContext` — the host's native context-injection surface.

**Registration**

```json
{
  "hooks": {
    "SubagentStart": [
      {
        "type": "command",
        "command": "adapters/dispatch.sh SubagentStart vscode-github-copilot-chat",
        "cwd": "{{FRAMEWORK_DIR}}",
        "timeout": 60
      }
    ]
  }
}
```

**In** (stdin)

```json
{
  "timestamp": "…",
  "hook_event_name": "SubagentStart",
  "session_id": "chat-session-guid",
  "transcript_path": "/…/transcript.jsonl",
  "cwd": "/abs/framework/root",
  "agent_id": "subagent-invocation-id",
  "agent_type": "qa-engineer"
}
```

`agent_type` is the subagent name — for a framework dispatch this is the framework
`agentSlug` the orchestrator relayed verbatim from function 3's step resolution.

**Harness invocations** (in order; registration always first):

1. Function 0 —
   `{ "in": { "agent": "<agent_type>", "sessionId": "<sanitized agent_id>", "parentSessionId": "<current agent session of session_id>" } }`
   (parent per the host-session → current-agent-session resolution — the dispatching
   orchestrator's turn session, not the raw conversation id, from the adapter's own
   `SessionTracker`). After resolving the parent, the adapter **pushes** this new step
   session (`sanitized(agent_id)`) as current for this `session_id` — see
   [Session identity binding](#session-identity-binding) — so any write hooks (H3/H5) firing
   during the step resolve to it, not to the dispatching orchestrator session; H7 pops it
   back on step close.
   C7 gate needs no ACL lookup **adapter-side**: start-session correlates this session to the
   parent's
   latest unresolved `step-resolution` entry for `agent_type` — only a configured,
   ACL-validated workflow actor is ever named there, so a non-framework `agent_type` simply
   finds no correlation and the function reports not-applicable; nothing is registered or
   logged, and the adapter never touches the access control list itself (the core's own
   root-session membership gate lives in `SessionLifecycle`, not here — see I15).
2. Function 6 `resolve-step-instructions` —
   `{ "in": { "sessionId": "<step>", "parentSessionId": "<parent>" } }`.
   Step correlation — matching this session to the parent's latest unresolved
   `step-resolution` entry whose actor equals `agent_type` — is validated INSIDE
   start-session itself (it already holds the session log); the adapter never reads the
   parent's log to pre-check this.
3. Function 7 `resolve-step-skills` — same In.

**Out** (stdout, exit 0)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SubagentStart",
    "additionalContext": "<rendered step context>"
  }
}
```

**Output construction** — `additionalContext` is ONE string, built deterministically from the
two reports, respecting the [context-injection semantics](#context-injection-semantics)
(rendered into the subagent's first user message, this request only):

1. For each ref in `StepInstructionsReport.instructions` (contract/repo-relative refs):
   resolve against `FRAMEWORK_INSTRUCTIONS_DIR` and **inline the file content**, in report
   order, each under a header naming its ref.
2. For each id in `StepSkillsReport.skills`: resolve the skill's `<slug>.skill.md` path
   under `FRAMEWORK_SKILLS_DIR` and emit a **load directive** (path + the instruction to
   read it before acting) — never an inline dump (skills are lazy-loaded by design).
3. Concatenate 1 then 2. No adapter-authored prose beyond the fixed headers/directive
   wording; the agent receives exactly its step's declared refs — nothing more, nothing
   chosen by the agent (functions 6–7 postconditions).

**Preconditions**

- Function 0's, 6's, and 7's own preconditions; notably start-session itself validates
  that an unresolved `step-resolution` entry correlating to this session exists in the
  parent session's log — the adapter has no log access of its own to pre-check this.
- A `SubagentStart` with **no** correlatable unresolved
  `step-resolution` entry gets `not-applicable` from function 0 (core failure-mode rule 2 —
  foreign and framework dispatches alike: for a step session the correlation IS the gate, so
  the two cases converge on the same outcome even though the core could now tell them apart
  via its own membership lookup, I15): rendered as pass-through, exit 0, empty
  output, optionally a stderr diagnostic. An uninstructed framework subagent is bounded: its
  step never journals executed (function 10 finds no in-flight step either), so the workflow
  makes no progress and re-resolution recovers.

**Postconditions**

- The step session's log exists; its first three entries are the registration and the two
  context resolutions.
- The subagent's context contains exactly the rendered step refs.

**Invariants**

1. 1 step = 1 agent = 1 session = 1 artifact: `agent_id` is unique per dispatch, so the
   registered `sessionId` never collides across retries of the same step.
2. Rendering is a pure function of the two reports plus the framework layout — byte-identical
   re-render for identical reports.
3. Foreign subagents (non-framework `agent_type`) pass through untouched and unlogged (C7).

### H2 `PreToolUse` — dispatch class — step-starting

THE enforcement point for step preconditions (function 5): a failing precondition denies the
dispatch before the step session ever opens. Fires on `PreToolUse` when `tool_name` is a
declared dispatch tool (`tools.yaml` → `dispatchTools: [runSubagent]`).

**Registration** (shared by H2/H3/H4 — the host has no matcher; ONE `PreToolUse` entry, the
adapter classifies by `tool_name`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "command": "adapters/dispatch.sh PreToolUse vscode-github-copilot-chat",
        "cwd": "{{FRAMEWORK_DIR}}",
        "timeout": 60
      }
    ]
  }
}
```

**In** (stdin)

```json
{
  "timestamp": "…",
  "hook_event_name": "PreToolUse",
  "session_id": "chat-session-guid",
  "transcript_path": "/…/transcript.jsonl",
  "cwd": "/abs/framework/root",
  "tool_name": "runSubagent",
  "tool_input": {
    "agentName": "qa-engineer",
    "model": "Claude Sonnet 4.6 (copilot)",
    "prompt": "…the relayed step resolution…"
  },
  "tool_use_id": "call_abc123"
}
```

**Harness invocation** — function 5 `check-step-preconditions`, always invoked when
`tool_name` matches the binding's dispatch-tool list (mechanical classification — no ACL
access on the adapter side):

```json
{ "in": { "sessionId": "<current agent session of session_id>", "parentSessionId": null } }
```

Session per the host-session → current-agent-session resolution (the orchestrator's turn
session, from the adapter's own `SessionTracker`). The step key is deduced harness-side from
the dispatching session's in-flight step (function 3, invariant 9) — no ACL check and no
extra field needed: only a configured, ACL-validated workflow actor is ever resolved there,
so a dispatch with no matching in-flight step is already not-applicable, which the adapter
renders as pass-through (exit 0, empty output).

**Out** (stdout, exit 0)

- Preconditions hold (`CheckStepPreconditionsReport.outcome = pass`):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow"
  }
}
```

- A precondition fails (`outcome = fail`):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "check-step-preconditions fail: [after_build] pass; [report_exists] fail — no artifact matches 'review-report'"
  }
}
```

**Output construction** — `permissionDecision` maps 1:1 from the report `outcome`
(`pass → allow`, `fail → deny`). `permissionDecisionReason` serializes every
`conditionChecks[]` entry — condition slug, outcome, `failureMessage` when failing — so the
orchestrator's `reports-handling` instruction receives exactly the report content (the host
relays the reason to the model on deny). Harness error outcomes (`state-error`,
`configuration-error`, …) map to `deny` with the `error` detail as reason — **never** to a
silent allow (deny is the enforcement; erring open would unmake it).

**Preconditions** — function 5's own; the invoking session is registered (H0).

**Postconditions**

- One `check-step-preconditions` entry in the dispatching (orchestrator) session's log.
- On deny, the dispatch never executes — the step session never opens (host guarantee for a
  completed hook returning `deny`).

**Invariants**

1. Deny-by-default within the hook: any non-`pass` outcome — including harness errors —
   denies. (The residual fail-open is the HOST's: timeout/crash proceeds — see I6.)
2. The hook never mutates: no artifact, no `updatedInput` at this boundary.
3. Non-framework dispatches pass through untouched and unlogged (C7).

### H3 `PreToolUse` — write class — write-starting

Live authorization (function 8) of every structured write tool call whose target resolves
into the workspace artifact layout. Same physical registration as H2; classified by
`tool_name ∈ writeTools ∪ deleteTools` (`tools.yaml`).

**In** (stdin) — as H2, with a write tool:

```json
{
  "timestamp": "…",
  "hook_event_name": "PreToolUse",
  "session_id": "chat-session-guid",
  "tool_name": "create_file",
  "tool_input": { "filePath": "/…/portfolio/epics/epic-payments.md", "content": "…" },
  "tool_use_id": "call_def456"
}
```

**Harness invocation** — function 8 `check-step-authorization`, once **per artifact path** of
the call:

```json
{
  "in": {
    "sessionId": "<current agent session of session_id>",
    "parentSessionId": "<parent, when the writer is a step session>",
    "artifactPath": "portfolio/epics/epic-payments.md",
    "action": "create"
  }
}
```

- `action` maps from `tool_name` via `tools.yaml` (`writeTools` verb map; `deleteTools` →
  `delete` — this binding declares none, and the core denies every `delete` unconditionally
  in v1 anyway: an explicit non-goal, see function 8's failure modes).
- Path extraction probes `tool_input` per `pathKeys` (`filePath`, `dirPath`, `path`);
  `multi_replace_string_in_file` yields `replacements[].filePath` — **one function-8
  invocation per distinct path** (see I8). Absolute host paths are relativized to the
  workspace root before invocation.
- A write whose every path falls **outside** the workspace artifact layout (e.g. the
  framework's own source) passes through — exit 0, empty output, unlogged: the harness
  governs the workspace data plane only. **Exception**: a path under the workspace logs
  directory is **denied**, never passed through — logs are harness-authored, single-writer
  (core function 8, invariant 6); no agent write there is ever legitimate.
- `session_id` at this hook resolves (host-session → current-agent-session rule) to the
  session the tool call runs in — the step (subagent) session for step writes. C7: an
  unregistered session's write passes through.

**Out** (stdout, exit 0)

- Every path allowed:

```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "allow" } }
```

- Any path denied (missing privilege — or unclean staging baseline, function 8 invariant 5):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "check-step-authorization denied: missing privilege: update epic (portfolio/epics/epic-payments.md)"
  }
}
```

**Output construction** — collapse over the per-path `AuthorizationReport`s: all allowed →
`allow`; any denied → `deny`, reason concatenating each denied path's
`authorization.failureMessage`. Harness errors → `deny` + error detail (never erring open).

**Preconditions** — function 8's own (ACL, layout, artifact schemas loaded; write verb
mapped).

**Postconditions**

- One log entry per authorization decision (per path), in the acting session's log.
- On deny, the write never lands: the workspace never sees unauthorized bytes.

**Invariants**

1. The actor derives from the registered session (function 8, invariant 1) — never from tool
   arguments.
2. Multi-path calls are all-or-nothing at the host surface (a single `permissionDecision`
   guards the whole tool call), while the journal stays per-path — 1 invocation = 1 entry.
3. Deny-by-default within the hook (as H2, invariant 1).

### H4 `PreToolUse` — harness-command class — mediated attribution

The mediated agent invocation surface for functions 3 (`resolve-step`) and 4
(`resolve-step-model`) — the orchestrator's loop calls in the SD. Same physical registration
as H2/H3; classified when `tool_name = run_in_terminal` **and** the command string invokes the
harness command entry point.

> **Accepted under the core's narrow tool-boundary-stamp exception**
> ([`../../core/spec.md`](../../core/spec.md), Invocation surfaces) — a tool-boundary rewrite
> of a model-authored argument is insufficient by the core's general rule, but this binding
> qualifies under the exception because all four of its conditions hold: (1) the stamped
> shape is fixed and framework-authored — the orchestrator's own instructions dictate the
> exact `harness.py <function> --workflow …` invocation verbatim, never open-ended agent
> phrasing; (2) any invocation already carrying `--session-id`/`--parent-session-id` is
> denied outright (Mechanics rule 2, below), never merely overwritten; (3) the
> [mediated-invocation backstop](../../core/spec.md#invocation-surfaces-one-command-system)
> is in force — functions 3–4 themselves reject an unresolvable or unregistered id
> regardless of what this hook stamped; (4) this remains an **explicit, interim**
> mechanism — see **I4** — pending a host-native surface that passes attribution outside
> model-visible arguments, which no host of this class (hook-mediated tool interception, no
> custom-tool session channel) is currently known to offer.

**In** (stdin)

```json
{
  "timestamp": "…",
  "hook_event_name": "PreToolUse",
  "session_id": "chat-session-guid",
  "tool_name": "run_in_terminal",
  "tool_input": { "command": "harness.py resolve-step --workflow verification", "…": "…" },
  "tool_use_id": "call_ghi789"
}
```

**Mechanics**

1. Classify: the command string matches the harness invocation pattern
   (`harness.py <function> …`); otherwise fall through to the **guarded-shell check**: a
   non-harness command on a `guardedShellTools` tool (`tools.yaml`) that textually references
   a workspace artifact-layout path or the workspace logs path is **denied** with the offending
   path named — an advisory-grade guard (textual matching is bypassable; C6's
   detect-and-remediate plane remains the guarantee) closing the routine write escape hatch
   (I9); anything else passes
   through. **Deny** a harness invocation whose function is not `resolve-step` or
   `resolve-step-model`: functions 3–4 are the only agent-invoked surface — every other
   function belongs to a hook boundary, and a model-authored call to one is never legitimate.
2. **Deny** any invocation whose command already carries session-attribution arguments
   (`--session-id`, `--parent-session-id`): model-authored attribution is never accepted.
3. Resolve `SessionTracker.resolve_current(sanitized(session_id))`. **Deny** if it returns
   `None` — never registered — there is no session to attribute this call to. (A resolved but
   already-closed session is not caught here — the adapter has no log access to know that; the
   harness core itself refuses it via C8 when the call actually runs — see Invariant 4.)
4. Rewrite via `updatedInput`: inject `--session-id <the resolved current session>`
   (host-observed, adapter-resolved) into the command. The executed invocation's attribution
   is then fully adapter-controlled — the model can neither choose nor forge it.

**Out** (stdout, exit 0)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "updatedInput": {
      "command": "harness.py resolve-step --workflow verification --session-id chat-session-guid-t2026-07-11t14-32-07-000z"
    }
  }
}
```

(The host validates `updatedInput` against the tool's input schema; the object must carry the
full rewritten `tool_input`, not a patch.)

**Invariants**

1. No harness command with model-authored session attribution ever executes (rule 2 above
   denies it).
2. The stamped id is resolved from the envelope `session_id` — the same host-observed value
   H0's opening derives from; never from any model-authored argument.
3. Functions 3–4 journal to the stamped session per the harness logging rules; the hook
   itself journals nothing (it is attribution plumbing, not a boundary function).
4. A never-registered session is never stamped: rule 3 (above, Mechanics) denies it before
   rule 4 would rewrite anything. A stamped-but-already-closed session is instead refused by
   the harness core itself (C8) when functions 3–4 actually run — this hook has no log access
   to pre-check that; it only guards against a session it has never seen at all.

### H5 `PostToolUse` — write class — write-ended

The commit gate (function 9): validates the just-landed staged write and either commits it
into workspace state or reverts it, feeding the failure back to the writing agent.

**Registration** (shared by H5/H6 — ONE `PostToolUse` entry):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "adapters/dispatch.sh PostToolUse vscode-github-copilot-chat",
        "cwd": "{{FRAMEWORK_DIR}}",
        "timeout": 60
      }
    ]
  }
}
```

**In** (stdin)

```json
{
  "timestamp": "…",
  "hook_event_name": "PostToolUse",
  "session_id": "chat-session-guid",
  "tool_name": "create_file",
  "tool_input": { "filePath": "/…/portfolio/payments/features/feature-refunds.md", "content": "…" },
  "tool_response": "Created file …",
  "tool_use_id": "call_def456"
}
```

**Harness invocation** — function 9 `check-step-artifact`, **once per tool call**, carrying
the call's whole artifact-path set (same path extraction as H3; the set is the atomic unit —
call-level atomicity is the core's, not assembled here):

```json
{
  "in": {
    "sessionId": "<current agent session of session_id>",
    "parentSessionId": "<parent when applicable>",
    "artifactPaths": ["portfolio/payments/features/feature-refunds.md"]
  }
}
```

Non-artifact paths are excluded from the set; a call whose every path is non-artifact passes
through (exit 0, empty output), as do non-write tools.

**Out** (stdout, exit 0)

- Valid (validated and committed):

```json
{ "continue": true }
```

- Reverted (invalid, discarded from staging):

```json
{
  "decision": "block",
  "reason": "check-step-artifact reverted portfolio/payments/features/feature-refunds.md: frontmatter.status: 'shipped' is not one of the enum values",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "The write was discarded (restored from HEAD). Rewrite the artifact to satisfy its schema and retry."
  }
}
```

**Output construction** — `outcome: valid` → plain success (the whole set's commit already
happened
harness-side; nothing to tell the model). `outcome: reverted` → `decision: "block"` with
`reason` concatenating each `artifactChecks[]` entry's path and `failureMessage` verbatim
plus its revert record — the host
feeds a blocked `PostToolUse` reason back to the model, which is exactly function 9's caller
usage ("the agent receives the failure message(s) and rewrites the artifacts"). A mixed
multi-path result does not exist at this surface: any invalid path reverted the whole set
(function 9, invariant 2), so the decision is always uniformly valid or uniformly discarded.
Harness errors →
`decision: "block"` + error detail.

**Preconditions** — function 9's own: the path resolves to an artifact schema; function 8
established the clean staging baseline at H3.

**Postconditions**

- C6 holds: state advanced by the validated commit, or the staged write was discarded — one
  log entry per write validation carrying the outcome (and the revert record when reverted).

**Invariants**

1. The revert is the HARNESS's git action (restore from `HEAD` / delete the new path) — the
   host cannot undo a tool call and is never asked to; `decision: block` only carries the
   message.
2. ONE function-9 invocation per tool call, carrying the whole path set — unlike H3's per-path
   function-8 fan-out: authorization is per-path, the commit gate is per-call (the set is the
   transaction).
3. The hook never returns `valid` without the commit having succeeded — commit failure is a
   `system-error` → block.

### H6 `PostToolUse` — dispatch class — step-ended

THE evaluation point (function 10): the dispatch returned, the step session has ended, the
state it left is final. Same physical registration as H5; classified by
`tool_name ∈ dispatchTools`.

**In** (stdin)

```json
{
  "timestamp": "…",
  "hook_event_name": "PostToolUse",
  "session_id": "chat-session-guid",
  "tool_name": "runSubagent",
  "tool_input": { "agentName": "qa-engineer", "model": "…", "prompt": "…" },
  "tool_response": "…the subagent's final report…",
  "tool_use_id": "call_abc123"
}
```

Note: the payload does **not** echo the subagent's `agent_id` — correlation of which step
ended relies on function 3, invariant 9 (one in-flight step per orchestrator session; see I7).

**Harness invocation** — function 10 `check-step-postconditions`, always invoked when
`tool_name` matches the binding's dispatch-tool list:

```json
{ "in": { "sessionId": "<current agent session of session_id>", "parentSessionId": null } }
```

Runs in the dispatching (orchestrator) session — the step session is already closed. Same
in-flight-step deduction as H2 (function 3, invariant 9) — no ACL check, no extra field: a
target with no matching in-flight step is already not-applicable, rendered as pass-through.
The host fires `SubagentStop` (H7 — the step's `end-session` + tracker pop)
**before** this `PostToolUse` — verified: the `SubagentStop` hook is awaited inside the
subagent's own tool-calling loop, hence inside the parent's dispatch tool call (I13(e)).
Defensively, this hook still resolves
the **stack base** (the orchestrator's turn session), not the raw top — correct under either
ordering.

**Out** (stdout, exit 0)

- Postconditions hold (`outcome: pass` — the step journals executed):

```json
{ "continue": true }
```

- Postconditions fail (`outcome: fail` — the step is not journaled executed):

```json
{
  "decision": "block",
  "reason": "check-step-postconditions fail: [report_exists] fail — no artifact matches 'review-report'",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Per reports-handling: re-resolve (resolve-step) — the cursor returns the failed step; do not surface step details to the user."
  }
}
```

**Output construction** — `pass` → plain success (the orchestrator proceeds to the next
`resolve-step` per its instructions). `fail` → `decision: "block"` with `reason` serializing
the `conditionChecks[]` (as H2's construction); `additionalContext` restates the injected
`reports-handling` reaction so the failure stays inside the workflow. Harness errors →
block + error detail.

**Preconditions** — function 10's own: the step was dispatched from a correlated unresolved
`step-resolution` entry; its session has ended.

**Postconditions**

- One `check-step-postconditions` entry — the exact input of function 3's cursor — in the
  dispatching session's log. No artifact is touched.

**Invariants**

1. Evaluated ONCE per step pass, at this hook only — H7's `SubagentStop` never re-evaluates
   (function 10, invariant 2).
2. The hook never converts a `fail` into a user-facing verdict: the block reason addresses
   the orchestrator (re-resolution), per function 3, invariant 7.

### H7 `SubagentStop` / `Stop` — session-ended, best-effort closure

The session-ended boundary (function 11, `end-session`): resolves the ending session and
closes its log with a final, journaled entry — best-effort, since not every host termination
fires this hook (a crash, a force-quit host never does). No host-visible effect either way:
also registered for operational visibility (adapter stderr diagnostics); always exit 0, empty
stdout, never `decision: block` (a `SubagentStop`/`Stop` block would force the agent to
continue — a real host capability the harness deliberately does not use: step outcome is
H6's, and retry is re-resolution through the orchestrator, never a forced extra turn).

**Registration**

```json
{
  "hooks": {
    "SubagentStop": [
      {
        "type": "command",
        "command": "adapters/dispatch.sh SubagentStop vscode-github-copilot-chat",
        "cwd": "{{FRAMEWORK_DIR}}",
        "timeout": 10
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "adapters/dispatch.sh Stop vscode-github-copilot-chat",
        "cwd": "{{FRAMEWORK_DIR}}",
        "timeout": 10
      }
    ]
  }
}
```

**In** — `SubagentStop`: envelope + `agent_id`, `agent_transcript_path`, `stop_hook_active`;
`Stop`: envelope + `stop_hook_active`.

**Harness invocation** — function 11 `end-session`, once the ending session resolves:

1. `SubagentStop` — the ending session is `sanitized(agent_id)` directly: the step session
   that just ended is exactly this event's own id, no resolution needed. The adapter then
   calls `SessionTracker.pop_current(sanitized(session_id))`, restoring the dispatching
   orchestrator session as current for this `session_id` (see
   [Session identity binding](#session-identity-binding)) — without this, the orchestrator's
   next mediated call (H4) would misattribute to the now-closed step session.
2. `Stop` — resolve the ending orchestrator session via
   `SessionTracker.resolve_current(sanitized(session_id))` first, then close THAT id — never
   the raw `session_id` itself (the conversation may still open later agent sessions; only
   the one that just ended closes). The adapter then **clears** the stack for this
   `session_id`: the turn is over, and an emptied tracker makes any later firing in this
   conversation under a non-framework agent (which fires no H0) resolve to `None` — the
   correct C7 pass-through — instead of a stale framework session. The next framework
   `UserPromptSubmit` (H0) starts the stack fresh anyway.

```json
{ "in": { "sessionId": "chat-session-guid-t2026-07-11t14-32-07-000z" } }
```

If resolution finds no current session (never registered), no harness function is invoked —
pass-through, as elsewhere in this binding.

**Out** (stdout, exit 0) — none: `end-session`'s outcome is never surfaced to the host,
success or error alike; this hook has no host-visible effect either way.

**Preconditions** — function 11's own: the surrounding mechanism (this hook) has observed the
session ending.

**Postconditions**

- The session's log carries the closing entry as its last line, when it was open — a real
  journal write, unlike H7's previous purely-observational framing: one harness function now
  genuinely runs here.
- From this entry on, C8 makes the harness core refuse any further invocation against this
  `sessionId` — a defense that survives even if this adapter's own `SessionTracker` later
  mis-resolves or goes stale, because it is enforced by the core reading its own log, not by
  anything this adapter tracks.

**Invariants**

1. Best-effort, not guaranteed: a session whose end this hook never observes (host crash,
   force-quit) simply never closes — not an error state anywhere else in this contract
   (function 11, invariant 3).
2. Idempotent: closing an already-closed or never-opened session produces the same `ended`
   outcome and no additional entry — a duplicate `Stop`/`SubagentStop` firing (host
   re-delivery) changes nothing (function 11, invariant 2).
3. This hook still journals nothing of its OWN — the journal entry belongs to function 11,
   attributed to the closed session's own log, exactly like every other function's entries.

---

## Invocation plumbing and contract layering

One hook firing traverses four seams. Each seam has exactly one governing contract — the
pre/postconditions and invariants in H0–H7 above are written against these seams: host-facing
clauses (stdin shape, stdout decision, firing rules) bind seams 1/4; harness-facing clauses
(functions invoked, journal postconditions, C7 gating) bind seam 3.

```text
VS Code hook engine
  │ (1) stdin: host event JSON ── contracts/hook-stdin.schema.json  [adapter-owned]
  ▼
dispatch.sh <Event> vscode-github-copilot-chat [<agentSlug>]
  │ (2) exec: argv (event, env, optional scoping agent) + stdin unchanged
  │     — no contract of its own: a pure forwarder that locates this adapter's own
  │     hook entry by the env argument (adapters/<env>/adapter.py)
  ▼
adapters/vscode-github-copilot-chat/adapter.py hook --event <Event> [--agent <agentSlug>]
  │ (3) this adapter (host-aware, inside the harness component):
  │     its own binding classes the event and maps its fields (tools.yaml — data);
  │     its own code normalizes (Boundary), resolves the session, gates (C7),
  │     sequences (registration first), fans out per path, aborts on failure →
  │     invokes the harness function commands — harness.py, 1 command per function,
  │     hook/host-blind — each governed by
  │     contracts/api/<function>.input|output.schema.json; every completed
  │     invocation journals per contracts/log-entry.schema.json (the report IS
  │     the out object — harness report identity rule); this adapter's renderer maps the
  │     reports to the host decision
  ▼
  │ (4) stdout: host decision JSON ── contracts/hook-stdout.schema.json [adapter-owned]
  ▼
VS Code hook engine (permissionDecision / decision:block / additionalContext / updatedInput)
```

- **Seam 1 and 4 are this adapter's normative I/O contracts** —
  [contracts/hook-stdin.schema.json](../../../adapters/vscode-github-copilot-chat/contracts/hook-stdin.schema.json) (the envelope +
  per-event stdin payloads H0–H7 consume) and
  [contracts/hook-stdout.schema.json](../../../adapters/vscode-github-copilot-chat/contracts/hook-stdout.schema.json) (the per-event
  decision objects H0–H7 emit). They formalize the verified host facts; the harness's
  fail-fast configuration load at instantiation
  validates the adapter binding (hooks.yaml, tools.yaml, models.yaml, and these two
  contracts' presence) like any other configuration (harness core spec — Internal validation).
- **Seam 3 splits between this adapter and the harness core, with exactly ONE edge
  crossing it.** The event→boundary→functions orchestration is this adapter's OWN code
  (`HookBinding`, `HookClassifier` + `EventClass`, `SessionTracker`, `Adapter`,
  `HookRenderer` — see [`adapter-src-classes.puml`](adapter-src-classes.puml)),
  parameterized by its own binding data (its own YAML, loaded
  with its own tools — never the harness's `ConfigLoader`). The invoked surface is the
  **harness core**: twelve function commands (`harness.py`, one per function, hook/host-blind),
  their per-function API contracts, and the log-entry contract — nothing adapter-specific
  may leak into them (C4). Basic layering: this adapter depends on the command API and
  nothing else — no `services`, `stores`, or `config` — so framework-agent gating (C7) and
  step-resolution correlation are validated INSIDE the invoked commands (which already hold
  `AccessControlList` / the session log for their own reasons), never pre-checked
  adapter-side. This adapter's rendering emits the per-event decision shapes of seam 4
  (structured stdout, not exit-2 — I5).
- **Seam 2 has no contract**: `dispatch.sh` adds nothing but argv shaping.

**Component vs planes.** "No hook logic outside the harness" reads at the **component**
level (the harness repository); within the component the split is explicit and structural, and basic
layering applies between them: an adapter depends on a system's public API, never its
internals. The **harness core** (`src/` + `harness.py`) exposes exactly one command per
function — twelve commands, hook/host-blind by package graph, the surface agents
(`resolve-step`, `resolve-step-model`) invoke DIRECTLY, with
no adapter detour. This **adapter** (`adapters/vscode-github-copilot-chat/`) owns
everything host-aware for this host: classification + session-tracking + rendering code,
its OWN declarative YAML, its OWN tools, the two seam contracts, and a thin
envelope-parse/field-mapping edge. Its only dependency into the harness core is the command
API itself — never `ConfigLoader`, never `AccessControlList`, never the session log;
`dispatch.sh` only keeps host `command` strings short and stable. There is only ONE adapter
today — no shared abstraction is factored out speculatively; a second host, if it ever
exists, is the point at which genuinely common code gets pulled out, not before.

**Should `dispatch.sh` become a CLI under `cli/`?** No — verdict: keep the shim.
The command surfaces already exist: `harness.py` is the pure function CLI — one executable
entry point per function, each under a JSON I/O contract, needing no event→boundary
normalization — and this adapter's hook entry is the host-facing surface. `dispatch.sh`'s
only reason to exist is host-side ergonomics: hook `command` strings stay one short, stable
line while this adapter's invocation evolves. Promoting a two-argument exec shim to a CLI would
add a second command surface with no second consumer — overkill. One refinement is warranted
(not applied here — the shim is harness-wide plumbing, not this adapter's file): the shim
must forward a third optional argument (H0's scoping agent slug), which its current
2-argument contract doesn't state — noted in I13.

---

## Rendered registration

Three render targets, all from this adapter's sources at bundle render time,
`{{FRAMEWORK_DIR}}` substituted with the absolute framework root (the host resolves hook
`cwd` against `$HOME` by default — a relative command path would break; see host facts):

1. **Workspace hooks file** — `hooks.yaml` in this folder is the YAML source of truth,
   rendered to ONE workspace file `.github/hooks/safe-harness.json`:

```json
{
  "hooks": {
    "SubagentStart": [ { "type": "command", "command": "adapters/dispatch.sh SubagentStart vscode-github-copilot-chat", "cwd": "{{FRAMEWORK_DIR}}", "timeout": 60 } ],
    "PreToolUse":    [ { "type": "command", "command": "adapters/dispatch.sh PreToolUse vscode-github-copilot-chat",    "cwd": "{{FRAMEWORK_DIR}}", "timeout": 60 } ],
    "PostToolUse":   [ { "type": "command", "command": "adapters/dispatch.sh PostToolUse vscode-github-copilot-chat",   "cwd": "{{FRAMEWORK_DIR}}", "timeout": 60 } ],
    "SubagentStop":  [ { "type": "command", "command": "adapters/dispatch.sh SubagentStop vscode-github-copilot-chat",  "cwd": "{{FRAMEWORK_DIR}}", "timeout": 10 } ],
    "Stop":          [ { "type": "command", "command": "adapters/dispatch.sh Stop vscode-github-copilot-chat",          "cwd": "{{FRAMEWORK_DIR}}", "timeout": 10 } ]
  }
}
```

1. **Per-orchestrator agent-scoped block** (H0) — rendered into each framework
   orchestrator's `.agent.md` frontmatter (`hooks:` block), the orchestrator's own slug as
   the trailing dispatch argument:

```json
{
  "hooks": {
    "UserPromptSubmit": [ { "type": "command", "command": "adapters/dispatch.sh UserPromptSubmit vscode-github-copilot-chat <orchestrator-slug>", "cwd": "{{FRAMEWORK_DIR}}", "timeout": 30 } ]
  }
}
```

1. **Workspace settings** — `.vscode/settings.json` in the workspace carries the
   [required settings](#required-vs-code-settings) (`chat.useHooks: true`).

---

## Inconsistencies with the harness spec

Findings surfaced by rooting the harness in this concrete host. *Status:* all findings are
now **resolved or integrated** except the permanently tracked items in I4 (host-native
attribution surface) and I13(a)/(d) (host verifications only live experimentation can close).

- **I1 — RESOLVED.** The SD carries a distinct **Adapter** participant labeled with this
  host's actual events (H0–H7, no `SessionStart`), and the core spec's Boundary
  Normalization now states that the correlation classifier is the **fallback** for hosts
  without structurally distinct session-open events — a host with distinct events (this one)
  classifies by them directly, the correlation resolving only *which step*, validated inside
  function 0 (see I15); the actor-heuristic fallback is likewise demoted to hosts that lack a
  direct actor field.
- **I2 — RESOLVED: the spec's session nouns no longer conflate the conversation with the
  agent session.** This
  adapter binds sessions per the framework definition: **1 agent session = 1 execution of 1
  agent until it returns** (to the user / to the orchestrator); the next prompt opens a new
  agent session. On this host the turn IS that unit, and H0's per-request firing point maps
  it 1:1. The harness spec previously said "an orchestrator session spans many
  workflow instances over its lifetime" — lifetime language that described the
  **conversation** (the host `session_id`), not the agent session. The spec's Session
  definition and invariant 9 now state the corrected cardinality: an orchestrator session
  drives AT
  MOST ONE workflow instance (one workflow end = one return to the user ends the session), a
  half-finished instance legitimately continues under a LATER agent session (function 3's
  latest-open-instance deduction + the single-driver invariant already carry this), and no
  session's log ever carries entries of two instances — the instance view spans sessions,
  never the converse.
- **I3 — RESOLVED: the core now states the injection semantics normatively.** Boundary
  Normalization carries the rule: functions 1–2/6–7 return refs; the embedding mechanism
  renders them (instructions inlined, skills as load directives — never an inline dump); a
  host MAY scope injected context to the current request, so re-resolution at every
  session-started / step-started boundary is **mandatory**, not an optimization. This
  adapter's H0/H1 output construction realizes exactly that rule under the verified
  [context-injection semantics](#context-injection-semantics).
- **I4 — RESOLVED: the core now names an explicit, narrow exception for this class of host.**
  Functions 3–4 require host-observed session attribution "outside the tool arguments visible
  to the agent"; a tool-boundary rewrite of a model-authored argument is insufficient by the
  core's general rule. This host's only mechanism is exactly that — `PreToolUse`
  `updatedInput` (H4) — and a dedicated-tool alternative was investigated and set aside: MCP
  is host-agnostic and carries no standard, model-inaccessible "which chat session called
  this" field a server could trust over the model's own arguments, and no VS Code extension
  API surfacing one was found. Rather than leave this an unresolved contradiction against a
  mechanism that likely doesn't exist for hosts of this shape, the core's mediated attribution
  rule now states a **narrow tool-boundary-stamp exception** (fixed framework-authored
  invocation shape, deny-on-model-authored-attribution, the mediated-invocation backstop, and
  explicit interim-not-permanent status) that H4 satisfies point-for-point — see H4, below,
  and [`../../core/spec.md`](../../core/spec.md), Invocation surfaces. The real fix — a
  host-native surface that passes attribution outside model-visible arguments — remains
  tracked, not scheduled.
- **I5 — RESOLVED: the exit-2-centric dispatch contract is gone.** `dispatch.sh` and
  `adapters/README.md` now state the real control surface — **structured stdout JSON on exit
  0** (`permissionDecision` for H2/H3, `decision: block` for H5/H6, `additionalContext` for
  H0/H1, `updatedInput` for H4), rendered by this adapter's `HookRenderer`; exit 2 remains
  only the hard-failure fallback — and the shim forwards to this adapter's own hook entry
  (the core has no `hook` command, I14), with the optional third scoping-agent argument H0
  needs (I13(c)).
- **I6 — RESOLVED: enforcement claims are scoped per plane.** The core's functions 5 and 8
  each carry an explicit **Enforcement scope** paragraph: "THE enforcement point" holds while
  the boundary fires; a fail-open host (timeout, spawn failure — this host's documented
  behavior) can bypass the function, and the bypass is bounded — unmediated writes never
  commit (fn 8) and an ungated step never journals executed (fn 5) — with C6's
  detect-and-remediate plane owning residual cleanup, exactly as C6's own graded guarantee.
- **I7 — RESOLVED: the core names the correlation reliance.** Function 10's In now states
  that step-ended correlation **normatively relies on function 3, invariant 9** (one
  in-flight step per orchestrator session): a host payload need not echo the step session's
  id — this host's `PostToolUse` (no `agent_id` echo) is fully supported without a
  `tool_use_id → agent_id` pairing.
- **I8 — RESOLVED: one write ≠ one path, modeled end to end.** Function 8 takes exactly one
  `artifactPath` (authorization is per-path — the adapter fans out one invocation per distinct
  path, 1 invocation = 1 entry preserved); function 9 is **set-based** (`artifactPaths`):
  ONE invocation per tool call carries the whole set, which validates and commits (or is
  discarded) atomically. The adapter-binding contract (`tools.conf.schema.json`) now carries
  `nestedPathKeys` — dotted path expressions with `[]` array fan-out
  (`replacements[].filePath`) — plus the previously undeclared binding keys
  (`hostStepSessionKeys`, `hostStepActorKeys`, `mediatedCommandTools`, `mediatedCommandKeys`),
  and the shipped `tools.yaml` validates against it.
- **I9 — RESOLVED: the guarded-shell class exists.** The binding vocabulary now carries
  `guardedShellTools` (contract + this binding's `tools.yaml`: `run_in_terminal`,
  `create_and_run_task`): a non-harness command on a guarded tool that textually references a
  workspace artifact-layout path or the logs path is **denied** at the pre-tool boundary (H4
  mechanics, rule 1). Advisory-grade by declaration — textual matching is bypassable, so C6's
  graded guarantee (detect-and-remediate + CI gate) remains the integrity claim; the guard
  closes the routine escape, not the adversarial one.
- **I10 — The previous registration set was baggage for this host.** The old adapter
  registered camelCase CLI-style events (`sessionStart`, `userPromptSubmit`, `preToolUse`,
  `postToolUse`, `stop`, `sessionEnd`) rendered to `.copilot/hooks.json` — the Copilot **CLI**
  surface, not this host's: VS Code reads `.github/hooks/*.json`, uses PascalCase event
  names, has no documented native `SessionEnd`, and its 8-event set includes `SubagentStart`/
  `SubagentStop` (absent from the old registration entirely — the step-started boundary was
  unreachable). Replaced wholesale here.
- **I11 — RESOLVED: the stale references are gone.** `Makefile` now renders THIS adapter's
  `hooks.yaml` to `.github/hooks/safe-harness.json` (the host's real discovery location, I10);
  `adapters/README.md` names `vscode-github-copilot-chat/`, the structured-stdout control
  surface, and the three-argument dispatch contract. The embedding framework's
  `conf/model-profiles.conf.yaml` comments live in the framework repository — tracked there,
  not here. `builds/github-copilot/` is a
  different artifact (the plugin bundle), not this adapter — unaffected.
- **I12 — `models.yaml` verified compatible.** `runSubagent`'s `model` argument on this host
  is the exact string `"Model Name (copilot)"` — the existing binding shape is correct and
  carried over unchanged.
- **I13 — Open host verifications + shim gap.** (a) Whether a **steering** message submitted
  mid-execution fires `UserPromptSubmit` on the native panel path: unverified — H0's rule is
  correct under both outcomes (see [Agent-session semantics](#agent-session-semantics)), so
  this only affects whether some firings open engagement sessions that never act. (b)
  **RESOLVED** — verified directly against `microsoft/vscode` on BOTH subagent paths: the
  built-in subagent tools
  (`extensions/copilot/src/extension/tools/node/executionSubagentTool.ts`,
  `searchSubagentTool.ts`) construct the subagent's own
  `Conversation` as `new Conversation(parentSessionId, …)` — the **parent's**
  `sessionId`, never the subagent's own `subAgentInvocationId` — and the full `runSubagent`
  chat pipeline does the same: `chatParticipantRequestHandler.ts` resolves
  `actualSessionId = historySessionId ?? request.sessionId ?? generateUuid()`, where a
  subagent request's `request.sessionId` IS the parent conversation's id
  (`defaultIntentRequestHandler.ts` uses that very field as the "link back to the parent
  session" and reserves `subAgentInvocationId` for trajectory/log-file linking only). So a
  subagent's own
  tool-call hooks carry the **same shared `session_id`** as its dispatching session, never a
  distinct one; `agent_id`/`subAgentInvocationId` surfaces in hooks solely as
  `SubagentStart`/`SubagentStop`'s own fields. The host docs' pervasive "agent session"
  wording does NOT mean a per-agent-execution session — the envelope `session_id` is the
  conversation, full stop. This confirms this
  binding's `tools.yaml` assumption was correct, and rules out the "distinct id per subagent"
  branch the resolution rule used to hedge for — see the corrected
  [Session identity binding](#session-identity-binding) below (`SessionTracker` is now a
  stack, not a flat pointer, precisely because `session_id` repeats across nesting). (c)
  **RESOLVED** — `dispatch.sh` now declares the three-argument contract
  (`<event> <env> [<agent>]`), forwarding the optional scoping-agent slug H0 passes.
  (d) Hooks are a preview feature — the setting names (`chat.useHooks`,
  `chat.hookFilesLocations`) and event set may drift; the harness's fail-fast adapter-binding
  validation at instantiation is the intended drift detector. (e) **RESOLVED** — verified
  against `microsoft/vscode`: the `SubagentStop` hook is executed and awaited **inside the
  subagent's own tool-calling loop** (`toolCallingLoop.ts` —
  `executeHook('SubagentStop', …)` runs as the loop concludes), i.e. inside the parent's
  dispatch tool call — so it necessarily completes before the parent's `PostToolUse` for that
  tool fires. H7-before-H6 holds by host construction; H6's stack-base resolution stays as
  defense in depth.
- **I14 — INTEGRATED: this adapter owns host-event orchestration and rendering.**
  H0–H6's **output
  construction** — report `outcome` → `permissionDecision` mapping, `conditionChecks[]`
  serialization into reasons, instruction/skill refs rendered to inlined content + load
  directives, `updatedInput` stamping — is real behavior that must live neither in the
  function commands (host contamination) nor in shell. Adopted resolution, now integrated in
  the harness core spec and this adapter's own class diagram: the harness core drops the
  `hook` command — `harness.py`
  is **twelve pure function commands**, the direct surface agents invoke — and
  this adapter's OWN code (there is no shared-adapter package: with a single host today, all
  of it lives directly under `adapters/vscode-github-copilot-chat/`) owns
  event classification (`HookClassifier` + `EventClass` — the seven harness boundaries plus
  `MEDIATED_ATTRIBUTION` for H4 and `PASS_THROUGH`, so every firing classifies into exactly
  one closed case), session identification
  (`SessionTracker`), orchestration (`Adapter`: sequencing with registration first,
  per-path fan-out, abort-on-failure), and rendering (`HookRenderer`, governed by the
  adapter-owned seam-4 stdout contract). If a second host is ever added, whatever proves
  genuinely common factors out then, not speculatively now; `src/` stays host-blind
  by package graph either way.
- **I15 — INTEGRATED: the adapter's only dependency is the command API.** Refining
  I14 further — basic layering: an adapter depends on a system's public surface, never its
  internals. The adapter holds NO dependency on `services`, `stores`, or `config` —
  not even its own binding is loaded through `ConfigLoader` anymore; it uses its own tools.
  Two things this forces, now integrated in the harness def and the CD: (a) session
  identification (H0/H1) uses only host-observed event data (the envelope's own `timestamp`)
  plus the adapter's own private `SessionTracker` record — never a log read; (b)
  framework-agent gating (C7) needs no `AccessControlList` dependency **adapter-side**:
  the CORE owns it — `SessionLifecycle` checks a root session's `agent` against the ACL's
  declared actors (function 0's membership gate, the session plane's one ACL lookup), and a
  step session's gate is its step-resolution correlation (only a configured, ACL-validated
  workflow actor is ever
  resolved there in the first place). The adapter itself never touches the access control
  list; core-side, the ACL serves two distinct purposes in two services — membership at
  `SessionLifecycle` (function 0) and privilege/grant checking at
  `StepAuthorizationChecker` (function 8).
