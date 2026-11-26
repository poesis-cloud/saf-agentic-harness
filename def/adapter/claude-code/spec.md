# Adapter — `claude-code`

The host binding for the **Claude Code** agent runtime: the `claude` CLI and the surfaces that
run it.

This document is the adapter's canonical specification. It binds the runtime's **hooks** to
the boundaries of [`../../core/spec.md`](../../core/spec.md) (Boundary Normalization). H0–H7
name those boundaries as bound in [Hook → boundary binding](#hook--boundary-binding). The
harness function contracts are the core's and are not restated here.

**Shared adapter base.** The gating logic — classification through `tools.yaml`, session
tracking, sequencing the function commands, rendering reports into decisions — is the shared
base [`adapters/common/saf_host_adapter`](../../../adapters/common/saf_host_adapter), which the
[`github-copilot`](../github-copilot/spec.md) adapter runs too. Its behavior is specified there
(sections *Adapter architecture*, *Session identity*, *The hooks*); this document states only
what Claude Code binds differently. Where it says nothing, the Copilot specification applies
as written, with this runtime's event and field names.

## Sources

| Host fact | Source |
| --- | --- |
| Hook events, input/output fields, settings and plugin formats | [Claude Code hooks reference](https://code.claude.com/docs/en/hooks), [plugins reference](https://code.claude.com/docs/en/plugins-reference) |
| **Actual** payload shapes, tool names, subagent identity, decision effects (authoritative where they differ from the docs) | Payloads captured on Claude Code **2.1.323** (Linux, `claude -p`, plugins loaded with `--plugin-dir`): [`tests/functional/adapter_claude_code/fixtures/claude-code-linux-2.1.323.jsonl`](../../../tests/functional/adapter_claude_code/fixtures/claude-code-linux-2.1.323.jsonl) |

## Host facts the design rests on

1. **Config.** A plugin's hooks are `hooks/hooks.json`:
   `{"hooks": {"<Event>": [{"matcher"?: "<tool pattern>", "hooks": [{"type": "command",
   "command": "...", "timeout": <seconds>}]}]}}`. A command runs through the shell, with the
   session's folder as its working directory and the plugin's root in `CLAUDE_PLUGIN_ROOT`, its
   persistent data folder in `CLAUDE_PLUGIN_DATA`.
2. **Every payload is snake_case** and carries `session_id` (the conversation), `cwd`,
   `transcript_path` and `hook_event_name`. Tool events carry `tool_name`, `tool_input` (an
   object) and `tool_use_id`, the same on a call's pre- and post-tool events; `PostToolUse`
   adds `tool_response`.
3. **Subagents.** Every event fired inside a subagent carries the conversation's `session_id`
   **and** that subagent's own `agent_id` and `agent_type`. `SubagentStart`/`SubagentStop` carry
   the subagent's `agent_id`/`agent_type` but **no dispatcher id**, also for a subagent a
   subagent dispatched (nested dispatch works). Observed order at a step's return:
   `SubagentStop`, then the dispatcher's `PostToolUse` of the dispatch, whose `tool_response`
   names the returned `agentId`.
4. **The selected agent is observed.** The main thread's events carry the conversation's
   selected agent as `agent_type` (`claude --agent <name>`, or the `agent` setting),
   `UserPromptSubmit` included. Plugin agents are namespaced `<plugin>:<agent>`, in `agent_type`
   and in the dispatch's `subagent_type`. Hooks declared in a plugin agent's frontmatter did
   **not** fire (2.1.323), for the main agent or a subagent.
5. **Output.** Stdout is ONE JSON object (exit 0). Event-specific fields sit in
   `hookSpecificOutput`, with `hookEventName` naming the event. `PreToolUse` decides through
   `permissionDecision` and `permissionDecisionReason`; the reason reaches the model verbatim
   (observed). `SessionStart`, `UserPromptSubmit`, `SubagentStart` and `PostToolUse` take
   `additionalContext`, which reaches the model (observed). A `PostToolUse`
   `decision: "block"` shows its `reason` to the model next to the result; a built-in tool's
   result cannot be replaced. `systemMessage` is shown to the user.
6. **Context size.** An `additionalContext` over ~10,000 characters is stored in a file, and the
   model receives only a preview and the file's path (observed, 48 KB).
7. **Failure semantics.** Only **exit 2** blocks (`PreToolUse`: the call is denied with stderr
   as the reason). Exit 1, a crash and a **timeout** are non-blocking errors: the call
   proceeds — they **fail open** (observed). Exit 2 at `Stop`/`SubagentStop` makes the agent
   continue, and at `UserPromptSubmit` erases the prompt.
8. **Tools.** Files: `Read`, `Write` (`file_path`, `content`; creates missing folders, overwrites
   an existing file), `Edit` (`file_path`, `old_string`, `new_string`), `NotebookEdit`
   (`notebook_path`). Shell: `Bash` (`command`, `description`), on every OS. Search: `Grep`,
   `Glob`. Dispatch: `Agent` (`subagent_type`, `description`, `prompt`, optional `model`,
   `run_in_background`). Also `Skill`, `AskUserQuestion` and MCP tools.
9. **Dispatch mode.** A dispatch without `run_in_background` was observed running in the
   background (`tool_response.status: "async_launched"`); with `run_in_background: false` it
   runs synchronously.
10. **Models.** The `Agent` tool's `model` accepts only the aliases `sonnet`, `opus`, `haiku`,
    `fable` (observed: anything else is a validation error); each alias runs the account's
    current model of that family.

## Hook → boundary binding

| Runtime event | Boundary | Harness function(s) | Decision on stdout |
| --- | --- | --- | --- |
| `SessionStart` | — (announce) | none | `additionalContext`: framework root, workspace, the harness command lines. Stateless. |
| `UserPromptSubmit` (main thread, `agent_type` set) | **H0** session-started + context | `start-session` (turn), `resolve-workflow-instructions`, `resolve-workflow-skills` | `additionalContext` = hint + workflow context |
| `SubagentStart` | **H1** step-started | `start-session` (step, parent = the correlated dispatcher), `resolve-step-instructions`, `resolve-step-skills` | `additionalContext` = hint + step context |
| `PreToolUse` · `Agent` | **H2** step-starting | `check-step-preconditions` | deny on fail; deny unless `run_in_background: false` |
| `PreToolUse` · `Write`/`Edit`/`MultiEdit`/`NotebookEdit` | **H3** write-starting | `check-step-authorization` per target | deny unless the target is allowed |
| `PreToolUse` · `Bash` naming `harness.py` | **H4** agent harness request (hook-executed) | `resolve-step` / `resolve-step-model` | deny whose reason is `SAFe agentic harness RESULT (<fn>): …` + the report |
| `PreToolUse` · other `Bash` | I9 shell guard | none | deny when the command names the workspace or `logs/` |
| `PostToolUse` · write tool | **H5** write-ended | `check-step-artifact` | `decision: block` + reason + retry directive on revert or error |
| `PostToolUse` / `PostToolUseFailure` · `Agent` | **H6** step-ended | `check-step-postconditions` (dispatcher's session) | `additionalContext` on fail |
| `PostToolUse` · any (first one only, if H0 context never landed) | H0 fallback | `resolve-*-instructions/skills` | `additionalContext` with load directives |
| `SubagentStop` | **H7** worker session ended | `end-session` (step) | none |
| `Stop` | **H7** turn session ended | `end-session` (the turn) | none |
| `SessionEnd` | **H7** closure | `end-session` for the conversation and every descendant | none |

The adapter never edits an agent's command (no `updatedInput`) and never emits `allow`.

## Session identity

- **Host session id.** `tools.yaml` reads `agent_id` first, then `session_id`
  (`hostSessionKeys`): inside a subagent the host session is the subagent; on the main thread
  it is the conversation. The conversation itself is `hostRootSessionKeys: [session_id]`.
- **Turn session (H0):** `<session_id>-t<prompt_id>`, opened at `UserPromptSubmit` for the agent
  the payload names (`hostTurnAgentKeys: [agent_type]`, namespace stripped). No agent-scoped
  hook is needed (host fact 4): a conversation with no selected agent, or whose agent is not a
  framework orchestrator (`start-session` answers `not-applicable`), stays ungoverned. The
  turn ends at `Stop`; the next prompt opens a new turn.
- **Worker session (H1):** the subagent's `agent_id`.
- **Dispatcher correlation (H1).** `SubagentStart` names no dispatcher (host fact 3), so the
  shared core runs with `correlate_dispatches`: every dispatch a `PreToolUse` lets through is
  recorded as *pending* under the conversation (dispatcher host id, `subagent_type`,
  `tool_use_id`), and dropped at that dispatch's post-tool event. A `SubagentStart` claims the
  oldest pending dispatch of its `agent_type`:
  - none → the subagent was not dispatched by a session the adapter saw: it stays
    **ungoverned** (as a Copilot `not-applicable`);
  - pending dispatches of that agent from **several** dispatchers → the subagent cannot be
    attributed: it is recorded **unregistered** under the conversation (fail closed: its writes
    and dispatches are denied);
  - otherwise the claimed dispatcher is the parent, and is remembered for that subagent's
    `SubagentStop` (which names no dispatcher either), where it arms H6's ignored-return marker.
- **Records** live in the adapter's state folder: `SAF_HARNESS_STATE_DIR`, else
  `$CLAUDE_PLUGIN_DATA/state` in plugin mode, else
  `$XDG_STATE_HOME/saf-agentic-harness/claude-code` (`%LOCALAPPDATA%` on Windows). Stamping,
  unregistered and ungoverned sessions behave as on Copilot.

## Binding specifics

- **H2.** `syncDispatchFlagKeys: [run_in_background]`: a governed dispatch is denied unless the
  argument is present and `false` (host fact 9).
- **H3.** `Write` is an **upsert tool** (`upsertTools`): its action is `update` when the target
  exists and `create` otherwise, per target, at the pre-tool boundary. The missing-folder
  adaptation of the shared core is harmless here (`Write` creates folders itself).
- **H4.** The request line is parsed with POSIX quoting on every OS (`Bash`). Model-authored
  attribution is denied, as on Copilot. A `resolve-step-model` result also names the host model
  `models.yaml` binds the resolved profile to when it differs from the slug: "dispatch the
  resolved profile '<slug>' with the model '<alias>'".
- **H5.** The write already ran and Claude Code cannot replace a built-in tool's result, so a
  reverted or unvalidated write answers `decision: block` with the `WRITE REJECTED` /
  `WRITE OUTCOME UNKNOWN` text as `reason`, and the retry directive as `additionalContext`.
- **Context size.** Every `additionalContext` is bounded at 10,000 bytes (host fact 6): H0/H1
  inline instruction bodies when they fit, else render them as load directives; a context that
  fits neither is not injected (and not marked injected, so the H0 fallback delivers it); a
  post-tool context is truncated.

## Fail-closed entry

`adapter.py hook --event <Event>` answers as the Copilot entry does, with one difference forced
by host fact 7: a bootstrap failure exits **2 at `PreToolUse` only**, and **1** at every other
event. Exit 2 elsewhere would trap a stopping agent or erase the user's prompt, and the gating
event already denies every tool call of a session whose bootstrap is broken. The launcher and
the shim follow the same rule:

- an adapter that cannot even load (a broken runtime: an import fails) exits 2 at `PreToolUse`
  and 1 elsewhere, before any hook handling;
- the launcher relays the adapter's answer only when the adapter exited 0 with an empty stdout
  or ONE JSON object (anything else Claude Code would read as plain text and let the call run).
  Any other outcome — a non-zero exit, a signal, unreadable output — becomes a structured `deny`
  at `PreToolUse`, a `systemMessage` warning at `SessionStart`, and exit 1 elsewhere.

## Model binding

[`models.yaml`](../../../adapters/claude-code/models.yaml) maps every profile slug of the
framework catalogue to an alias the `Agent` tool accepts (host fact 10). Several slugs may share
an alias (`claude-opus-4.8` and `claude-opus-4.8-fast` both route to `opus`): the profile still
drives the harness's choice, and the alias runs the account's current model of that family. Every
hook load refuses a catalogue that names a slug `models.yaml` does not bind, as on Copilot.

## Plugin mode

The harness ships as its own Claude Code plugin, **`poesis-saf-agentic-harness`**, beside the
framework's Claude Code plugin; [`render_plugin.py`](../../../adapters/claude-code/render_plugin.py)
renders it (`make plugin-claude`):

| Path | Content |
| --- | --- |
| `.claude-plugin/plugin.json` | the manifest: `name`, the harness `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords` |
| `hooks/hooks.json` | every event of `hooks.yaml`, `sh "${CLAUDE_PLUGIN_ROOT}/bin/saf-hook.sh" <Event>`, with its timeout; tool events take `matcher: "*"` |
| `VERSION`, `requirements.lock`, `launcher.py`, `bin/saf-hook.sh` | as on Copilot |
| `harness.py`, `src/`, `contracts/`, `adapters/claude-code/`, `adapters/common/`, `adapters/binding_contract.py` | the harness runtime |

- **The shim** finds Python ≥ 3.10 (`SAF_HARNESS_PYTHON`, `python3`, `python`) and runs
  `launcher.py <Event>`. Without one it decides in shell: it records an orchestrator's intent
  at `UserPromptSubmit`, exits 2 at a governed `PreToolUse`, and warns at `SessionStart`.
- **The launcher** (stdlib only) records the conversation's **intent** at an orchestrator's
  `UserPromptSubmit` (its `agent_type` namespace names a registered framework), resolves the
  workspace from the committed `.saf/workspace.yaml` and the framework root from the registry,
  provisions the runtime in `CLAUDE_PLUGIN_DATA` at `SessionStart` only, and applies the
  Copilot plugin mode's *Fail-closed scope* with this host's shapes: a structured `deny` at
  `PreToolUse`, a `systemMessage` warning at `SessionStart`. An orchestrator selected in a
  folder that is not a SAF workspace of its framework gets the `UserPromptSubmit` context "this
  folder is not a SAF workspace; ask me to initialise it".
- **The registry** is the per-user SAF registry of the Copilot plugin mode. The framework
  plugin's `SessionStart` writes `hosts/claude-code/frameworks/<name>/root` — keyed by host, so
  a Copilot and a Claude Code install of the same framework never overwrite each other. The
  harness plugin needs no registry entry of its own: no framework hook calls it (no
  agent-scoped hooks, host fact 4), so neither the stable launcher nor version routing applies.
  The intent records (`intents/<session_id>`) are shared with Copilot's (session ids are unique).
  Hosts do not order two plugins' `SessionStart` hooks: a framework not registered yet is
  silent at `SessionStart` and denies at a governed `PreToolUse` ("start a new session").

## Limited or unsupported boundaries, and their fallbacks

| Boundary / guarantee | Limitation in this runtime | Fallback |
| --- | --- | --- |
| Hook timeout and crashes | A timed-out or crashing hook fails open (host fact 7). | Generous timeouts on gating events; every harness call is stopped at a deadline inside the timeout (25 %, at least 5 s) and becomes a denial; every failure path of the entry, launcher and shim exits 2 at `PreToolUse`. |
| Shell writes (I9) | `Bash` is an unmediated write channel. | As on Copilot: the textual guard, and the core's commit-set check and C6 backstop. |
| H4 result channel | The answer reaches the model as a hook denial. | As on Copilot: the `RESULT` prefix and the injected hint. |
| Dispatcher correlation | The dispatcher of a started subagent is inferred, not observed. | Exact for every non-concurrent dispatch; concurrent dispatches of the same agent by several dispatchers refuse the subagent (fail closed). |
| Background dispatch | A dispatch may run in the background unless told otherwise. | `run_in_background: false` required; the framework's binding prose says so. |
| H5 result | A built-in tool's result cannot be replaced. | `decision: block` puts the rejection next to the result; the harness has already reverted the write. |
| Model fidelity | Only family aliases are accepted at dispatch. | `models.yaml` binds slugs to aliases; the RESULT names the alias. |
| Human gate in `claude -p` | A non-interactive run cannot hold a human gate. | Run a framework that needs a human gate interactively. |
| Agent-scoped hooks | Not fired for plugin agents (2.1.323). | Not used: H0 reads the observed `agent_type`. |
| Windows | Not verified. | Hooks run through `sh`; the harness request line is parsed with POSIX quoting. |
