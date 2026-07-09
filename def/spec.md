# Agentic Harness

The harness is the deterministic execution core of an agentic framework. It owns:

- resolution of step, workflow, and model,
- checking of step's conditions, authorization, and artifacts,
- resolution of agent's instructions and skills — the context the host adapter injects
  into sessions,
- logging of all that.

Agents stay limited to the irreducible work: generating content, judging within a step, and actuating host tools.

The harness does not orchestrate: the **orchestrator agent** orchestrates (converses with the
user, starts workflows on assent, dispatches subagent's steps). The harness **resolves, checks, and records** — deterministically, from persisted state and validated configuration only.

The harness is methodology-agnostic and host-agnostic: it knows nothing of SAFe, Scrum, or any
other methodology, and nothing of GitHub Copilot, VS Code, or any other host. It consumes generic
workflow definitions, artifact schemas, an ACL, and a model catalog supplied by the framework
that embeds it. Methodology-specific concepts live in the framework's skills, artifact schemas,
and workflow configs; host-specific concepts live in the adapter under `harness/adapters/<env>/`.

This document is the canonical harness specification: **the functions section is the functional
contract** (it changes only by explicit design decision, never by refactoring), and the design
and implementation parts prescribe its realization.

## Summary

- [Terminology](#terminology) — the shared nouns: framework, workspace, artifact, log,
  session, facilitator, workflow, workflow instance, step.
- [The harness functions](#the-harness-functions) — the functional contract: thirteen functions,
  numbered and ordered as they sequence in the diagram, each specified by its interface (with
  JSON I/O contract), pre/postconditions, and invariants.
  - Session (0): [`register-session`](#0-register-session)
  - Workflow context (1–2): [`resolve-workflow-instructions`](#1-resolve-workflow-instructions) ·
    [`resolve-workflow-skills`](#2-resolve-workflow-skills)
  - Resolution (3–4): [`resolve-step`](#3-resolve-step) ·
    [`resolve-step-model`](#4-resolve-step-model)
  - Step (5–10): [`check-step-preconditions`](#5-check-step-preconditions) ·
    [`resolve-step-instructions`](#6-resolve-step-instructions) ·
    [`resolve-step-skills`](#7-resolve-step-skills) ·
    [`check-step-authorization`](#8-check-step-authorization) ·
    [`check-step-artifact`](#9-check-step-artifact) ·
    [`check-step-postconditions`](#10-check-step-postconditions)
  - Global checks (11–12): [`check-workspace`](#11-check-workspace) ·
    [`check-configuration`](#12-check-configuration)
  - [General invariants](#general-invariants) — C0–C7: workspace state definition, assertion
  scope, agnosticism, schema binding, workspace validity, framework-agent scope.
- [Design](#design) — trigger planes, hook event normalization, source layout, class design,
  hook adapter layout, configuration plane, workspace Git plane, logging.
- [Development](#development) — Python conventions, SOLID, TDD, unit and functional
  testing, installation, and the validation surface.

## Terminology

- **Framework** — the agentic framework application embedding the harness (this repo's SAFe methodology). Its
  layout is declared in environment variables loaded from a `.env` file at the framework root
  (see the [configuration plane](#configuration-plane)).
- **Workspace** — the writable data plane the harness checks, constituted exclusively of
  **artifacts** and **logs** — one workspace, two authors, no overlap: agents author artifacts,
  the harness authors logs. The workspace is a Git repository, and **workspace state is its
  committed state** (`HEAD`): the working tree is the write staging area — an agent write
  lands there, is validated (function 9), and only a validated write is committed into state;
  an invalid write is discarded from staging, so committed state never holds invalid bytes.
- **Artifact** — the framework's content: every business, governance, or orchestration
  deliverable persisted under the workspace state paths. Schema-bound (C5),
  valid-by-construction once persisted (C6), and the sole basis for every check the harness
  performs (C1–C2). Artifacts — status transitions, human decisions, and authored deliverables
  included — are produced by dispatched agents (or the user) and then checked by the harness,
  which never authors artifact content: its only deliberate Git actions are at the write
  boundary — committing a validated staged write into state and discarding an invalid one
  (restore / delete) — neither of which writes new content. Artifacts attach
  to **steps**, never to workflows: each step delivers exactly one artifact — its `artifact`
  field names it by schema slug (the artifact kind) or URI (a specific instance) — and a
  workflow's deliverables are simply the union of its steps' artifacts (in this framework:
  epics, features, stories, …).
- **Log** — the harness's record: the append-only JSONL file of one session
  (`<workspace>/logs/<sessionId>.log.jsonl`), created by function 0's registration entry —
  its first line. Every completed function invocation appends exactly one entry to the log of
  the session it ran in, and the log-entry contract schema-binds every entry. A workflow instance
  owns no file: its history is its *instance view* — the ordered union of the entries carrying
  its id across the session logs (see [Logging](#logging)). Logs are persisted workspace state
  (C0) — a condition may read log evidence and assert it as state (C5) — but they are not
  artifacts: agents never write logs, and no step produces one.
- **Session** — the harness's unit of log ownership, registered by function 0 at the
  session-started boundary under its `sessionId`, with `parentSessionId` linking a session to
  the session that opened it (a dispatched subagent, a shell opened from a wrapped shell, a
  chained CI job) — a parent chain of arbitrary depth, origin-agnostic. A session is not
  host-exclusive: its `actor` names the origin — `agent` (a framework
  agent acting inside a host session, `sessionId` host-observed via the adapter, C7-gated),
  `human` (a person at a terminal, `sessionId` minted by a bash wrapper at shell start), or
  `system` (a CI job, `sessionId` sourced from the CI platform's ambient run id). An
  orchestrator session spans many workflow instances over its lifetime; a step session IS its
  step (1 step = 1 agent = 1 session = 1 artifact). Session ids are always observed or minted
  by the surrounding mechanism (adapter, bash wrapper, CI platform) — never self-reported by an
  agent.
- **Facilitator** — the orchestrator agent a workflow declares to drive its instances. Its
  session receives its workflow instructions — selection and return-handling (function 1) —
  and its procedure skills (function 2); it converses, obtains assent, calls resolution, and
  dispatches steps. A facilitator is an **agent**; an agent is an **actor** only when acting at a step —
  the facilitator normally never is: steps are acted by dispatched subagents.
- **Workflow** — a configuration entity (`conf/workflows/*.workflow.conf.yaml`): an atomic,
  artifact-delivering unit of the methodology, made of steps. Each workflow declares its
  **facilitator** — the orchestrator agent that drives its instances; the facilitator's
  workflow instructions are injected at its session open (function 1).
- **Workflow instance** — one run of one workflow, the way an object is an instance of a
  class: the workflow configuration is the definition; the instance is one dated execution of
  it, journaled across the session logs that serve it — its *instance view* — with its own
  step cursor. An instance carries no input of its
  own — it is identified by its minted id and recognized by its content: the artifacts its
  steps have journaled delivered. A workflow delivers MANY artifacts — one per step — so no
  single artifact is attached to the instance; artifacts attach to steps.
- **Step** — one agent turn. **1 step = 1 agent = 1 session = 1 artifact**: the session
  lifecycle IS the step lifecycle, and each step delivers exactly one artifact — its
  `artifact` declares it.

---

## The harness functions

This is the functional contract: everything the harness does is exactly one of these thirteen
functions, every log entry names the function that produced it, and every harness command is
an entry point into one of them. A **harness command** is the Python executable function entry
point; its input is the function's JSON `in` object:

```json
{
  "in": {
    "sessionId": "01j9xq0f2m",
    "parentSessionId": null,
    "workflowSlug": "verification"
  },
  "out": {
    "context": {
      "function": "resolve-step",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "step-resolution" },
    "step": { "...": "..." }
  }
}
```

Every session-bound function's `in` carries the session attribution fields directly:
`sessionId` and nullable `parentSessionId`. Function-specific fields live
beside them in the same object (for example `workflowSlug` for function 3 or `artifactPath`
for functions 8–9). The invocation surface supplies the session fields (hook adapter, a
trusted adapter-mediated surface with verifiable session attribution, a bash wrapper for a
human terminal session, or the CI platform's ambient run id for a system session); a raw
agent-authored value is not trusted. Function 0 is the bootstrap exception: it may also carry
the framework agent name when the surrounding mechanism is registering a framework-agent
session, because no registration exists yet and the session record is the place to attach that
identity. Every function is session-bound
— functions 11–12 run under a human or system session like any other, and journal the same way.

Every `out` object is a concrete **Report** object: the exact object returned by the service,
rendered by the command, forwarded by the hook adapter, and persisted byte-identically as the
log entry's `report`. Reports share the `context` object (`function`, `sessionId`, nullable
`parentSessionId`, nullable `workflowInstanceId` — the exact invocation context the log entry
persists) and the `outcome` object (`status`, plus the `error` detail — required on error
statuses, absent otherwise), and add one function-owned specific property where needed. Any
session metadata beyond correlation, such as framework-agent identity when applicable, is
recorded by function 0's registration and recovered through `sessionId` when needed. The
normative schemas live at `harness/contracts/functions/<function>.input.schema.json` and
`harness/contracts/functions/<function>.output.schema.json`; each function section links to
them. Shared contracts live beside them:
[harness/contracts/actions.schema.json](harness/contracts/actions.schema.json),
[harness/contracts/context.schema.json](harness/contracts/context.schema.json),
[harness/contracts/report.schema.json](harness/contracts/report.schema.json), and
[harness/contracts/log-entry.schema.json](harness/contracts/log-entry.schema.json).

| # | Function | What it answers | When |
|---|---|---|---|
| 0 | [`register-session`](#0-register-session) | Which framework-agent session just opened, under which parent session — the registration every later entry's session ids trace back to? | session-started / step-started — every framework-agent session, always first |
| 1 | [`resolve-workflow-instructions`](#1-resolve-workflow-instructions) | Which workflow-context guidance does the orchestrator's session load? | session-started |
| 2 | [`resolve-workflow-skills`](#2-resolve-workflow-skills) | Which skills does the orchestrator's session load? | session-started |
| 3 | [`resolve-step`](#3-resolve-step) | What is the next eligible step of this workflow instance, with its full step resolution — or is there no next step to resolve? | adapter-mediated agent invocation — on assent, and after each step's outcome journals |
| 4 | [`resolve-step-model`](#4-resolve-step-model) | Which model profile serves this step's dispatch? | adapter-mediated agent invocation — between resolution and dispatch |
| 5 | [`check-step-preconditions`](#5-check-step-preconditions) | May this step start? | step-starting |
| 6 | [`resolve-step-instructions`](#6-resolve-step-instructions) | Which behavioral guidance does this step's session load? | step-started |
| 7 | [`resolve-step-skills`](#7-resolve-step-skills) | Which skills does this step's session load? | step-started |
| 8 | [`check-step-authorization`](#8-check-step-authorization) | Is this write a granted privilege of the acting agent? | write-starting |
| 9 | [`check-step-artifact`](#9-check-step-artifact) | Is the step's written artifact schema-valid? | write-ended |
| 10 | [`check-step-postconditions`](#10-check-step-postconditions) | Did this step deliver? | step-ended |
| 11 | [`check-workspace`](#11-check-workspace) | Is the workspace, as a whole, in a valid state right now? | CI (system session) / human terminal session invocation |
| 12 | [`check-configuration`](#12-check-configuration) | Is the embedding application's configuration a valid harness input? | CI (system session) / human terminal session invocation |

The `When` values are the harness's canonical **boundaries** (realized by the `Boundary` enum
in the hook plane) or the non-hook invocation surface: host event names never appear in the
functional contract (C4) — the mapping from host events to boundaries is the adapter's job,
per [Boundary Normalization](#boundary-normalization).

Function 0 is **session-scoped**: triggered at every session start — orchestrator and step
sessions alike — strictly before any other function of that session; it registers the
session's identity and creates its log. Functions 1–2 **resolve the
facilitator's workflow context** at its session start — selection and return-handling
instructions, procedure skills — which the adapter injects. Functions 3–4 are
**resolution-scoped**: pure functions over
the instance view of the logs plus validated configuration; they read no artifacts. Functions
5–10 are **step-scoped**, in step lifecycle order — 1 step = 1 agent = 1 session = 1 artifact:
5 gates the dispatch, 6–7 resolve the step's declared context at its session start (the
adapter injects it), 8–9 guard
every write as it lands, 10 evaluates delivery against the state the step left. Functions
11–12 are **globally-scoped** checks: sweeps over the whole workspace or the
whole configuration, run under a session like any other — a human's terminal session (bash
wrapper-minted) or CI's system session (the platform's ambient run id) — and journaled the same
way: no step, but a session, and one log entry per invocation.

The sequence diagram [`harness-functions.puml`](harness-functions.puml) shows all
thirteen functions in play across one workflow instance — framework user, orchestrator agent,
step subagents, host, and harness.

![Harness functions sequence diagram](harness-functions.png)

### 0. register-session

The session seed: the first function of every session, triggered strictly before any other
function at that session's level. It registers the session's identity — the
`sessionId` and, for a dispatched (subagent) session, its `parentSessionId` — and creates the
session's log file with this registration as its first entry: the entry every later entry's
session ids trace back to. The harness stays agnostic about how the session id was sourced;
the surrounding mechanism is responsible for observing or minting it before invocation. When
the registered session corresponds to a framework agent, the caller may also attach that
framework-agent identity as `agent`.

No agent ever reports its own session identity: the id is always observed or minted by the
surrounding mechanism (adapter, bash wrapper, CI platform), never accepted as a raw
agent-authored argument.

**Interface**

- **In** — the session ids: `sessionId` (this session) and `parentSessionId` (the session that
  opened this one — nullable, any origin: a dispatching agent session, a wrapped parent shell,
  a chaining CI job). `agent` is optional session metadata and is present when the registered
  session corresponds to a framework agent.
- **Out** — `SessionRegistrationReport`: the `session` object.
- **Caller usage** — never called by an agent's own action; called by the adapter, the bash
  wrapper, or the CI step wrapper. Later adapter-mediated agent invocations of the same
  session are attributed to it per the
  session-attribution rule (see [Invocation surfaces](#invocation-surfaces-one-command-system)
  and [Logging](#logging)).

Example:

```json
{
  "in": { "agent": "qa-engineer", "sessionId": "01j9xqr7t3", "parentSessionId": "01j9xq0f2m" },
  "out": {
    "context": {
      "function": "register-session",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": null
    },
    "outcome": { "status": "registered" },
    "session": { "agent": "qa-engineer", "sessionId": "01j9xqr7t3", "parentSessionId": "01j9xq0f2m" }
  }
}
```

Contract schemas — [harness/contracts/functions/register-session.input.schema.json](harness/contracts/functions/register-session.input.schema.json) and [harness/contracts/functions/register-session.output.schema.json](harness/contracts/functions/register-session.output.schema.json).

**Preconditions**

- The surrounding mechanism has observed or minted a `sessionId` and, when applicable, a
  `parentSessionId`, and normalized any host-sourced ids to a safe slug — the id becomes a log
  filename (see [Logging](#logging)).
- When `agent` is present, it resolves to a framework agent identity (C7 — foreign,
  non-framework-agent sessions pass through untouched and unregistered).
- Trigger — the session-started boundary (hook plane) for adapter-opened sessions; the first
  harness command of a wrapped shell; the first harness command of a CI job — before
  functions 1–2 (orchestrator session) or 6–7 (step session) run at the
  same boundary, where applicable.

**Postconditions**

- The session log `<workspace>/logs/<sessionId>.log.jsonl` exists; the registration entry
  is its first line.
- The registration is the session-scope seed: every subsequent entry of this session carries
  the registered `sessionId` (and `parentSessionId` where present) in its envelope.

**Invariants**

1. Registration precedes everything: no function logs at a session's level before this one —
   physically enforced, since function 0 creates the very file the others append to.
2. Session ids are observed or minted by the surrounding mechanism — host event payload and
  adapter normalization, the bash wrapper, or the CI platform's ambient run id — never minted
  by the harness, never self-reported by an agent.
3. The parent chain is unbounded: each registration records one parent, so any nesting depth
   of subagent dispatches reconstructs by walking registrations parent-by-parent — the
   envelope never caps the hierarchy at two levels. Any origin may record a parent (a
   dispatched subagent, a shell opened from a wrapped shell, a chained CI job).
4. Registration is idempotent per session: re-delivery of the same session-start signal (host
   resume, duplicate hook, a wrapped shell re-invoking the wrapper) appends no second
   registration for an already-registered `sessionId`.

### 1. resolve-workflow-instructions

Which workflow-context guidance does the orchestrator's session load? Deterministic
resolution, at the session-started boundary, of the facilitator's workflow instructions —
the adapter injects what this function resolves: one named instruction per orchestrator
duty, so every selection and every harness return meets an instructed reaction, never an
improvised one:

- `workflow-selection-handling` — how to select a workflow. This instruction **references the
  facilitated workflows themselves** (the catalog knowledge is framework-authored instruction
  content, with each workflow's advisory position and guidance — not function output): match
  the user's intent to one facilitated workflow, propose it (or the continuation of an
  unfinished one), ask when ambiguous, await assent.
- `step-resolution-handling` — how to use function 3's step resolution: resolve the step's
  model profile (function 4) and relay the resolution verbatim into the host dispatch.
- `no-next-step-handling` — how to close: return end-to-end workflow results and the
  workflow-level options — including the advisory successor workflows it names.
- `reports-handling` — how to react to any negative return (denied dispatch, failed outcome):
  produce the missing state, re-resolve — never override, never surface step details to the
  user.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The facilitator is resolved from
  the registered session; the instruction set is keyed by that facilitator.
- **Out** — `WorkflowInstructionsReport`: the instruction refs.
- **Caller usage** — the adapter renders the refs into the host's session context; the
  orchestrator starts its conversation already knowing its workflows and how to handle every
  harness return.

Example:

```json
{
  "in": { "sessionId": "01j9xq0f2m", "parentSessionId": null },
  "out": {
    "context": {
      "function": "resolve-workflow-instructions",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": null
    },
    "outcome": { "status": "resolved" },
    "instructions": [
      "instructions/workflow-selection-handling.instructions.md",
      "instructions/step-resolution-handling.instructions.md",
      "instructions/no-next-step-handling.instructions.md",
      "instructions/reports-handling.instructions.md"
    ]
  }
}
```

Contract schemas — [harness/contracts/functions/resolve-workflow-instructions.input.schema.json](harness/contracts/functions/resolve-workflow-instructions.input.schema.json) and [harness/contracts/functions/resolve-workflow-instructions.output.schema.json](harness/contracts/functions/resolve-workflow-instructions.output.schema.json).

**Preconditions**

- A session opened with no unresolved `resolve-step` entry correlating to it (typically: its
  registration carries no `parentSessionId`) — an orchestrator session
  (a step session loads functions 6–7 instead).
- The session's agent is a framework facilitator (C7 — foreign sessions pass
  through untouched).
- Trigger — the session-started boundary (hook plane).

**Postconditions**

- The session context contains the facilitator's instruction refs — nothing more, nothing
  chosen by the agent.
- The invocation appends its own entry to the session's log (what was resolved, for which
  facilitator).

**Invariants**

1. The instruction set derives from configuration only: one framework-authored instruction
   per orchestrator duty, keyed by facilitator; the selection instruction names the workflows
   whose `facilitator` is the session's agent, with their advisory positions and guidance —
   static, no log reads.
2. The instructions are framework-authored refs, one per duty and per harness return kind the
   orchestrator handles; every function-3 return the orchestrator receives — and the workflow
   selection itself — is covered by an injected instruction.
3. Resolution is deterministic and facilitator-scoped: `sessionId` resolves to the
  facilitator's registered session, and the configuration decides the refs — never the agent.

### 2. resolve-workflow-skills

Which skills does the orchestrator's session load? The same session-kind correlation and
determinism as function 1, for skills: the facilitator's procedure skills — its selection skill
and one procedure skill per workflow it facilitates.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The facilitator is resolved from
  the registered session.
- **Out** — `WorkflowSkillsReport`: the skill ids to load.
- **Caller usage** — the adapter loads the skills into the session; the orchestrator's toolbox
  is its facilitator role's toolbox, by construction.

Example:

```json
{
  "in": { "sessionId": "01j9xq0f2m", "parentSessionId": null },
  "out": {
    "context": {
      "function": "resolve-workflow-skills",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": null
    },
    "outcome": { "status": "resolved" },
    "skills": ["workflow-selection", "pair-programming-procedure", "verification-procedure"]
  }
}
```

Contract schemas — [harness/contracts/functions/resolve-workflow-skills.input.schema.json](harness/contracts/functions/resolve-workflow-skills.input.schema.json) and [harness/contracts/functions/resolve-workflow-skills.output.schema.json](harness/contracts/functions/resolve-workflow-skills.output.schema.json).

**Preconditions**

- A session opened with no unresolved `resolve-step` entry correlating to it — an
  orchestrator session.
- Trigger — the session-started boundary (hook plane).

**Postconditions**

- The session loads exactly the facilitator's declared skill set.
- The invocation appends its own entry to the session's log, alongside function 1's.

**Invariants**

1. The skill set derives from configuration only: the selection skill plus each facilitated
   workflow's procedure skill.
2. Resolution is deterministic: the configuration decides, never the agent.

### 3. resolve-step

The resolution core and the orchestrator's main loop. The orchestrator agent drives a workflow
instance by calling this function repeatedly — once after starting the instance on user assent,
then once after each step's outcome journals — and relaying each result verbatim into the host.
The harness alone governs workflow/step sequencing: no agent selects steps, and there is no
`previous` direction — a failed or reopened step is simply not journaled executed, so plain
forward resolution returns it again. Retry is re-resolution, not selection.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId` + the workflow slug
  (`workflowSlug`) — the only agent-supplied function-specific parameter in the whole
  contract. The instance is deduced, never named:
  the harness continues the **latest
  open instance** of the workflow (an instance not all of whose steps are journaled passing),
  or — none open — **opens a new one**, minting the workflow-instance id; the opening is this
  invocation's own log entry, no dedicated file exists. Instance ids are minted and correlated
  by the harness alone and are never accepted as input: the report's `context` carries the
  `workflowInstanceId` read-only (the contract is the source of truth) — no agent ever passes
  or mints one. (Starting over
  a half-finished instance is an explicit non-goal for now: an open instance is always
  resumed.)
- **Out** — `StepResolutionReport`: `outcome` ± `step`, nothing more:
  - a **step resolution** (`step-resolution`): the configured step object itself, verbatim
    from the workflow configuration — `slug`, `actor`, `skills`, `instructions`, `artifact`
    (schema slug / URI), flat `conditions`, and `capabilities`;
  - `no-next-step`: every authored step currently has a passing journaled execution — a
    reversible observation, no step attached. Advisory succession is `no-next-step-handling`
    instruction content (function 1), not function output.
- **Caller usage** — the orchestrator handles each return per its injected instructions
  (function 1). On a step resolution — per `step-resolution-handling` — it resolves the
  step's model profile (function 4), relays the resolution verbatim into the host dispatch
  (actor, model, skills, instructions exactly as given), awaits the step's outcome journals,
  and calls again — after a failed outcome (per `reports-handling`) exactly the same way: the
  cursor returns the failed step.
  On `no-next-step` — per `no-next-step-handling` — it returns the instance's END-TO-END
  workflow results (the delivered artifacts) to the user and presents the workflow-level
  options — reiterate the workflow or take an advisory successor: the user decides.

Example:

```json
{
  "in": {
    "sessionId": "01j9xq0f2m",
    "parentSessionId": null,
    "workflowSlug": "verification"
  },
  "out": {
    "context": {
      "function": "resolve-step",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "step-resolution" },
    "step": {
      "slug": "review", "actor": "qa-engineer", "skills": ["code-review"],
      "instructions": ["instructions/review.instructions.md"],
      "artifact": "review-report",
      "conditions": [
        { "kind": "precondition", "type": "after", "id": "after_build", "step_id": "build" },
        {
          "kind": "postcondition", "type": "state", "id": "report_exists",
          "set_selector": {
            "set_type": "artifact",
            "artifact_types": [{ "alias": "report", "schema_id": "review-report" }],
            "set_query": "report.filter(a, a.slug == artifact)"
          },
          "set_predicate": "selected.size() == 1"
        }
      ],
      "capabilities": {
        "deep-reasoning": 9, "coding": 2, "tool-use": 6,
        "long-context": 6, "multimodal": 0, "writing-quality": 7,
        "instruction-following": 8, "fast-iteration": 4, "schema-adherence": 8
      }
    }
  }
}
```

Contract schemas — [harness/contracts/functions/resolve-step.input.schema.json](harness/contracts/functions/resolve-step.input.schema.json) and [harness/contracts/functions/resolve-step.output.schema.json](harness/contracts/functions/resolve-step.output.schema.json).

**Preconditions**

- The configuration is validated (fail-fast at load): the workflow exists, its step DAG is
  acyclic, every step routes.
- The user assented to the workflow (per the selection instruction). Instance continuation or
  opening is deduced (In, above) — no id is ever given.
- Trigger — adapter-mediated agent invocation, each time the orchestrator asks "what's
  next?" on a driven workflow: after starting it on user assent, and after each step's
  outcome journals — including after a failed outcome, where the same call re-resolves the
  failed step. The invocation is session-attributed (see [Logging](#logging)).

**Postconditions**

- Exactly one log entry records the invocation: the resolved action (`step-resolution` with
  the step / `no-next-step`), carrying the instance id in its envelope.
- When no instance was open, the new instance exists: its id is minted and the opening is
  this very entry — surfaced read-only in the report's `context`, never accepted as input.
- No artifact is written, no step is started — nothing beyond the log entry changes.

**Invariants**

1. The step cursor derives from the instance view of the logs only: a step counts as **executed** when
   its LATEST journaled `check-step-postconditions` outcome for this instance reports its
   postconditions holding (latest wins — a replayed or reopened step drops back out).
2. Eligibility follows the authored step order: the first remaining step whose `after`
   predecessors are all journaled executed. In a validated configuration (acyclic step DAG,
   resolvable `after` references — enforced at load) an instance with a remaining step always
   has exactly one next eligible step: there is no runtime "blocked" state.
3. The step resolution is fully resolved by the harness — the agent relays it verbatim into the
   host dispatch (e.g. `runSubagent`), never chooses for itself. The model profile is NOT part
   of the resolution: it is function 4's, resolved by its own call between resolution and
   dispatch — the two functions are fully independent.
4. Resolution never writes artifacts and never starts the step: it returns the resolution; the
   agent dispatches it.
5. The harness never declares a workflow complete, done, or finished — those are judgments it
   cannot make. `no-next-step` only observes that every authored step currently has a passing
   journaled execution: a reversible observation, not a verdict. Any completion judgment
   belongs to the user at the return boundary.
6. Sequencing is governed by the harness alone: no agent — orchestrator or subagent — selects
   steps. There is no `step` parameter and no `previous` direction: a failed or reopened step
   is not journaled executed, so forward resolution (invariants 1–2) returns it again — retry
   is re-resolution, not selection.
7. The workflow is the user-facing unit of execution: the orchestrator returns end-to-end
   workflow results to the user, never intermediate step results. The USER reiterates
   workflows; step retries — on negative harness reports — happen inside the workflow through
   re-resolution (invariant 6), invisible to the user. The human checkpoint is the workflow
   return boundary: every instance ends by delivering artifacts back to the user — there are
   no in-band gates and no step-level user surface.
8. **Instance correlation is deduced, latest-open-wins**: `resolve-step(workflowSlug)`
   continues the latest open instance of that workflow, else opens one. Older open instances
   simply stop being the latest — abandonment is not a state, and no register closes
   anything. Agents never pass or mint instance ids — the id surfaces read-only in the
   report's `context`.
9. **One open workflow instance and one in-flight step per facilitator session**: a facilitator
   session drives at most one open workflow instance at a time, and between a step's resolution
   and its journaled outcome it resolves no other step. Running concurrent instances — whether
   of the same workflow or different workflows — is a deliberate non-goal for now. The log still
   carries `workflowInstanceId`, so a later concurrent model can be added without changing the
   identity of past entries.

### 4. resolve-step-model

The model resolution function: which model profile serves this step's dispatch. A
**standalone, adapter-mediated agent invocation between resolution and dispatch** — never embedded in
function 3: the two functions are fully independent, and whoever needs both composes them
outside the harness (the orchestrator per its instructions, or the adapter on one hook).
Resolved from two static configuration layers, deterministically, with no artifact reads and
no per-instance estimation; the harness deduces WHICH step from its own logs — the agent
asks, never describes.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The harness deduces the
  session's **in-flight step** from the session id and logs — the latest step resolution
  journaled in the invoking session, not yet
  concluded by a function-10 outcome (unambiguous by function 3, invariant 9) — and reads
  its weighted `capabilities` from the workflow configuration: an agent never supplies
  weights (self-reporting).
- **Out** — `ModelProfileReport`: the canonical model **profile**
  `{id, score, cost_rank, reason}` — a catalog profile, not a host model id: the adapter's
  `models.yaml` maps it to the host-specific id at dispatch. It always returns a profile
  (invariant 4).
- **Caller usage** — the orchestrator calls it between function 3's resolution and the
  dispatch (per its `step-resolution-handling` instruction) and relays the profile into the
  dispatch.

Example:

```json
{
  "in": {
    "sessionId": "01j9xq0f2m",
    "parentSessionId": null
  },
  "out": {
    "context": {
      "function": "resolve-step-model",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "resolved" },
    "profile": { "id": "claude-sonnet-4", "score": 144, "cost_rank": 2, "reason": "highest weighted capability score" }
  }
}
```

Contract schemas — [harness/contracts/functions/resolve-step-model.input.schema.json](harness/contracts/functions/resolve-step-model.input.schema.json) and [harness/contracts/functions/resolve-step-model.output.schema.json](harness/contracts/functions/resolve-step-model.output.schema.json).

**Preconditions**

- The model catalog and the step's capability map are loaded and validated (fail-fast at load).
- An in-flight step exists in the invoking session: function 3 resolved it and its outcome has
  not journaled yet.
- Trigger — adapter-mediated agent invocation, between resolution and dispatch. The
  invocation is session-attributed (see [Logging](#logging)).

**Postconditions**

- One log entry records the resolution (1 invocation = 1 entry), carrying the deduced step in
  its envelope. A profile is always returned: unroutable configurations died at load
  (invariant 4) — no `Auto` and no unknown id ever reaches a dispatch.

**Invariants**

1. **Step → `capabilities` (weighted, static)**: each workflow step declares a weighted map of
   capability tag → weight (0–10), all nine tags explicit, authored once per step in its
   workflow configuration. The step is the dispatch — its kind of work fixes both WHICH tags
   matter and HOW MUCH (the methodology's splitting discipline homogenizes per-unit complexity,
   so two instances of the same step carry the same weights). Every step must carry at least one
   positive weight — the config plane rejects an all-zero map at load.
2. **Model catalog → `capability_scores` + `cost_rank` (static)**: `conf/model-profiles.conf.yaml`
   scores every model 0–10 per tag and ranks its relative cost. The catalog owns the canonical
   tag vocabulary.
3. **The score** is a pure weighted capability sum:

   $\text{Score}(m) = \sum_{\text{tag}} \text{capability\_score}_m[\text{tag}] \times \text{step.capabilities}[\text{tag}]$

   Highest score wins; ties break toward lower `cost_rank`; if both are equal, the
   lexicographically lowest model `id` wins. Cost sensitivity emerges
   structurally: low, sparse weights compress candidate scores into a narrow band where the
   cheap-model tie-break dominates; high weights on discriminating tags let capability dominate
   cost.

   *Worked example* — a step weighting `deep-reasoning: 9`, two models scoring 9 vs 7 on it:
   $A = 81$, $B = 63$ — A wins on capability. At weight 3 the spread narrows ($27$ vs $21$) and,
   across a larger candidate set, the `cost_rank` tie-break routes cheaper.

4. An empty catalog or an all-zero effective weight is **unroutable** — rejected at
   configuration load (invariant 1 plus catalog validation), so it cannot occur at runtime and
   is never papered over with a silent `Auto`.
5. **Deterministic and idempotent per step**: the profile is a pure function of static
   configuration and the deduced step, so any number of calls for the same step yields the
   identical profile — there is no "once" to protect and no re-resolution risk. The relayed
   profile is what reaches the dispatch, per the orchestrator's `step-resolution-handling`
   instruction: the harness resolves; the instructed agent relays.

### 5. check-step-preconditions

May this step start? The gate the orchestrator consults between resolution and dispatch.
Evaluated strictly against persisted workspace state
(C1) and the instance view of the logs — never against anything an agent merely remembers.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The step key is resolved by the
  harness from the session ids and logs — hook-plane correlation at the boundaries,
  in-flight-step deduction for the agent probe — never supplied by an agent.
- **Out** — `ConditionCheckReport`: the aggregate `outcome` (`pass` / `fail`) +
  `conditionChecks`: one check per
  declared precondition — the condition object itself, verbatim from the workflow
  configuration, its `outcome`, and its `failureMessage` when failing.
- **Caller usage** — the orchestrator dispatches only on a passing outcome; on `fail` — per its
  `reports-handling` instruction (function 1) — it reports exactly which persisted state is
  missing so the user or a predecessor step produces it — it never overrides a failing
  precondition.

Example:

```json
{
  "in": {
    "sessionId": "01j9xq0f2m",
    "parentSessionId": null
  },
  "out": {
    "context": {
      "function": "check-step-preconditions",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "fail" },
    "conditionChecks": [
      { "condition": { "kind": "precondition", "type": "after", "id": "after_build", "step_id": "build" }, "outcome": "pass" },
      {
        "condition": {
          "kind": "precondition", "type": "state", "id": "report_exists",
          "set_selector": {
            "set_type": "artifact",
            "artifact_types": [{ "alias": "report", "schema_id": "review-report" }],
            "set_query": "report.filter(a, a.slug == artifact)"
          },
          "set_predicate": "selected.size() == 1"
        },
        "outcome": "fail", "failureMessage": "no artifact matches 'review-report'"
      }
    ]
  }
}
```

Contract schemas — [harness/contracts/functions/check-step-preconditions.input.schema.json](harness/contracts/functions/check-step-preconditions.input.schema.json) and [harness/contracts/functions/check-step-preconditions.output.schema.json](harness/contracts/functions/check-step-preconditions.output.schema.json).

**Preconditions**

- A resolved step is in hand, carrying its declared precondition list from the workflow
  configuration.
- Persisted workspace state and the logs are readable.
- Trigger — the step-starting boundary (THE enforcement point: a failing precondition denies
  the dispatch); optionally the orchestrator, probing before dispatch for early feedback
  (the in-flight step is deduced from its session ids/logs) — the probe is advisory,
  the boundary enforces. Both run in the dispatching (facilitator) session: the step
  session does not exist yet at this boundary.

**Postconditions**

- One log entry records the invocation: the per-condition checks plus the aggregate outcome —
  appended to the dispatching (facilitator) session's log, the session the step-starting
  boundary belongs to.
- No artifact is touched — the invocation's own log entry is the only write: checking never
  mutates artifacts.

**Invariants**

1. `after` conditions: every referenced predecessor step must be journaled executed in the
  correlated workflow instance. The instance view always exists once a step is in flight, so
  there is no unevaluable case: a predecessor not journaled executed fails the condition —
  never a silent pass.
2. `state` conditions: `set_selector` binds artifact aliases and a CEL `set_query` to produce
   `selected`; `set_predicate` is then evaluated over `selected`. The step's declared `artifact`
   ref is in scope as a runtime constant. Every `<alias>.<property>` reference is statically
   validated against the aliased artifact schema — an undeclared property is a hard error, not
   a false pass. An empty selected set is a normal value: the predicate decides whether it
   passes or fails.
3. Condition ids are the audit handle: unique within a step, and every check logs the full
   condition object with its outcome under that id.

### 6. resolve-step-instructions

Which behavioral guidance does this step's session load? Deterministic resolution of the
step's authored context at the step-started boundary — the adapter injects what this function
resolves: 1 step = 1 agent = 1 session = 1 artifact — the
step's authored constraints
reach the agent with no discretion of its own.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The step whose session opened is
  resolved from those ids by the hook plane (the unresolved-`step-resolution`-entry correlation
  is normalization's job, not the resolver's).
- **Out** — `StepInstructionsReport`: the step's declared instruction refs.
- **Caller usage** — the adapter renders the refs into the host's session context; the agent
  starts its turn already carrying its constraints.

Example:

```json
{
  "in": {
    "sessionId": "01j9xqr7t3",
    "parentSessionId": "01j9xq0f2m"
  },
  "out": {
    "context": {
      "function": "resolve-step-instructions",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "resolved" },
    "instructions": ["instructions/review.instructions.md"]
  }
}
```

Contract schemas — [harness/contracts/functions/resolve-step-instructions.input.schema.json](harness/contracts/functions/resolve-step-instructions.input.schema.json) and [harness/contracts/functions/resolve-step-instructions.output.schema.json](harness/contracts/functions/resolve-step-instructions.output.schema.json).

**Preconditions**

- A session opened and the logs are readable (the correlation source).
- An unresolved `resolve-step` entry correlating to this session exists in the logs — matched
  exactly via the registration's `parentSessionId` to the parent session's latest
  `step-resolution` entry whose actor is the session's agent and whose step has no later
  function-10 outcome. Hosts without parent-session payloads may fall back to the most recent
  unresolved `step-resolution` entry whose actor is the session's agent. A session with no such
  correlation is the orchestrator's and loads functions 1–2 instead.
- The session's agent resolves to a framework agent identity (C7 — foreign sessions pass
  through untouched).
- Trigger — the step-started boundary (hook plane).

**Postconditions**

- The session context contains exactly its step's declared refs — nothing more, nothing chosen
  by the agent.
- The invocation appends its own entry to the step session's log (what was resolved, for
  which step).

**Invariants**

1. Instruction refs are declared per step in the workflow configuration
   (`instructions:` — contract/repo-relative refs).
2. At session open, the hook plane correlates the new session to its step — exact match first
   (the parent session's latest unresolved `step-resolution` entry whose actor matches the
   session's agent), then the most recent unresolved `step-resolution` entry for that actor as
   fallback for hosts without parent-session payloads — and calls this function with THAT step
   resolved from the session ids; the resolution itself is a pure configuration lookup. No
   separate dispatch-open marker is required.
3. Resolution is deterministic and step-scoped: the workflow configuration decides, never the
   agent.

### 7. resolve-step-skills

Which skills does this step's session load? The same correlation and determinism as function
6, for skills — a session's capabilities are step-scoped by construction.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The step is resolved from those
  ids by the hook plane, as in function 6.
- **Out** — `StepSkillsReport`: the skill ids to load.
- **Caller usage** — the adapter loads the skills into the session; the agent's toolbox is its
  step's toolbox, by construction.

Example:

```json
{
  "in": {
    "sessionId": "01j9xqr7t3",
    "parentSessionId": "01j9xq0f2m"
  },
  "out": {
    "context": {
      "function": "resolve-step-skills",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "resolved" },
    "skills": ["code-review"]
  }
}
```

Contract schemas — [harness/contracts/functions/resolve-step-skills.input.schema.json](harness/contracts/functions/resolve-step-skills.input.schema.json) and [harness/contracts/functions/resolve-step-skills.output.schema.json](harness/contracts/functions/resolve-step-skills.output.schema.json).

**Preconditions**

- A session opened with an unresolved `resolve-step` entry correlating to it (function 6's
  correlation) — a step session.
- Trigger — the step-started boundary (hook plane).

**Postconditions**

- The session loads exactly its step's declared skills.
- The invocation appends its own entry to the step session's log, alongside function 6's.

**Invariants**

1. Skill ids are declared per step (`skills:` in the workflow configuration) — per step, not per
   workflow: a session loads exactly its step's skills.
2. Correlation identical to function 6 (unresolved-`step-resolution`-entry lookup).
3. Resolution is deterministic: the step declaration decides.

### 8. check-step-authorization

Is this write a granted privilege of the acting agent? Plain whole-resource RBAC over
structured privileges from `conf/access-control-list.conf.yaml`, guarding every agent write
live at the boundary. The same boundary also refuses writes whose staging baseline is not
clean against `HEAD` — the commit gate's precondition (C6).

**ACL design principles**

- **Roles are framework-defined.** The framework owns the role catalog: each role is a named
  set of privileges, where each privilege is exactly one artifact schema slug plus one action
  verb (`CREATE`, `READ`, `UPDATE`, `DELETE`). Roles are not declared by agents.
- **Actors are role-to-agent mappings.** An actor assigns one or more framework-defined roles
  to a single framework agent. Agents are the `.agent.md` files in the framework's
  `agents/` directory, identified by the `name` value in their YAML frontmatter.
- **Authorization is whole-resource.** The resource under test is the artifact kind (schema
  slug); path-level or property-level granularity is intentionally not modeled.
- **No implicit grants.** A write is denied unless at least one role held by the acting agent
  explicitly grants the requested action on the artifact kind.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId` + the **artifact path**
  (`artifactPath`) + the write `action` (`create`, `read`, `update`, `delete`), derived by the
  adapter from the host write tool. The actor is derived from the registered session, never
  supplied by the agent; the resource — the artifact's schema slug — is derived from the path
  (invariant 2).
- **Out** — `AuthorizationReport`: allow, or deny with an `authorization.failureMessage`
  naming the missing privilege.
- **Caller usage** — the hook adapter enforces the live verdict (a denied tool call never
  executes); the orchestrator then routes the change through a privileged author and re-runs.

Example:

```json
{
  "in": {
    "sessionId": "01j9xqr7t3",
    "parentSessionId": "01j9xq0f2m",
    "artifactPath": "portfolio/epics/epic-payments.md",
    "action": "update"
  },
  "out": {
    "context": {
      "function": "check-step-authorization",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "denied" },
    "authorization": {
      "actor": "qa-engineer",
      "artifactPath": "portfolio/epics/epic-payments.md",
      "action": "update",
      "resource": "epic",
      "failureMessage": "missing privilege: UPDATE epic"
    }
  }
}
```

Contract schemas — [harness/contracts/functions/check-step-authorization.input.schema.json](harness/contracts/functions/check-step-authorization.input.schema.json) and [harness/contracts/functions/check-step-authorization.output.schema.json](harness/contracts/functions/check-step-authorization.output.schema.json).

**Preconditions**

- The ACL, workspace layout, and the framework's artifact schemas are loaded (path → resource
  resolution needs them), and the adapter has mapped the host write tool to a write `action`.
- Trigger — the write-starting boundary, once per pending write.

**Postconditions**

- One log entry per authorization decision (allow / deny with the missing privilege).
- On a deny, the write never lands — the workspace never sees unauthorized bytes.

**Invariants**

1. The actor is the AGENT (normalized agent identity) derived from the registered host
  session, never the skill and never a function input.
2. The resource is the artifact's schema identity, resolved from the write path — via the
   workspace layout's singleton map for well-known single-instance files, else via the
   artifact schemas' own path patterns (disambiguated by the artifact's `type` when several
   match).
3. Authorization is whole-resource: any `#property` suffix on an artifact path is ignored.
4. A write nobody granted is denied at the boundary — denial is the enforcement.
5. A write whose staging baseline is not clean against `HEAD` is denied at the same boundary:
   dirty tracked targets, pre-existing untracked targets, and paths outside the artifact layout
   do not execute — so the staged write is always the only staged content at its path (C6).

### 9. check-step-artifact

Is the step's written artifact schema-valid? The write-boundary enforcement of C6 — the
**commit gate**: a write lands in the working tree (the staging area, not yet workspace
state), this function validates the staged bytes, and only a validated write is committed
into workspace state (`HEAD`). (The workspace-wide validation on demand is function 11, this
function's instance-less counterpart.)

**Interface**

- **In** — `sessionId` + nullable `parentSessionId` + the written artifact path.
  **Out** — `ArtifactCheckReport`: the `outcome` — `valid` (validated and committed into
  workspace state) or `reverted` (judged invalid and discarded from staging) — plus, when
  reverted, the `artifactCheck`: the failure message and the revert record.
- **Caller usage** — the agent receives the failure message and rewrites the artifact
  correctly; a write never silently corrupts the workspace.

Example:

```json
{
  "in": {
    "sessionId": "01j9xqr7t3",
    "parentSessionId": "01j9xq0f2m",
    "artifactPath": "portfolio/payments/features/feature-refunds.md"
  },
  "out": {
    "context": {
      "function": "check-step-artifact",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "reverted" },
    "artifactCheck": {
      "failureMessage": "frontmatter.status: 'shipped' is not one of the enum values",
      "revert": { "action": "restored", "from": "HEAD" }
    }
  }
}
```

Contract schemas — [harness/contracts/functions/check-step-artifact.input.schema.json](harness/contracts/functions/check-step-artifact.input.schema.json) and [harness/contracts/functions/check-step-artifact.output.schema.json](harness/contracts/functions/check-step-artifact.output.schema.json).

**Preconditions**

- The framework's artifact schemas are loaded and the written path resolves to one of them.
  Function 8 has already established a clean staging baseline: the target path was absent or
  tracked-and-clean against `HEAD`, so the staged write is the only staged content at its path.
- Trigger — the write-ended boundary, after every write.

**Postconditions**

- C6 holds by construction: workspace state advanced only by the validated write's commit, or
  the staged write was discarded — committed state never contained the invalid bytes.
- One log entry per write validation (`valid` / `reverted`). When reverted, the same entry's
  report carries the revert action, so there is no second revert entry.

**Invariants**

1. Every artifact write is validated against its matched artifact schema (path patterns + `type`
   disambiguation; schemas extend the harness base contract via `$ref`).
2. An invalid write is always **reverted** — discarded from staging: restore the tracked path
   from `HEAD`, or delete a newly created path — and denied with the failure message so the
   agent retries. The discard never touches workspace state: the invalid bytes existed only in
   staging. Unclean baselines were denied before the write (function 8, invariant 5), so
   `invalid but left in place` is not a function-9 outcome.
3. A valid write is **committed** in the same act — 1 validated write = 1 commit, attributed
   to the acting session (its `sessionId` in the commit message) so Git history and the
   session log correlate. The commit and the discard are the harness's only deliberate Git
   actions: the discard is recorded inside the validation entry's report (the revert record);
   the commit's record is the Git history itself, correlated by session id.

### 10. check-step-postconditions

Did this step deliver? The same condition machinery as function 5, applied to the step's
declared postconditions — and the producer of the step outcome that drives the whole instance:
function 3's cursor reads nothing else.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The step key is resolved by the
  hook plane from the returning dispatch and session ids — never supplied by an agent.
- **Out** — `ConditionCheckReport`: the aggregate `outcome` (`pass` / `fail`) +
  `conditionChecks`, exactly as function 5.
- **Caller usage** — on a passing outcome the orchestrator calls function 3 for the next step;
  on a failing one — per its `reports-handling` instruction (function 1) — it handles the
  failure messages and calls function 3 again — the failed step is not
  journaled executed, so the cursor resolves it again, the messages feeding the new pass. The
  failure stays inside the workflow: the user sees end-to-end workflow results only (function
  3, invariant 7).

Example:

```json
{
  "in": {
    "sessionId": "01j9xq0f2m",
    "parentSessionId": null
  },
  "out": {
    "context": {
      "function": "check-step-postconditions",
      "sessionId": "01j9xq0f2m",
      "parentSessionId": null,
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "pass" },
    "conditionChecks": [
      {
        "condition": {
          "kind": "postcondition", "type": "state", "id": "report_exists",
          "set_selector": {
            "set_type": "artifact",
            "artifact_types": [{ "alias": "report", "schema_id": "review-report" }],
            "set_query": "report.filter(a, a.slug == artifact)"
          },
          "set_predicate": "selected.size() == 1"
        },
        "outcome": "pass"
      }
    ]
  }
}
```

Contract schemas — [harness/contracts/functions/check-step-postconditions.input.schema.json](harness/contracts/functions/check-step-postconditions.input.schema.json) and [harness/contracts/functions/check-step-postconditions.output.schema.json](harness/contracts/functions/check-step-postconditions.output.schema.json).

**Preconditions**

- The step was dispatched from a correlated unresolved `step-resolution` entry and its execution
  is being evaluated — at the step-ended boundary.
- Trigger — the step-ended boundary (THE evaluation point: the step's session has ended, the
  state it left is final). The invocation runs in the dispatching (facilitator) session — the
  same session that owns the step's precondition check: at step-ended the step session has
  already closed.

**Postconditions**

- One log entry per step evaluation, carrying the step's outcome — the exact input of
  function 3's cursor — appended to the dispatching (facilitator) session's log.
- No artifact is touched — the invocation's own log entry is the only write.

**Invariants**

1. `state` assertions evaluate over persisted artifacts only — never agent memory (C2).
2. Postconditions are evaluated ONCE per step pass, at the step-ended boundary — the step's
   session has ended, the state it left is final; the step's own session end adds no second
   evaluation of the same state.
3. The step's outcome logs from this function: its postconditions hold, or they do not.
   This journaled outcome is exactly what function 3's cursor reads — a step whose latest
   outcome passes counts as executed.

### 11. check-workspace

Is the workspace, as a whole, in a valid state? The instance-less proof of C6: where function 9
guards each write as it lands, this function proves the invariant globally, on demand — the
audit and CI face of workspace validity.

**Interface**

- **In** — session attribution: `sessionId` + nullable `parentSessionId`, from a human
  terminal session (bash wrapper-minted) or a CI system session (the platform's ambient run
  id). The check scope is always the whole workspace (KISS — no scoping for now).
- **Out** — `WorkspaceCheckReport`: the `outcome` + `workspaceChecks`: one check per finding
  — its `filePath` (a file or a directory: structural findings — a missing directory, an
  unexpected or misplaced file — concern a path too) and its `failureMessage`.
- **Caller usage** — CI fails the pipeline on checks, blocking a merge that would break C6:
  the required, unskippable status check of the workspace's protected canonical branch
  (invariant 4). A human can run the command locally and use the failure messages for
  remediation; a framework-shipped **advisory pre-commit hook** may run the same command on a
  workspace clone to catch foreign mistakes at the desk — ergonomics only, acknowledged
  bypassable (`--no-verify`), never the guarantee.

Example:

```json
{
  "in": { "sessionId": "ci-run-42" },
  "out": {
    "context": {
      "function": "check-workspace",
      "sessionId": "ci-run-42",
      "parentSessionId": null,
      "workflowInstanceId": null
    },
    "outcome": { "status": "invalid" },
    "workspaceChecks": [
      { "filePath": "portfolio/payments/features/feature-refunds.md", "failureMessage": "frontmatter.status: invalid enum value" }
    ]
  }
}
```

Contract schemas — [harness/contracts/functions/check-workspace.input.schema.json](harness/contracts/functions/check-workspace.input.schema.json) and [harness/contracts/functions/check-workspace.output.schema.json](harness/contracts/functions/check-workspace.output.schema.json).

**Preconditions**

- The framework's artifact schemas and the workspace layout are loaded (fail-fast at load).
- A session is registered (function 0) — a human terminal session or a CI system session.
- Trigger — a CI pipeline on the workspace repository (every push / pull request), or a human
  at a terminal.

**Postconditions**

- The checks exist for the whole workspace; zero checks prove C6 globally.
- One log entry records the sweep in the invoking session's log, same as any other function —
  stdout and the exit status remain the immediate CI/human-readable signal, but the journaled
  entry is the durable record.
- No artifact is touched — validation never mutates artifacts.

**Invariants**

1. Every artifact is validated against its matched artifact schema, plus the
   cross-artifact rules a single write cannot see: scope/frontmatter coherence, parent linkage
   resolution, blocking open items — and the workspace structure itself against the workspace
   layout.
2. Validation reads the raw universe (`scan_raw`) of committed workspace state — the sweep
   must be able to enumerate invalid
   artifacts that valid-by-construction readers refuse to yield.
3. The sweep never mutates: it reports. Remediation goes through agents (unlike function 9's
   commit gate, this function's write-boundary counterpart).
4. The CI gate is normative and unskippable: the workspace's canonical branch is **protected**
   — no direct pushes — and every change, harness-authored or foreign, lands only through a
   merge whose required status check is this function running clean. The gate constrains
   **validity, not authorship**: anyone may author artifacts (agents, the user); nothing
   invalid becomes shared state. This closes what the write boundary cannot see — commits
   made outside the harness (a human's editor commit, a script, an unvalidated merge).

### 12. check-configuration

Is the embedding application's configuration a valid input to the harness? This validates
everything the other twelve functions trust: the configuration plane of the integrating application
(e.g. this repo's SAFe agentic framework) — the layout environment, every configuration file,
the semantic rules beyond schema, cross-configuration coherence, and the adapter bindings.

**Interface**

- **In** — session attribution: `sessionId` + nullable `parentSessionId`, from a human
  terminal session (bash wrapper-minted) or a CI system session (the platform's ambient run
  id). The framework root is where the harness runs (the `.env` layout environment anchors it —
  KISS, no root parameter for now).
- **Out** — `ConfigurationCheckReport`: the `outcome` + `configurationChecks`, mirroring
  function 11: one check per finding — its `filePath` (`.env`, a `conf/` file, an adapter binding) and its
  `failureMessage`.
- **Caller usage** — CI gates merges to the framework repository; an agent that edited
  configuration validates before considering its edit delivered; the harness itself runs the
  same validation at startup and refuses to serve on failures.

Example:

```json
{
  "in": { "sessionId": "ci-run-42" },
  "out": {
    "context": {
      "function": "check-configuration",
      "sessionId": "ci-run-42",
      "parentSessionId": null,
      "workflowInstanceId": null
    },
    "outcome": { "status": "invalid" },
    "configurationChecks": [
      { "filePath": "conf/workflows/verification.workflow.conf.yaml", "failureMessage": "step 'review': after reference 'biuld' does not resolve" },
      { "filePath": ".env", "failureMessage": "FRAMEWORK_SKILLS_DIR points to a missing directory" }
    ]
  }
}
```

Contract schemas — [harness/contracts/functions/check-configuration.input.schema.json](harness/contracts/functions/check-configuration.input.schema.json) and [harness/contracts/functions/check-configuration.output.schema.json](harness/contracts/functions/check-configuration.output.schema.json).

**Preconditions**

- The framework repository (its `.env`, `conf/`, and the harness adapters) is readable; no
  workspace is needed.
- A session is registered (function 0) — a human terminal session or a CI system session.
- Trigger — a CI pipeline on the framework repository (every push / pull request), or a human
  at a terminal.

**Postconditions**

- Full checks exist (validation never stops at the first failure).
- One log entry records the sweep in the invoking session's log, same as any other function —
  the pipeline log, stdout, and exit status remain the immediate signal, but the journaled
  entry is the durable record.

**Invariants**

1. Every configuration file validates against its contract schema — parsing and validation are
   one act; an unvalidated parse never escapes.
2. The semantic rules JSON Schema cannot express are enforced: non-empty steps, unique step
   slugs, resolvable step `after` references, unique condition ids per step, acyclic step
   DAGs, at least one positive capability weight per step; at catalog level, the advisory
   workflow graph resolves and is acyclic.
3. Cross-configuration coherence holds: workflow actors exist in the ACL, capability tags belong
   to the model catalog's vocabulary, step `artifact` slugs resolve to one of the framework's
   artifact schemas, and instruction/prompt/skill refs resolve to files in the framework layout.
4. The layout environment is validated like any file configuration: every required layout
   variable is present (from the process environment or the `.env` file) and points to an
   existing directory.
5. Adapter bindings are validated like any other configuration (against the adapter contract):
   host tool names, write verbs, and model id bindings that map to canonical profiles.
6. This function is the explicit, reportable form of the same validation that runs implicitly,
   fail-fast, at every harness start — the two can never diverge.

### General invariants

#### C0 - Workspace state definition

Workspace state is the union of:

- Persisted **artifacts** — the committed (`HEAD`) content of the workspace repository under
  the workspace state paths: an agent write becomes state only when function 9's commit gate
  promotes it (C6).
- Persisted **logs** — the harness's session logs under the workspace logs path:
  harness-authored, append-only, single-writer. They have no invalid-write window, so they are
  exempt from the commit gate and read directly from the working tree.

Both are first-class state for deterministic checks and replay.

#### C1 - Workspace-state scope

All preconditions and postconditions are evaluated strictly against persisted workspace state.

#### C2 - Harness assertion boundary

Condition checks executed by the harness only assert workspace state. The harness does not
assert private agent memory or transient, non-persisted host context.

#### C3 - Agent sourcing freedom

The actor agent may source from workspace data, external systems, tools, or web context. Harness
pass or fail is based only on persisted workspace state.

#### C4 - Methodology and host agnosticism

The harness is generic: it is not hard-coded to any methodology, host environment, or artifact
taxonomy. Methodology-specific semantics come from the embedding framework's workflow configs,
artifact schemas, skills, and templates. Host-specific event mapping comes from the adapter. The
harness only interprets the generic primitives it is given: workflow graphs, conditions,
artifact schemas, ACL grants, and the model catalog.

#### C5 - Schema-bound persistence

Every artifact is schema-bound to one of the framework's artifact schemas; every log entry is
bound to the log-entry contract. The selector model has one selector type: selecting persisted
artifacts. Where a
condition depends on log evidence, that evidence is read from the logs (part of workspace
state) and asserted as state. Artifacts are the framework's content; logs are the harness's
record — one workspace, two authors, no overlap.

#### C6 - Workspace validity

Workspace state — the committed (`HEAD`) content of the workspace repository — contains
exclusively schema-valid artifacts, at all times: the write boundary is a **Git commit gate**.

- The working tree is the staging area: an authorized agent write lands there (write-starting,
  function 8), is validated there (write-ended, function 9), and is then either **committed**
  into workspace state — state advances atomically, one validated write = one commit — or
  **discarded** from staging (restore the tracked path from `HEAD` / delete the new path) and
  denied with the schema reports so the agent retries. Committed state never contains invalid
  bytes — the transactional guarantee.
- Staging-baseline invariant: at write-starting the target path is either absent or
  tracked-and-clean against `HEAD` — dirty tracked targets, pre-existing untracked targets,
  and paths outside the artifact layout are denied (function 8, invariant 5). The staged write
  is therefore always the only staged content at its path, and the discard is always trivial.
- Relied on by every reader: the artifact repository reads committed state and is
  valid-by-construction (`discover()` raises rather than yield a schema-invalid artifact).
  Validators that must enumerate invalids read the raw universe (`scan_raw`) of committed
  state instead.
- Enables safe asynchronous / remote workspace sync: what syncs is committed state — a synced
  replica is trustably valid.
- The commit gate governs artifacts — the agent-authored plane. Logs are harness-authored,
  append-only, single-writer (C0): they have no invalid-write window and are exempt.
- **Guarantee scope by plane** — the harness's write boundary only sees harness-mediated
  writes; a foreign commit (a human's editor commit, a script, an unvalidated merge) can still
  land invalid bytes in a local `HEAD`. The invariant therefore holds at three strengths:
  **local staging** is guarded live (functions 8–9, the commit gate); a **local clone's
  committed state** is detect-and-remediate (function 11 detects, readers fail loudly —
  `discover()` raises — and remediation goes through agents); the **canonical branch** is
  guaranteed — it is protected (no direct pushes) and every change merges only through
  function 11's required, unskippable status check (function 11, invariant 4), so nothing
  invalid ever becomes shared state. A framework-shipped advisory pre-commit hook (running
  function 11) narrows the local window; it is ergonomics, never the guarantee.

#### C7 - Framework-agent scope

The harness governs the framework's agents, not the host. A hook event is processed only when
its agent resolves to a framework agent (a normalized identity the ACL declares); events from
any other host agent or session pass through untouched and unlogged.

---

## Design

### Invocation surfaces, one command system

Every harness function is exposed as a harness command: the Python executable function entry
point. It accepts one input object (session attribution fields + function-specific fields) and
returns the function's Report as `out`. Every invocation is session-bound — there is no
session-less surface. The command system is entered through three invocation surfaces,
one per session origin:

- **Hook invocation** — host event → adapter → harness command, for `agent`-origin sessions.
  The adapter builds the session attribution fields from the host event payload; this surface
  triggers functions 0–2 and 5–10 at their natural boundaries.
- **Adapter-mediated agent invocation** — agent asks through a trusted adapter surface →
  harness command, within an already-registered `agent`-origin session. This surface is
  available only for adapters that can attach host-observed session attribution outside
  model-authored arguments. It may serve functions 3–4. Hosts that cannot prove such
  attribution MUST NOT expose this surface for session-bound functions.
- **Direct session invocation** — a human terminal session (`sessionId` minted by a bash
  wrapper at shell start) or a CI system session (`sessionId` sourced from the platform's
  ambient run id) → harness command. The wrapper (bash or CI step) calls `register-session`
  first, then any function under that session's id — typically functions 11–12, the
  globally-scoped checks, but any function may in principle run under a human or system
  session.

**Session attribution.** Every journaling invocation carries the `sessionId` of the session it
runs in, and the id always comes from the surrounding mechanism — never self-reported by an
agent: hook-triggered functions read it from their own event payload (`agent` origin);
adapter-mediated agent invocations are attributed by the adapter, but only where the adapter has
a verifiable host-observed attribution mechanism (`agent` origin); a bash wrapper mints it once
per shell (`human` origin); a CI platform exposes it as an ambient run id (`system` origin).
There is no invocation without a session, and no journaling or step-scoped function refuses a
correctly-attributed session — the earlier "session-less" plane is gone: it is replaced by the
`human` and `system` session origins, both of which journal exactly like `agent` origins.

**Adapter-mediated attribution rule.** A trusted adapter surface is valid only when the adapter
can populate `sessionId` and `parentSessionId` from a host-observed source that the model
did not author. Acceptable mechanisms include a per-session process environment owned by the
host adapter or a host-native command/tool surface that passes session identity to the adapter
outside the tool arguments visible to the agent. A tool-boundary stamp that merely rewrites a
model-authored command argument is not sufficient. If a host lacks such a mechanism, functions
3–4 are not exposed as adapter-mediated commands for that host; the adapter-specific spec must
state that limitation. This rule is specific to `agent`-origin sessions: `human` and `system`
origins have their own sourcing mechanisms (the bash wrapper, the CI platform's ambient run
id), neither of which is an agent self-report either.

There is no separate hook logic outside the harness: the harness mediates all host hook events,
so the host adapter never becomes a second source of truth. The adapters only forward host
events and render the harness decision back into the host format.

### Boundary Normalization

Host events are normalized to harness boundaries before any policy is applied. The core harness
spec names boundaries only; adapter-specific specs bind host event names and host tool classes to
those boundaries. Dispatch tools carry the **step boundary** — the subagent session IS the step,
so step pre- and postconditions apply before and after it — while write tools carry the **write
boundary** (authorization and schema validity, never step conditions).

| Boundary | Adapter evidence | Functions |
| --- | --- | --- |
| session-started | Session-open event with no correlated unresolved `step-resolution` entry | 0 (registration — always first) + 1, 2 |
| step-starting | Dispatch about to open a step session | 5 (preconditions — THE enforcement point) |
| step-started | Session-open event correlated to an unresolved `step-resolution` entry | 0 (registration — always first) + 6, 7 |
| write-starting | Write tool about to mutate an artifact path | 8 (authorization + staging baseline — can deny) |
| write-ended | Write tool has landed a staged write on an artifact path | 9 (schema validity — the commit gate) |
| step-ended | Dispatch returned after the step session ended | 10 (postconditions — THE evaluation point) |

This keeps the harness env-agnostic: only the adapter maps host-specific event names and tool
payloads, and only the adapter binding decides which tools are dispatches and which are writes.
The step-starting and step-ended boundaries occur in the dispatching (facilitator) session —
their entries journal to its log; the step session's own log carries its registration (0), its
context resolutions (6–7), and its write-boundary entries (8–9).
Observational host events that do not enter one of these boundaries are adapter telemetry, not
harness functions, and they are not written to the harness journal.

The boundary names are lifecycle participles precisely to dissolve the step/session clash the
1 step = 1 session invariant creates: **step-starting** (the dispatch about to open the step —
it can deny) and **step-started** (the step session having opened — it can only inject) coincide
in time but differ in capability. No merged "step-start"
boundary could carry both capabilities — no host guarantees a session hook can veto its own
session.

**Framework-agent gate (C7)** — normalization resolves the event's agent and host session ids
first: only events
whose agent is a framework agent proceed to any function; everything else passes through
untouched and unlogged. The harness governs the framework's agents, never the host's other
inhabitants.

### Source layout

Five packages, one dependency direction — `commands → services → {stores, config}`, with
`utils` beneath everything:

```text
src/
  application.py      # the composition root: builds the object graph (config dataclasses fail-fast) and dispatches argv to one command
  commands/           # usage entry points (≈ web controllers): parse input, invoke the service(s), render the result — no domain logic
  services/           # the logical domain services commands use: all harness logic lives here
    session_registration/ # SessionRegistrar + its result: SessionRegistration (function 0)
    step_resolution/  # StepResolver + its result: StepResolution
    model_resolution/ # StepModelResolver + its result: ModelProfileReport
    checking/         # the checkers + ConditionEvaluator + their result reports and ConditionCheck
    context_resolution/ # the four context resolvers + their instruction/skill report classes
    hooks/            # the hook plane: HookNormalizer (+ its Boundary), SessionCorrelator
  stores/             # data access, mirroring services: one family subpackage per store — used by services exclusively
    artifact_store/   # ArtifactStore + its persisted dataclasses: Artifact, Finding
    session_log_store/ # SessionLogStore + its persisted/derived dataclasses: Log, LogEntry, StepRef, the Report base, WorkflowInstanceView
  config/             # ConfigLoader + the configuration dataclasses it constructs (parse + contract-validate + semantic rules)
  utils/              # domain-free mechanics: loaders (env/json/yaml/markdown) + higher-level SchemaValidator / JsonlStore
```

Four placement rules settle the boundary questions:

- **Harness contracts use JSON Schema 2020-12 and GSM-style composition.** The harness contract
  dialect is `https://json-schema.org/draft/2020-12/schema`, matching GSM/ITIP's Archetype
  validation posture rather than draft-07 portability. Contract extension uses a root `$ref`
  when a schema specializes exactly one base contract (e.g. a function output rooting
  `report.schema.json`), with sibling constraints (`properties`, `required`, `oneOf`,
  `unevaluatedProperties`) added directly. `allOf` is reserved for true facets, conditional
  composition, or multi-source intersection — not for ordinary single-base extension.
- **A contract is loaded by the layer that owns the boundary it guards.** The
  `*.conf.schema.json` contracts guard the configuration boundary, so `config/` loads them —
  parsing and validation are one act there. The data contracts (the artifact base contract, the
  log-entry contract, and the framework's artifact schemas) guard the workspace read/write
  boundary, so `stores/` loads them (`artifact_store/` the artifact schemas, `session_log_store/`
  the log-entry contract) — that is where valid-by-construction (C6) is enforced. Services never
  touch a raw schema: they receive typed configuration views and valid-by-construction entities.
- **Adapter configuration goes through `config/` like everything else.** The split between
  framework-supplied configuration (`conf/`, declared by the embedding application) and
  harness-internal configuration (`harness/adapters/<env>/`, resolved structurally from the
  harness tree) is a difference of *provenance*, not *mechanism*. One loader discipline parses
  and contract-validates every configuration file; the config plane keeps the two provenances
  distinct in its API (`ConfigLoader.load_layout/load_acl/…` for `conf/` and `.env`,
  `ConfigLoader.load_adapter_binding(env)` for adapters) so
  neither leaks into the other.
- **Shared mechanics live in `utils/`.** `config/` and `stores/` both parse files and
  validate instances against JSON Schema contracts, so the mechanical primitives are one shared
  package: the `.env` loader, the safe YAML loader, the JSON / JSONL readers, and the schema
  validator (contract compilation, `$ref` registry, instance validation returning raw
  reports). `utils/` is
  strictly domain-free — it knows no artifact, workflow, or ACL, depends on nothing internal,
  and returns plain data; each caller layer turns raw reports into its own typed views or
  entities. Anything with domain meaning belongs to its owning layer, never to `utils/`.

### Classes

The class diagram [`harness-src-classes.puml`](harness-src-classes.puml) is the
prescriptive class model of `src/`: **1 folder = 1 module = 1 package** in the diagram. Classes
expose their public interface — what other classes call — plus one **private, constructor-
injected attribute per dependency**, typed after the collaborator class (`-_session_log_store :
SessionLogStore`); dataclasses expose public typed attributes. **Method names are verb + subject**
(`resolve_step`, `check_step_preconditions`, `load_workflow_catalog`, `append_log_entry`).
Methods return **typed results, never bare dicts**: services return the result dataclasses
homed in their own family subpackage, and the JSON dict appears only at the command boundary,
where the contract
lives. External dependencies are drawn: **cel-python** (CEL compilation/evaluation, used by
`ConditionEvaluator`), **jsonschema** (contract validation, used by `SchemaValidator`), and
**PyYAML** (safe loading, used by `YamlLoader`) — the harness's only third-party imports. One
dependency direction, as in the source layout: `commands → services → {stores, config}`, with
`utils` beneath `stores` and `config`.

![Harness source classes diagram](harness-src-classes.png)

- **`application` (src root)** — `Application` is the composition root, a single module at the
  root of `src/`, above every package: it builds every configuration dataclass through
  `ConfigLoader` (fail-fast) and wires the object graph, then dispatches `argv` to one command
  (`dispatch_command`).
- **`commands`** — `Command` is the single interface (`execute_function`), realized by
  **fourteen commands — one per harness function plus `HookCommand`** — each holding its
  service(s) as private attributes: parse the function's `in` object, invoke, render the typed
  result to the `out` object. No command composes services: functions 3 and 4 are fully
  independent, and whoever needs both composes them outside the harness. `HookCommand` is the
  hook entry point: `HookNormalizer` gives the boundary, `SessionCorrelator` resolves
  session-start events to function 0's and the context resolvers' In — exact match via the session's
  `parentSessionId` first, the unresolved-`step-resolution`-entry actor heuristic as fallback —
  and the boundary's commands run, registration always first — no policy of its own.
- **`services`** — all harness logic, one service per function, grouped in **subpackages
  by family**, and each result dataclass homed in the subpackage of the classes returning it —
  **exclusively with them**:
  - `session_registration/` — `SessionRegistrar` (0 → `SessionRegistration`);
  - `context_resolution/` — the four context resolvers (1–2, 6–7) with their report classes:
    `WorkflowInstructionsReport`, `WorkflowSkillsReport`, `StepInstructionsReport`, and
    `StepSkillsReport`;
  - `step_resolution/` — `StepResolver` (3 → `StepResolution`);
  - `model_resolution/` — `StepModelResolver` (4 → `ModelProfileReport`);
  - `checking/` — `StepPreconditionChecker` (5), `StepPostconditionChecker` (10),
    `StepAuthorizationChecker` (8 — live only: `check_step_authorization(actor,
    artifact_path)`), `StepArtifactChecker` (9), `WorkspaceChecker` (11),
    `ConfigurationChecker` (12), plus `ConditionEvaluator` (the CEL
    machinery functions 5 and 10 share, via cel-python) — with their results `CheckReport`
    (outcome + findings + condition checks) and `ConditionCheck`;
  - `hooks/` — `HookNormalizer` with its `Boundary` enum, and `SessionCorrelator` (hook plane
    only — the context resolvers take their resolved keys, never raw events).

  **Report identity rule:** every service returns a concrete `Report` subtype; every command
  returns that same report object as its `out`; every hook returns or forwards that same
  report object in the host format; and the log entry stores that exact object under
  `report`. `outcome` and `context` live on the `Report` base (homed in
  `stores/session_log_store/`, alongside `LogEntry`), while each subtype adds its
  function-owned specific property. The log is therefore not a second projection of the
  result — it is the persisted report itself, wrapped only by the entry's own `timestamp`.
- **`stores`** — the data-access layer, mirroring `services`: one **family subpackage per
  store**, each homing its own persisted (or derived) dataclasses — no gateway, no facade;
  services depend on exactly the store(s) they need.
  - `artifact_store/` — `ArtifactStore` owns the artifact side (`is_staging_clean(path)` backs
    function 8's invariant 5 — the target path is absent or tracked-and-clean against `HEAD`,
    checked by `StepAuthorizationChecker` before a write is admitted; `discover_artifacts` reads
    committed state and raises on
    an invalid artifact, C6; `scan_raw_paths` enumerates the raw universe; `validate_artifact`
    produces `Finding`s; `commit_artifact` promotes a validated staged write into committed
    state and `revert_artifact` discards an invalid one — together the harness's only
    deliberate Git actions), alongside `Artifact`
    and `Finding` (source, rule, message — the check finding persisted inside `CheckReport`).
  - `session_log_store/` — `SessionLogStore` owns the log side (`create_session_log` writes
    function 0's registration entry — the file's first line; `mint_workflow_instance_id` mints
    an instance id — prefixed by its `workflow_slug` (e.g. `verification-01J9XQ`) — no file;
    `find_latest_open_instance(workflow_slug)` backs function 3's invariant 8: scans
    `<workspace>/logs/` for that prefix, groups entries per instance, and returns the most
    recently active still-open instance (or `None`), see [Logging](#logging); `load_session_log`
    hydrates a `Log`; `load_workflow_instance_view`
    assembles the cross-log `WorkflowInstanceView` — discovery scan + `timestamp`-ordered assembly,
    see [Logging](#logging); `append_log_entry(session_id, entry)` writes one
    contract-bound entry), alongside its persisted dataclasses `Log` (one session log:
    `session_id`, `entries`), `LogEntry` — the log-entry-contract wrapper: `timestamp` and
    `report` (the report carries its own `context`: `function`, `session_id`, nullable
    `parent_session_id`, nullable `workflow_instance_id`); `Report` — the abstract base every
    service result extends (the subtypes live with their services); and the derived
    `WorkflowInstanceView` (`workflow_instance_id`, `entries`; queries
    `list_executed_steps()`, `find_latest_outcome(step_slug)`,
    `find_unresolved_step_resolution(actor)`) — assembled here, never persisted.

  Frozen dataclasses throughout: public typed attributes, no getters/setters.
- **`config`** — `ConfigLoader` plus the configuration dataclasses it constructs, homed
  together: `FrameworkLayout`, `AccessControlList`, `ModelProfiles`/`ModelProfile`,
  `WorkspaceLayout`/`WorkspaceNode`, `WorkflowCatalog`/`Workflow`/`Step`/`Condition`,
  `AdapterBinding`. Each `load_*` method performs parse + contract-validation + semantic rules
  - dataclass construction as ONE act; there is **no aggregate `FrameworkConfig`**: each
  service receives exactly the dataclasses it needs.
- **`utils`** — the domain-free mechanics, in two layers. LOADERS — one per format:
  `EnvLoader`, `JsonLoader`, `YamlLoader` (PyYAML), `MarkdownLoader` (frontmatter + body).
  HIGHER-LEVEL utilities abstracting the loaders: `SchemaValidator` (jsonschema; loads
  contracts through `JsonLoader`) and `JsonlStore` (the log file: `load_entries` /
  `append_entry` — a store, not a reader, because it persists too). Plain data
  in, plain
  data out.

### Hook adapter layout

The dispatch script is shared and generic across every host; only the per-host registration and
tool binding live under each adapter's own subfolder. See `harness/adapters/README.md` for the
full adapter contract.

`dispatch.sh` is intentionally thin and env-agnostic: it takes the event name and the environment
id as its two arguments and forwards the raw event payload to `harness.py hook --event <name>
--env <env>`, exiting with the harness result. Each adapter's `hooks.yaml` supplies its own
environment id as the second argument.

Session identity is an adapter obligation for `agent`-origin sessions: the binding names the
payload keys where the host carries its session ids (`host_session_keys`,
`host_parent_session_keys` in `tools.yaml`); the adapter extracts them, sanitizes each to a safe
slug (`[a-z0-9-]` — the id becomes a log filename), and attributes adapter-mediated agent
invocations to their session only when that adapter has a verifiable host-observed attribution
mechanism per [Invocation surfaces](#invocation-surfaces-one-command-system) — never from a
model-authored argument. `human`- and `system`-origin sessions source their `sessionId` outside
this adapter binding entirely (the bash wrapper; the CI platform's ambient run id).

```text
.env                  # framework layout environment: where skills, agents, schemas, templates, workspace live
conf/                 # env-agnostic framework configuration (owned by the embedding framework)
  access-control-list.conf.yaml  # framework-defined roles -> agents (from agents/) mapping; privileges are artifact-kind + action
  workspace.conf.yaml            # workspace layout blueprint (nodes: path -> schema/template/cardinality)
  model-profiles.conf.yaml       # canonical model catalog: capability_scores + cost_rank per model
  workflows/                     # *.workflow.conf.yaml — steps declare actor and weighted capabilities
harness/
  adapters/
    dispatch.sh       # shared, generic dispatcher: stdin JSON -> harness hook command
    github-copilot/
      hooks.yaml      # YAML source rendered to .copilot/hooks.json
      tools.yaml      # host tool names, write verbs, payload keys (adapter binding)
      models.yaml     # host model id bindings to canonical profiles
  contracts/          # generic harness schemas: artifact, log-entry, report, context, actions,
                      # conf/<name>.conf.schema.json — one contract per configuration file, and
                      # split functions/<function>.input|output.schema.json contracts
  src/
    application.py    # the composition root (builds the object graph, dispatches to one command)
    commands/         # usage entry points: argparse dispatch
    services/         # domain logic in family subpackages: session_registration/ step_resolution/ model_resolution/ checking/ context_resolution/ hooks/ (each with its result dataclasses)
    stores/           # data access in family subpackages: artifact_store/ (ArtifactStore, Artifact, Finding) + session_log_store/ (SessionLogStore, Log, LogEntry, StepRef, Report base, WorkflowInstanceView)
    config/           # ConfigLoader + the configuration dataclasses (from conf/, .env, adapters)
    utils/            # domain-free mechanics: .env/YAML/JSON(L) loading, JSON Schema validation
```

### Configuration plane

Every configuration source has a contract and a configuration dataclass (in `src/config/`,
beside the `ConfigLoader` that builds it):

| Configuration | Contract | Typed view |
|---|---|---|
| `.env` layout environment | required-variable set (below) | `FrameworkLayout` |
| `conf/access-control-list.conf.yaml` | `conf/access-control-list.conf.schema.json` | `AccessControlList` |
| `conf/model-profiles.conf.yaml` | `conf/model-profiles.conf.schema.json` | `ModelProfiles` |
| `conf/workspace.conf.yaml` | `conf/workspace.conf.schema.json` | `WorkspaceLayout` |
| `conf/workflows/*.workflow.conf.yaml` | `conf/workflow.conf.schema.json` | `WorkflowCatalog` / `Workflow` / `Step` |
| `harness/adapters/<env>/tools.yaml` | `conf/adapter.conf.schema.json` | adapter binding (internal config) |
| `harness/adapters/<env>/models.yaml` | `conf/model-bindings.conf.schema.json` | model binding (internal config) |

The framework's **layout is environment, not file configuration**: the framework declares WHERE
its pieces live via environment variables, loaded from a `.env` file at the framework root
(process environment takes precedence — CI and containers override without editing files).
`FRAMEWORK_DIR` anchors the layout: it is the one ABSOLUTE path — defaulting to the directory
containing the `.env` file — and every other layout variable resolves relative to it:

```bash
FRAMEWORK_DIR=/abs/path/to/safe-agentic-framework   # the anchor: absolute; all other paths resolve relative to it
FRAMEWORK_SKILLS_DIR=.github/skills
FRAMEWORK_AGENTS_DIR=agents
FRAMEWORK_SCHEMAS_DIR=schemas
FRAMEWORK_TEMPLATES_DIR=templates
FRAMEWORK_WORKSPACE_DIR=../safe-agentic-portfolio
```

`FrameworkLayout` is the dataclass over these variables — validated fail-fast like any other
configuration source (every variable present, every path existing — function 12, invariant 4).
WHAT the framework declares (grants, catalogs, workflows) stays in `conf/` files under contract
schemas.

Two planes, never conflated: the **framework** is the application embedding the harness; the
**workspace** is the data plane the harness checks. The harness's own contracts and adapters are
harness-owned and resolved structurally from the harness code — internal configuration goes
through the same loader discipline, but is never declared in `conf/`.

Parsing and validation are one act: `ConfigLoader` parses the source and validates it against
the contract in the same step — an unvalidated parse never escapes the config package. Loading a
workflow configuration additionally enforces the semantic rules JSON Schema cannot express
(non-empty steps, unique step slugs, resolvable `after` references, unique condition ids within
a step, an acyclic step DAG, at least one positive capability weight per step); the workflow
catalog additionally validates the advisory workflow graph (references resolve, acyclic).
There is no aggregate configuration object: `ConfigLoader` builds each configuration dataclass
once at `Application` initialization, and each service receives exactly the dataclasses it
needs (interface segregation), so
every interaction with the harness fails fast — with the full reports — on an invalid
configuration before any function runs.

### Workspace Git plane

Git is not an implementation detail of the workspace: it is the harness's **transaction and
integrity mechanism**, and the versioned-workspace capability is a design principle in its own
right. Four principles synthesize how Git realizes C0/C6; the normative statements live in the
functional contract (C0, C6, functions 8, 9, and 11) — this section is their design-level
synthesis.

**1. Committed state IS workspace state.** The workspace is a Git repository and its committed
state (`HEAD`) is the data plane the harness reads and checks; the working tree is nothing but
the write staging area. Readers (`discover`, `scan_raw`) read committed state; conditions and
sweeps assert committed state; what syncs to a replica is committed state — so the replica
story and the validity story are the same Git story (C6).

**2. The write boundary is a commit gate.** An agent write is a transaction: function 8 admits
it only onto a clean staging baseline (target absent or tracked-and-clean against `HEAD` — the
staged write is always the only staged content at its path), the write lands in staging,
function 9 validates the staged bytes, and the transaction either **commits** — 1 validated
write = 1 commit, attributed to the acting session (`sessionId` in the commit message) so Git
history and the session logs correlate — or **discards** (restore from `HEAD` / delete the new
path). The commit and the discard are the harness's only deliberate Git actions; committed
state never holds invalid bytes — transactional by construction.

**3. The integrity guarantee is graded per plane.** The write boundary sees only
harness-mediated writes, so the guarantee scales with distance from the gate: **local
staging** — guarded live (functions 8–9); **a local clone's `HEAD`** — detect-and-remediate
(a foreign commit can land invalid bytes; function 11 detects, readers fail loudly, agents
remediate); **the canonical branch** — guaranteed: protected, no direct pushes, every change
merging only through function 11's required, unskippable status check (function 11, invariant
4). The gate constrains **validity, not authorship** — anyone may author artifacts; nothing
invalid becomes shared state. A framework-shipped advisory pre-commit hook (running function
11 locally) narrows the local window — ergonomics, never the guarantee.

**4. Logs ride outside the gate.** Logs are workspace state (C0) but harness-authored,
append-only, and single-writer: they have no invalid-write window, so they are exempt from the
commit gate and read directly from the working tree. The commit gate governs exactly the
agent-authored plane — artifacts — and nothing else.

### Logging

One append-only JSONL file per session:
`<workspace>/logs/<sessionId>.log.jsonl`

**1 session = 1 writer = 1 log file.** `register-session` (function 0) creates the file —
its registration entry is the first line — so nothing logs at a session's level before
registration, and no two writers ever contend for one file: appends are single-writer by
construction (no locking), and a log stops growing when its session ends, so growth partitions
by session lifecycle and no rotation policy is needed.

**The entry contract.** Every entry is the record of exactly one completed function
invocation — 1 invocation = 1 entry: entries are never shared between functions and never
rewritten. An entry has exactly two top-level properties: `timestamp`, the wall-clock time the entry
was appended, and `report` — the generic report envelope plus the function-specific payload; the
report carries its own `context`, so the log entry never maintains a second context projection.
Example:

```json
{
  "timestamp": "2026-07-08T14:32:07Z",
  "report": {
    "context": {
      "function": "check-step-postconditions",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": "verification-01J9XQ"
    },
    "outcome": { "status": "pass" },
    "conditionChecks": []
  }
}
```

Contract schema — [harness/contracts/log-entry.schema.json](harness/contracts/log-entry.schema.json).

**The status rule.** The only status is the function's outcome, and each function defines
and owns it in its own I/O contract, inside its report — under ONE shared field name,
`outcome`, so every report carries one, even where a single value is possible: 0
`registered`; 1–2, 4, and 6–7 `resolved`; 3 `step-resolution`/`no-next-step`;
5 and 10 `pass`/`fail`; 8 `allowed`/`denied`; 9 `valid`/`reverted`; 11–12
`valid`/`invalid`; all functions may also return `input-error`, `state-error`,
`configuration-error`, `adapter-error`, or `system-error`. Error outcomes are ordinary outcomes
in the same report envelope; they carry the `error` detail object — required on error statuses,
forbidden on success statuses — rather than using a
separate error-report type. One field name for uniform handling, function-owned values — no
envelope status field, no global status enum. Two consequences:

- **Every other status is derived.** No step, session, instance, or workspace owns a stored
  status: any lifecycle, for any view, is reconstructed from the entry sequences —
  `function` plus the report's outcome. A step is *executed* when its latest function-10
  entry reports `outcome: pass` (function 3's cursor); the workspace is *valid* when
  function 11 runs clean now. Verdicts are journaled, entity states are recomputed — the
  harness keeps no status registers.

- **A failed invocation logs nothing here.** Only agents and the host trigger harness
  functions, so an invocation that crashes surfaces at its trigger plane — hook exit
  status, agent command stderr, CI pipeline — and is recorded on the host's side,
  correlatable with the session's log via the session id when needed. The harness
  journals completed invocations only — it could not reliably journal its own crash anyway.

**Instance views.** A workflow instance owns no file: its complete, ordered, replayable
history — every resolution, check, authorization decision, and revert — is the
*instance view*: the union of the entries carrying its `workflowInstanceId` across the
session logs.

- **Discovery** — the instance's entries are found by scanning `<workspace>/logs/` (an index
  is a permissible later optimization, not a contract change).
- **Latest-open resolution (function 3's deduction)** — `resolve-step(workflowSlug)` never
  receives an instance id (function 3, invariant 8): a minted `workflowInstanceId` is always
  prefixed by its `workflowSlug` (e.g. `verification-01J9XQ`), so `find_latest_open_instance`
  filters candidate entries by that prefix, groups them per instance, and — among instances
  still *open* (function 3, invariant 1: at least one step not journaled executed) — returns
  the one whose latest entry (by `timestamp`) is most recent; none open (or none found) means
  a new instance opens. This is a discovery scan, not an index — same cost class as instance-view
  assembly, below.
- **Ordering — `timestamp` plus single-driver invariant** — every entry's `timestamp` (the log entry's
  wall-clock write time) is the cross-log total ordering key: entries across every session log
  sort by `timestamp`, giving a single total order for the instance view regardless of which session
  wrote which entry. Within one session log, file order and `timestamp` agree by construction (a
  single-writer log only ever appends forward in time). At most one live session drives a
  workflow instance at any time (the single-driver invariant), so a handoff between driving
  sessions is just a `timestamp`-ordered continuation, not a special case. Function 3's cursor reads
  "latest wins" over this `timestamp` order. The fixed function lifecycle order (the table in
  [The harness functions](#the-harness-functions)) is a hardcoded, static check: for a given
  step, its function invocations MUST appear in that legal order — it validates the `timestamp`-ordered
  sequence, it does not replace it.
- **Sanitization (adapter obligation)** — `sessionId` becomes a log filename regardless of its
  origin: the adapter (`agent`), the bash wrapper (`human`), or the CI step wrapper (`system`)
  MUST normalize it to a safe slug (`[a-z0-9-]`) before it reaches the harness — a raw id is a
  path-traversal vector.

A note on sync: many small per-session files partition merge conflicts under asynchronous
workspace sync (C6) far better than one shared log would — an accepted design trade against
the cross-log assembly of instance views.

The logs are the audit and replay trace, but not the branching input: `resolve-step` reads
only journaled step outcomes (through the instance view); every other check recomputes from
workflow definitions plus current artifacts.

---

## Development

### Python conventions

- **PEP 8 / PEP 257** — packages and modules `snake_case`, classes `PascalCase`,
  functions/variables `snake_case`, constants `UPPER_SNAKE_CASE`; a docstring on every public
  module, class, and function stating its role.
- **One class per module** where reasonable; the module name is the class name in snake case
  (`step_checker.py` → `StepChecker`). Every package exports its public surface explicitly via
  `__all__`.
- **Full typing** — `from __future__ import annotations` everywhere; every public signature
  fully annotated; configuration crosses layer boundaries as dataclasses and workspace data as
  entities, never as raw dicts — the JSON dict exists only at the command boundary.
- **No getters/setters/hassers/issers** — Python has no accessor convention: model and
  configuration classes are **frozen dataclasses** exposing public typed attributes directly
  (`entry.report`, never `entry.get_report()`); computed facts are query methods named verb +
  subject (`log.list_executed_steps()`); boolean queries read as predicates
  (`acl.is_framework_agent(actor)`) only where they compute, never to wrap an attribute.
- **Method names are verb + subject** — `resolve_step`, `check_step_preconditions`,
  `load_workflow_catalog`, `append_log_entry`; never bare `check()` / `load()` / `run()`.
- **Immutability by default** — dataclasses are `frozen=True`; collections cross boundaries as
  tuples and read-only mappings; entities expose behavior, not mutable attribute bags.
- **Fail fast, loudly** — domain exceptions (`ConfigError`, …) carry the FULL reports;
  no silent fallbacks, no blanket `except Exception`.
- **No import-time side effects; constructor injection only** — the composition root
  (`application.py`, at the src root) is the single place that builds the object graph; no
  globals, no singletons, no service locators.

### SOLID

- **S**ingle responsibility — one reason to change per class; the re-implementation target is a
  1:1 function → service → command alignment (each service realizes exactly one harness
  function).
- **O**pen/closed — behavior extends through configuration (workflows, catalog, ACL, adapter
  bindings), never by modifying the engine.
- **L**iskov substitution — every typed view and entity honors its base contract (`Artifact`
  subtypes are substitutable wherever an artifact is read).
- **I**nterface segregation — small, intent-named service surfaces (`resolve`, `check`,
  `register`, `audit`); no god objects.
- **D**ependency inversion — services depend on constructor-injected collaborators; only the
  composition root knows the concrete wiring.

### TDD

Red–green–refactor against the functional contract: each function's Invariants, Preconditions,
Postconditions, and Interface are the test oracle. Write the failing test from the contract
clause first, then the code that satisfies it. A contract clause without a test does not exist;
a code branch justified by no clause is a candidate for deletion.

### Unit testing

`harness/tests/unit/` is a structural mirror of `harness/src/`: one test module per src module
(`tests/unit/<package>/test_<module>.py` ↔ `src/<package>/<module>.py`), one test class per src
class. Isolation comes from the constructor-injection convention — collaborators are replaced
by fakes and tmp-dir workspaces, never by monkey-patching internals.

### Functional testing (proposal)

`harness/tests/functional/` — one test module per harness FUNCTION (thirteen), exercising the real
command entry point over a fixture framework configuration and a fixture workspace:

- each test asserts the full Interface (In → Out), the Postconditions (exact log entries
  appended; workspace untouched — or the staged write committed / discarded for function 9),
  and the invariants observable
  from outside;
- log replay tests: re-running against a golden fixture set of session logs must be
  byte-stable per log and merge-stable per instance view — the determinism check;
- CI runs both suites on every push; functions 11 and 12 additionally run as pipeline gates on
  the workspace and framework repositories respectively.
