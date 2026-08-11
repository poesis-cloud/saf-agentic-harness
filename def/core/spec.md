# Agentic Harness

The harness is the deterministic execution core of an agentic framework. It owns:

- resolution of step, workflow, and model,
- checking of step's conditions, authorization, and artifacts,
- resolution of agent's instructions and skills — the context injected into sessions
  at session and step start,
- logging of all that.

Agents stay limited to the irreducible work: generating content, judging within a step, and actuating host tools.

The harness does not orchestrate: the **orchestrator agent** orchestrates (converses with the
user, starts workflows on assent, dispatches subagent's steps). The harness **resolves, checks, and records** — deterministically, from persisted state and validated configuration only.

The harness is methodology-agnostic and host-agnostic: it knows nothing of SAFe, Scrum, or any
other methodology, and nothing of GitHub Copilot, VS Code, or any other host. It consumes generic
workflow definitions, artifact schemas, an ACL, and a model catalog supplied by the framework
that embeds it. Methodology-specific concepts live in the framework's skills, artifact schemas,
and workflow configs; host-specific concepts live entirely outside this specification — this
document is fully agnostic to how it is invoked and never needs to describe that mechanism.

This document is the canonical harness specification: **the functions section is the functional
contract** (it changes only by explicit design decision, never by refactoring), and the design
and implementation parts prescribe its realization.

## Summary

- [Terminology](#terminology) — the shared nouns: framework, workspace, artifact, log,
  session, orchestrator, workflow, workflow instance, step.
- [The harness functions](#the-harness-functions) — the functional contract: twelve functions,
  numbered and ordered as they sequence in the diagram, each specified by its interface (with
  JSON I/O contract), pre/postconditions, and invariants.
  - Session (0, 11): [`start-session`](#0-start-session) · [`end-session`](#11-end-session)
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
  - [Internal validation — not functions](#internal-validation--not-functions) — how
    configuration and workspace validity are enforced without an exposed function.
  - [General invariants](#general-invariants) — C0–C8: workspace state definition, assertion
  scope, agnosticism, schema binding, workspace validity, framework-agent scope, session
  ending.
- [Design](#design) — trigger planes, boundary normalization, source layout, class design,
  configuration plane, workspace Git plane, logging.
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
  (`<workspace>/logs/<sessionId>.log.jsonl`), created by function 0 (`start-session`)'s entry —
  its first line. Every completed function invocation appends exactly one entry to the log of
  the session it ran in, and the log-entry contract schema-binds every entry. A workflow instance
  owns no file: its history is its *instance view* — the ordered union of the entries carrying
  its id across the session logs (see [Logging](#logging)). Logs are persisted workspace state
  (C0) — a condition may read log evidence and assert it as state (C5) — but they are not
  artifacts: agents never write logs, and no step produces one.
- **Session** — the harness's unit of execution identity and log ownership: a bounded span of
  work, opened at the session-started boundary and registered by function 0 (`start-session`)
  under a `sessionId` that names it for the life of its log — and, best-effort, closed by
  function 11 (`end-session`) with a final entry when the surrounding mechanism can observe
  that end (C8). A session is performed by exactly one framework
  **agent** — the orchestrator (driving a workflow) or a dispatched subagent (acting a step) —
  fixed for the session's entire lifetime: the harness registers agent sessions, nothing else.
  A session serves **at most one workflow instance**: an orchestrator session drives at most
  one (one workflow end = one return to the user, which ends the session; engagement and
  selection sessions before assent drive none), and a step session IS its step (1 step = 1
  agent = 1 session = 1 artifact). The converse does not hold — a half-finished instance
  legitimately continues under a LATER orchestrator session (mid-instance returns; function
  3's latest-open-instance deduction and the single-driver invariant carry the handoff) — so
  an instance's view spans sessions, never a session spanning instances: no session's log
  ever carries entries of two workflow instances.
  A session's `sessionId` is always minted or observed by the mechanism
  surrounding its opening, never self-reported by the acting agent, so that every session's
  identity is independently verifiable at the moment it opens.
- **parentSessionId** — links a session to the session whose action opened it, forming a
  parent chain of arbitrary depth: an orchestrator session opens a step session at dispatch, or
  a session opens a nested session. A session without a `parentSessionId` is a root — the
  first session of its lineage.
- **Orchestrator** — the orchestrator agent a workflow declares to drive its instances. Its
  session receives its workflow instructions — selection and return-handling (function 1) —
  and its procedure skills (function 2); it converses, obtains assent, calls resolution, and
  dispatches steps. An orchestrator is an **agent**; an agent is an **actor** only when acting at a step —
  the orchestrator normally never is: steps are acted by dispatched subagents.
- **Workflow** — a configuration entity (`conf/workflows/*.workflow.conf.yaml`): an atomic,
  artifact-delivering unit of the methodology, made of steps. Each workflow declares its
  **orchestrator** — the orchestrator agent that drives its instances; the orchestrator's
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

This is the functional contract: everything the harness exposes is exactly one of these twelve
functions, every log entry names the function that produced it, and every harness command is
an entry point into one of them. (Configuration and workspace validity are enforced too — but
internally, not as functions: see
[Internal validation](#internal-validation--not-functions).) A **harness command** is the
Python executable function entry
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
for functions 8–9). Whatever surrounds each invocation — a hook mechanism — supplies
the session fields; a raw agent-authored value is not trusted. Function 0 is the bootstrap
exception: its `in` always carries the framework agent name as a required `agent` slug,
because no opening exists yet and the session record is the only place to attach that
identity — the harness opens agent sessions: harnessing agents is what this function, and
the harness itself, is for.
Every function is session-bound: there is no session-less invocation.

Every `out` object is a concrete **Report** object: the exact object returned by the service,
rendered by the command, and persisted byte-identically as the
log entry's `report`. Reports share the `context` object (`function`, `sessionId`, nullable
`parentSessionId`, nullable `workflowInstanceId` — the exact invocation context the log entry
persists) and the `outcome` object (`status`, plus the `error` detail — required on error
statuses, absent otherwise), and add one function-owned specific property where needed. Any
session metadata beyond correlation, such as framework-agent identity when applicable, is
recorded by function 0's registration and recovered through `sessionId` when needed. The
normative schemas live at `contracts/api/<function>.input.schema.json` and
`contracts/api/<function>.output.schema.json`; each function section links to
them. Shared contracts live beside them:
[contracts/conf/framework/access-control-list.conf.schema.json](../../contracts/conf/framework/access-control-list.conf.schema.json) (action vocabulary),
[contracts/context.schema.json](../../contracts/context.schema.json),
[contracts/report.schema.json](../../contracts/report.schema.json), and
[contracts/log-entry.schema.json](../../contracts/log-entry.schema.json).

| # | Function | What it answers | When |
|---|---|---|---|
| 0 | [`start-session`](#0-start-session) | Which framework-agent session just opened, under which parent session — the registration every later entry's session ids trace back to? | session-started / step-started — every framework-agent session, always first |
| 1 | [`resolve-workflow-instructions`](#1-resolve-workflow-instructions) | Which workflow-context guidance does the orchestrator's session load? | session-started |
| 2 | [`resolve-workflow-skills`](#2-resolve-workflow-skills) | Which skills does the orchestrator's session load? | session-started |
| 3 | [`resolve-step`](#3-resolve-step) | What is the next eligible step of this workflow instance, with its full step resolution — or is there no next step to resolve? | mediated agent invocation — on assent, and after each step's outcome journals |
| 4 | [`resolve-step-model`](#4-resolve-step-model) | Which model profile serves this step's dispatch? | mediated agent invocation — between resolution and dispatch |
| 5 | [`check-step-preconditions`](#5-check-step-preconditions) | May this step start? | step-starting |
| 6 | [`resolve-step-instructions`](#6-resolve-step-instructions) | Which behavioral guidance does this step's session load? | step-started |
| 7 | [`resolve-step-skills`](#7-resolve-step-skills) | Which skills does this step's session load? | step-started |
| 8 | [`check-step-authorization`](#8-check-step-authorization) | Is this write a granted privilege of the acting agent? | write-starting |
| 9 | [`check-step-artifact`](#9-check-step-artifact) | Is the step's written artifact schema-valid? | write-ended |
| 10 | [`check-step-postconditions`](#10-check-step-postconditions) | Did this step deliver? | step-ended |
| 11 | [`end-session`](#11-end-session) | Which session just ended, closing its log with a final entry? | session-ended / step-ended — best-effort, whenever the surrounding mechanism can observe it |

The `When` values are the harness's canonical **boundaries** — abstract lifecycle trigger
points — or the non-hook invocation surface: host event names never appear in the
functional contract (C4); how host events normalize to boundaries is entirely outside this
specification's concern, per [Boundary Normalization](#boundary-normalization).

Function 0 is **session-scoped**: triggered at every session start — orchestrator and step
sessions alike — strictly before any other function of that session; it opens the
session's identity and creates its log. Functions 1–2 **resolve the
orchestrator's workflow context** at its session start — selection and return-handling
instructions, procedure skills — which get injected at session open. Functions 3–4 are
**resolution-scoped**: pure functions over
the instance view of the logs plus validated configuration; they read no artifacts. Functions
5–10 are **step-scoped**, in step lifecycle order — 1 step = 1 agent = 1 session = 1 artifact:
5 gates the dispatch, 6–7 resolve the step's declared context at its session start (injected
there too), 8–9 guard
every write as it lands, 10 evaluates delivery against the state the step left. Function 11 is
**session-scoped at the opposite end**: best-effort, triggered whenever the surrounding
mechanism can observe a session ending — orchestrator and step sessions alike — appending the
session's closing log entry (C8); a session whose end is never observed simply never gets one,
which is not an error state.

The sequence diagram [`harness.sd.puml`](../harness.sd.puml) shows all
twelve functions in play across one workflow instance — framework user, orchestrator agent,
step subagents, host, adapter, and harness core. It lives one level up (`def/`), not under
`def/core/`, because it depicts one concrete host binding's hooks (as a distinct Adapter
participant, separate from the host-agnostic Harness core) realizing the boundaries — see
[`../adapter/vscode-github-copilot-chat/spec.md`](../adapter/vscode-github-copilot-chat/spec.md).

![Harness functions sequence diagram](../harness.sd.png)

### 0. start-session

The session seed: the first function of every session, triggered strictly before any other
function at that session's level. It starts the session's identity — the
`sessionId` and, for a dispatched (subagent) session, its `parentSessionId` — and creates the
session's log file with this start as its first entry: the entry every later entry's
session ids trace back to. The harness stays agnostic about how the session id was sourced;
the surrounding mechanism is responsible for observing or minting it before invocation. The
caller always attaches the session's framework-agent identity as a required `agent` slug — the
harness starts agent sessions: it resolves, checks, and records for the framework's agents.

No agent ever reports its own session identity: the id is always observed or minted by the
surrounding mechanism, never accepted as a raw
agent-authored argument.

**Interface**

- **In** — the session ids: `sessionId` (this session) and `parentSessionId` (the session that
  opened this one — nullable: a dispatching agent session, or `null` for a root session).
  `agent` is **required**: the framework-agent identity — the harness
  starts agent sessions, so every invocation names one.
- **Out** — `SessionStartReport`: the `session` object.
- **Caller usage** — never called by an agent's own action; called by whatever surrounds the
  session's start (a hook mechanism). Later
  mediated agent invocations of the same
  session are attributed to it per the
  session-attribution rule (see [Invocation surfaces](#invocation-surfaces-one-command-system)
  and [Logging](#logging)).

Example:

```json
{
  "in": { "agent": "qa-engineer", "sessionId": "01j9xqr7t3", "parentSessionId": "01j9xq0f2m" },
  "out": {
    "context": {
      "function": "start-session",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": null
    },
    "outcome": { "status": "started" },
    "session": { "agent": "qa-engineer", "sessionId": "01j9xqr7t3", "parentSessionId": "01j9xq0f2m" }
  }
}
```

Contract schemas — [contracts/api/start-session.input.schema.json](../../contracts/api/start-session.input.schema.json) and [contracts/api/start-session.output.schema.json](../../contracts/api/start-session.output.schema.json).

**Preconditions**

- The surrounding mechanism has observed or minted a `sessionId` and, when applicable, a
  `parentSessionId`, and normalized any host-sourced ids to a safe slug — the id becomes a log
  filename (see [Logging](#logging)).
- `agent` resolves to a framework agent identity (C7) — a caller with no genuine
  framework-agent identity to supply simply names no real agent, so the session passes
  through untouched and unstarted: the harness starts agent sessions, nothing else.
  This holds without an
  explicit access-control-list lookup at this function: for a root session (no parent) the
  caller's own scoping already guarantees it (an agent-scoped trigger fires only for a genuine
  framework orchestrator); for a step session (parent present) it is a consequence of the
  step-resolution correlation this function also validates (function 3 only ever resolves
  configured, ACL-validated workflow actors, so a non-framework `agent` simply finds no
  correlation to complete).
- Trigger — the session-started boundary for hook-opened sessions, before
  functions 1–2 (orchestrator session) or 6–7 (step session) run at the
  same boundary, where applicable.

**Postconditions**

- The session log `<workspace>/logs/<sessionId>.log.jsonl` exists; the start entry
  is its first line.
- The start is the session-scope seed: every subsequent entry of this session carries
  the started `sessionId` (and `parentSessionId` where present) in its envelope.

**Invariants**

1. Starting precedes everything: no function logs at a session's level before this one —
   physically enforced, since function 0 creates the very file the others append to.
2. Session ids are observed or minted by the surrounding mechanism — host event payload and
  normalization — never minted
  by the harness, never self-reported by an agent.
3. The parent chain is unbounded: each start records one parent, so any nesting depth
   of subagent dispatches reconstructs by walking starts parent-by-parent — the
   envelope never caps the hierarchy at two levels. Any dispatched subagent may record a
   parent, at any nesting depth.
4. Starting is idempotent per session: re-delivery of the same session-start signal (host
   resume, duplicate hook) appends no second
   registration for an already-registered `sessionId`.

### 1. resolve-workflow-instructions

Which workflow-context guidance does the orchestrator's session load? Deterministic
resolution, at the session-started boundary, of the orchestrator's workflow instructions —
injected into the session at open: one named instruction per orchestrator
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

- **In** — `sessionId` + nullable `parentSessionId`. The orchestrator is resolved from
  the registered session; the instruction set is keyed by that orchestrator.
- **Out** — `WorkflowInstructionsReport`: the instruction refs.
- **Caller usage** — the refs get rendered into the host's session context at open; the
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

Contract schemas — [contracts/api/resolve-workflow-instructions.input.schema.json](../../contracts/api/resolve-workflow-instructions.input.schema.json) and [contracts/api/resolve-workflow-instructions.output.schema.json](../../contracts/api/resolve-workflow-instructions.output.schema.json).

**Preconditions**

- A session opened with no unresolved `resolve-step` entry correlating to it (typically: its
  registration carries no `parentSessionId`) — an orchestrator session
  (a step session loads functions 6–7 instead).
- The session's agent is a framework orchestrator (C7 — foreign sessions pass
  through untouched).
- Trigger — the session-started boundary (hook plane).

**Postconditions**

- The session context contains the orchestrator's instruction refs — nothing more, nothing
  chosen by the agent.
- The invocation appends its own entry to the session's log (what was resolved, for which
  orchestrator).

**Invariants**

1. The instruction set derives from configuration only: one framework-authored instruction
   per orchestrator duty, keyed by orchestrator; the selection instruction names the workflows
   whose `orchestrator` is the session's agent, with their advisory positions and guidance —
   static, no log reads.
2. The instructions are framework-authored refs, one per duty and per harness return kind the
   orchestrator handles; every function-3 return the orchestrator receives — and the workflow
   selection itself — is covered by an injected instruction.
3. Resolution is deterministic and orchestrator-scoped: `sessionId` resolves to the
  orchestrator's registered session, and the configuration decides the refs — never the agent.

### 2. resolve-workflow-skills

Which skills does the orchestrator's session load? The same session-kind correlation and
determinism as function 1, for skills: the orchestrator's procedure skills — its selection skill
and one procedure skill per workflow it facilitates.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The orchestrator is resolved from
  the registered session.
- **Out** — `WorkflowSkillsReport`: the skill ids to load.
- **Caller usage** — the skills get loaded into the session at open; the orchestrator's toolbox
  is its orchestrator role's toolbox, by construction.

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

Contract schemas — [contracts/api/resolve-workflow-skills.input.schema.json](../../contracts/api/resolve-workflow-skills.input.schema.json) and [contracts/api/resolve-workflow-skills.output.schema.json](../../contracts/api/resolve-workflow-skills.output.schema.json).

**Preconditions**

- A session opened with no unresolved `resolve-step` entry correlating to it — an
  orchestrator session.
- Trigger — the session-started boundary (hook plane).

**Postconditions**

- The session loads exactly the orchestrator's declared skill set.
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
        { "kind": "precondition", "slug": "after_build", "step": "build" },
        {
          "kind": "postcondition", "slug": "report_exists",
          "setSelector": {
            "setQuery": "artifacts['review-report'].filter(a, a.slug == artifact)"
          },
          "setPredicate": "selected.size() == 1"
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

Contract schemas — [contracts/api/resolve-step.input.schema.json](../../contracts/api/resolve-step.input.schema.json) and [contracts/api/resolve-step.output.schema.json](../../contracts/api/resolve-step.output.schema.json).

**Preconditions**

- The configuration is validated (fail-fast at load): the workflow exists, its step DAG is
  acyclic, every step routes.
- The user assented to the workflow (per the selection instruction). Instance continuation or
  opening is deduced (In, above) — no id is ever given.
- The attributed `sessionId` resolves to a currently open, registered session — the
  mediated-invocation backstop (see [Invocation surfaces](#invocation-surfaces-one-command-system)).
- Trigger — mediated agent invocation, each time the orchestrator asks "what's
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
2. Eligibility follows the authored step order: the first remaining step whose `kind:
   precondition` `stepCondition` predecessors are all journaled executed. In a validated
   configuration (acyclic step DAG, resolvable `stepCondition` references — enforced at load)
   an instance with a remaining step always has exactly one next eligible step: there is no
   runtime "blocked" state.
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
9. **One workflow instance and one in-flight step per orchestrator session**: an orchestrator
   session drives at most one workflow instance over its whole lifetime (one workflow end =
   one return to the user, which ends the session — see the Session definition), and between
   a step's resolution
   and its journaled outcome it resolves no other step. Running concurrent instances — whether
   of the same workflow or different workflows — is a deliberate non-goal for now. The log still
   carries `workflowInstanceId`, so a later concurrent model can be added without changing the
   identity of past entries.

### 4. resolve-step-model

The model resolution function: which model profile serves this step's dispatch. A
**standalone, mediated agent invocation between resolution and dispatch** — never embedded in
function 3: the two functions are fully independent, and whoever needs both composes them
outside the harness (the orchestrator per its instructions, or whatever mediates the invocation
surface, on one hook).
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
  `{slug, score, costRank, reason}` — a catalog profile, not a host model id: a host-specific
  binding maps it to the host-specific id at dispatch. It always returns a profile
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
    "profile": { "slug": "claude-sonnet-4", "score": 144, "costRank": 2, "reason": "highest weighted capability score" }
  }
}
```

Contract schemas — [contracts/api/resolve-step-model.input.schema.json](../../contracts/api/resolve-step-model.input.schema.json) and [contracts/api/resolve-step-model.output.schema.json](../../contracts/api/resolve-step-model.output.schema.json).

**Preconditions**

- The model catalog and the step's capability map are loaded and validated (fail-fast at load).
- An in-flight step exists in the invoking session: function 3 resolved it and its outcome has
  not journaled yet.
- The attributed `sessionId` resolves to a currently open, registered session — the
  mediated-invocation backstop (see [Invocation surfaces](#invocation-surfaces-one-command-system)).
- Trigger — mediated agent invocation, between resolution and dispatch. The
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
2. **Model catalog → `capabilities` + `costRank` (static)**: `conf/model-profiles.conf.yaml`
   scores every model 0–10 per tag and ranks its relative cost. The catalog owns the canonical
   tag vocabulary.
3. **The score** is a pure weighted capability sum:

   $\text{Score}(m) = \sum_{\text{tag}} \text{capability\_score}_m[\text{tag}] \times \text{step.capabilities}[\text{tag}]$

   Highest score wins; ties break toward lower `costRank`; if both are equal, the
   lexicographically lowest model `id` wins. Cost sensitivity emerges
   structurally: low, sparse weights compress candidate scores into a narrow band where the
   cheap-model tie-break dominates; high weights on discriminating tags let capability dominate
   cost.

   *Worked example* — a step weighting `deep-reasoning: 9`, two models scoring 9 vs 7 on it:
   $A = 81$, $B = 63$ — A wins on capability. At weight 3 the spread narrows ($27$ vs $21$) and,
   across a larger candidate set, the `costRank` tie-break routes cheaper.

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
      { "condition": { "kind": "precondition", "slug": "after_build", "step": "build" }, "outcome": "pass" },
      {
        "condition": {
          "kind": "precondition", "slug": "report_exists",
          "setSelector": {
            "setQuery": "artifacts['review-report'].filter(a, a.slug == artifact)"
          },
          "setPredicate": "selected.size() == 1"
        },
        "outcome": "fail", "failureMessage": "no artifact matches 'review-report'"
      }
    ]
  }
}
```

Contract schemas — [contracts/api/check-step-preconditions.input.schema.json](../../contracts/api/check-step-preconditions.input.schema.json) and [contracts/api/check-step-preconditions.output.schema.json](../../contracts/api/check-step-preconditions.output.schema.json).

**Preconditions**

- A resolved step is in hand, carrying its declared precondition list from the workflow
  configuration.
- Persisted workspace state and the logs are readable.
- Trigger — the step-starting boundary (THE enforcement point: a failing precondition denies
  the dispatch); optionally the orchestrator, probing before dispatch for early feedback
  (the in-flight step is deduced from its session ids/logs) — the probe is advisory,
  the boundary enforces. Both run in the dispatching (orchestrator) session: the step
  session does not exist yet at this boundary.

**Postconditions**

- One log entry records the invocation: the per-condition checks plus the aggregate outcome —
  appended to the dispatching (orchestrator) session's log, the session the step-starting
  boundary belongs to.
- No artifact is touched — the invocation's own log entry is the only write: checking never
  mutates artifacts.

**Invariants**

1. `stepCondition`: for `kind: precondition`, the referenced `step` (the predecessor) must be
  journaled executed in the correlated workflow instance. The instance view always exists once
  a step is in flight, so there is no unevaluable case: a predecessor not journaled executed
  fails the condition — never a silent pass. A `kind: postcondition` `stepCondition` (naming a
  successor) is advisory — a documentation cross-reference mirroring that successor's own
  `kind: precondition` declaration — never itself re-checked as a hard gate: the DAG edge it
  declares is enforced once, from the successor's side.
2. `stateCondition`: `setSelector.setQuery` is a CEL expression that references artifacts
   directly by their schema slug via the `artifacts` runtime constant (a map: artifact slug ->
   matching instances, e.g. `artifacts['review-report']`) to produce `selected`; `setPredicate`
   is then evaluated over `selected`. The step's declared `artifact` ref is in scope as a
   runtime constant. Every `artifacts['<slug>'].<property>` reference is statically validated
   against that slug's artifact schema — an unresolvable slug or an undeclared property is a
   hard error, not a false pass. An empty selected set is a normal value: the predicate decides
   whether it passes or fails.
3. Condition slugs are the audit handle: unique within a step, and every check logs the full
   condition object with its outcome under that slug.

### 6. resolve-step-instructions

Which behavioral guidance does this step's session load? Deterministic resolution of the
step's authored context at the step-started boundary — injected into the session at open:
1 step = 1 agent = 1 session = 1 artifact — the
step's authored constraints
reach the agent with no discretion of its own.

**Interface**

- **In** — `sessionId` + nullable `parentSessionId`. The step whose session opened is
  resolved from those ids by the hook plane (the unresolved-`step-resolution`-entry correlation
  is normalization's job, not the resolver's).
- **Out** — `StepInstructionsReport`: the step's declared instruction refs.
- **Caller usage** — the refs get rendered into the host's session context at open; the agent
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

Contract schemas — [contracts/api/resolve-step-instructions.input.schema.json](../../contracts/api/resolve-step-instructions.input.schema.json) and [contracts/api/resolve-step-instructions.output.schema.json](../../contracts/api/resolve-step-instructions.output.schema.json).

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
- **Caller usage** — the skills get loaded into the session at open; the agent's toolbox is its
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

Contract schemas — [contracts/api/resolve-step-skills.input.schema.json](../../contracts/api/resolve-step-skills.input.schema.json) and [contracts/api/resolve-step-skills.output.schema.json](../../contracts/api/resolve-step-skills.output.schema.json).

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
  (`artifactPath`) + the write `action` (`create`, `read`, `update`, `delete`), derived from
  the host write tool. The actor is derived from the registered session, never
  supplied by the agent; the resource — the artifact's schema slug — is derived from the path
  (invariant 2).
- **Out** — `AuthorizationReport`: allow, or deny with an `authorization.failureMessage`
  naming the missing privilege.
- **Caller usage** — the live verdict is enforced at the write boundary (a denied tool call never
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

Contract schemas — [contracts/api/check-step-authorization.input.schema.json](../../contracts/api/check-step-authorization.input.schema.json) and [contracts/api/check-step-authorization.output.schema.json](../../contracts/api/check-step-authorization.output.schema.json).

**Preconditions**

- The ACL, workspace layout, and the framework's artifact schemas are loaded (path → resource
  resolution needs them), and the host write tool has been mapped to a write `action`.
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
into workspace state (`HEAD`).

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

Contract schemas — [contracts/api/check-step-artifact.input.schema.json](../../contracts/api/check-step-artifact.input.schema.json) and [contracts/api/check-step-artifact.output.schema.json](../../contracts/api/check-step-artifact.output.schema.json).

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
          "kind": "postcondition", "slug": "report_exists",
          "setSelector": {
            "setQuery": "artifacts['review-report'].filter(a, a.slug == artifact)"
          },
          "setPredicate": "selected.size() == 1"
        },
        "outcome": "pass"
      }
    ]
  }
}
```

Contract schemas — [contracts/api/check-step-postconditions.input.schema.json](../../contracts/api/check-step-postconditions.input.schema.json) and [contracts/api/check-step-postconditions.output.schema.json](../../contracts/api/check-step-postconditions.output.schema.json).

**Preconditions**

- The step was dispatched from a correlated unresolved `step-resolution` entry and its execution
  is being evaluated — at the step-ended boundary.
- Trigger — the step-ended boundary (THE evaluation point: the step's session has ended, the
  state it left is final). The invocation runs in the dispatching (orchestrator) session — the
  same session that owns the step's precondition check: at step-ended the step session has
  already closed.

**Postconditions**

- One log entry per step evaluation, carrying the step's outcome — the exact input of
  function 3's cursor — appended to the dispatching (orchestrator) session's log.
- No artifact is touched — the invocation's own log entry is the only write.

**Invariants**

1. `state` assertions evaluate over persisted artifacts only — never agent memory (C2).
2. Postconditions are evaluated ONCE per step pass, at the step-ended boundary — the step's
   session has ended, the state it left is final; the step's own session end adds no second
   evaluation of the same state.
3. The step's outcome logs from this function: its postconditions hold, or they do not.
   This journaled outcome is exactly what function 3's cursor reads — a step whose latest
   outcome passes counts as executed.

### 11. end-session

The session's counterpart to function 0: closes a session's log with a final entry when the
surrounding mechanism can observe that session ending. **Best-effort, not mandatory** — a
session whose end the surrounding mechanism cannot observe (a crash, a force-quit host) simply
never receives this entry; that is not an error state, merely one without this function's
extra protection (C8). Where it does run, it gives the harness core itself an authoritative,
host-agnostic answer to "is this session still open" — computed from the session's own log,
the same persisted state every other function already reads — rather than leaving that fact
to any one host adapter's private bookkeeping.

**Interface**

- **In** — `sessionId`: the session being ended. Nothing else — no `parentSessionId`, no
  `agent`: both are already on record from this session's own start entry, and this
  function's only job is to close the log that entry started.
- **Out** — `SessionEndReport`.
- **Caller usage** — never called by an agent's own action; called by whatever surrounds the
  session's ending, whenever it can observe that ending (a hook mechanism). Idempotent: ending
  an already-ended or never-started session is a no-op, not an error — a duplicate ending
  signal (host re-delivery) changes nothing.

Example:

```json
{
  "in": { "sessionId": "01j9xqr7t3" },
  "out": {
    "context": {
      "function": "end-session",
      "sessionId": "01j9xqr7t3",
      "parentSessionId": "01j9xq0f2m",
      "workflowInstanceId": null
    },
    "outcome": { "status": "ended" }
  }
}
```

Contract schemas — [contracts/api/end-session.input.schema.json](../../contracts/api/end-session.input.schema.json) and [contracts/api/end-session.output.schema.json](../../contracts/api/end-session.output.schema.json).

**Preconditions**

- The surrounding mechanism has observed that the session ended — never inferred by the
  harness itself, which has no notion of a session's execution independent of its log.
- Trigger — the session-ended boundary, whenever the surrounding mechanism can observe it;
  best-effort, not guaranteed to fire for every session (above).

**Postconditions**

- The session's log carries an ending entry as its last line, when the session was open.
- Ending is idempotent: an already-ended or never-started `sessionId` produces the same
  `ended` outcome and no additional entry.
- **C8 takes effect from this entry on**: every session-bound function's precondition
  includes checking that its target session's log carries no ending entry; a call against an
  ended session is refused, regardless of what any adapter's own bookkeeping believes is
  "current" (see [General invariants](#general-invariants)).

**Invariants**

1. Ending is the mirror of starting (function 0, invariant 1): where starting precedes
   everything, ending follows everything — no further entry is ever appended to an ended
   session's log (enforced by C8 at every other function, not by this one).
2. Idempotent per session: re-delivery of the same session-end signal, or an ending call
   against a session already ended, appends no second ending entry and is never an error.
3. Best-effort by design: this function's absence from a session's log is not a fault
   condition anywhere else in this contract — no other function's precondition requires an
   ending entry to exist, only that one does not exist for OTHER, later invocations to be
   refused.
4. Ending asserts nothing about the session's workflow instance: a workflow instance's
   openness is computed structurally from its own steps' journaled outcomes (function 3,
   invariant 1), entirely independent of whether the session that resolved it has since
   ended.

### Internal validation — not functions

Configuration validity and workspace validity are harness concerns — but not harness
functions: nothing exposes them as commands, no boundary triggers them, and no log entry
carries a dedicated check report. They are enforced where they structurally belong:

**Configuration validity — fail-fast at every instantiation.** The configuration plane is the
enforcement point (see [Configuration plane](#configuration-plane)): `ConfigLoader` parses and
contract-validates every source as ONE act — an unvalidated parse never escapes — and applies
the semantic rules JSON Schema cannot express (non-empty steps, unique step slugs, resolvable
`stepCondition` references, unique condition slugs per step, acyclic step DAGs, at least one
positive capability weight per step, an acyclic advisory workflow graph), the
cross-configuration coherence rules (workflow actors exist in the ACL, capability tags belong
to the model catalog's vocabulary, step `artifact` slugs resolve to artifact schemas,
instruction/skill refs resolve to files in the framework layout), and the layout environment
(every required variable present and pointing to an existing directory).
`Application` builds every configuration dataclass at
instantiation, so **every invocation of any function fails fast — with the full reports, as
domain exceptions at the trigger plane — before any function runs** on an invalid
configuration. There is no separate check to invoke and therefore nothing that can diverge
from what the functions actually trust. The embedding application's CI gates its framework
repository by exercising exactly this: any harness invocation (e.g. its test suite) is the
configuration check.

**Workspace validity — enforced at the write boundary, detected by construction.** C6's
enforcement is functions 8–9 (the commit gate); its detection face is the readers:
`discover()` raises rather than yield a schema-invalid artifact, so any consumer of committed
state fails loudly on foreign corruption. The whole-workspace sweep — every artifact against
its matched schema, the cross-artifact rules a single write cannot see (scope/frontmatter
coherence, parent linkage resolution, blocking open items), the workspace structure against
the workspace layout — is an **internal store capability** (`scan_raw_paths` +
`validate_artifact` over the raw universe of committed state), not a function: it journals
nothing, never mutates, and reports findings to whoever runs it. The embedding application
uses it for C6's canonical-branch guarantee — the required CI status check on the workspace
repository (see C6, guarantee scope) — and may ship it as an advisory pre-commit hook;
remediation always goes through agents.

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
artifact schemas, skills, and templates. Host-specific event mapping is entirely someone else's
concern, outside the harness's own boundary. The
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
  committed state** is detect-and-remediate (the internal workspace sweep detects, readers
  fail loudly —
  `discover()` raises — and remediation goes through agents); the **canonical branch** is
  guaranteed — it is protected (no direct pushes) and every change merges only through a
  required, unskippable CI status check running the internal workspace sweep clean (see
  [Internal validation](#internal-validation--not-functions)), so nothing
  invalid ever becomes shared state. A framework-shipped advisory pre-commit hook (running
  the same sweep) narrows the local window; it is ergonomics, never the guarantee.

#### C7 - Framework-agent scope

The harness governs the framework's agents, not the host. A hook event is processed only when
its agent resolves to a framework agent (a normalized identity the ACL declares); events from
any other host agent or session pass through untouched and unlogged.

#### C8 - Session ending

A session whose log carries an ending entry (function 11) accepts no further invocation:
every session-bound function's precondition includes checking that its target session's log
carries no such entry, and refuses the call if it does. This holds regardless of what any
adapter's own bookkeeping believes is "current" — the check reads the target session's own
log, the harness's own persisted state (C0), never an adapter-side record. Ending is
best-effort (function 11, invariant 3): a session that never receives an ending entry is
simply never refused on that basis — this invariant only ever narrows what is accepted, never
requires ending to have happened.

---

## Design

### Invocation surfaces, one command system

Every harness function is exposed as a harness command: the Python executable function entry
point. It accepts one input object (session attribution fields + function-specific fields) and
returns the function's Report as `out`. Every invocation is session-bound — there is no
session-less surface, and every session is an `agent`-origin session: the harness opens,
and journals for, agents — nothing else. The command system is entered through two invocation
surfaces:

- **Hook invocation** — a host event triggers a harness command. Whatever normalizes the host
  event builds the session attribution fields from the event payload and its own private
  bookkeeping — never the harness's stores or config; this surface triggers functions 0–2 and
  5–10 at their natural boundaries.
- **Mediated agent invocation** — an agent asks through a trusted surface into a harness
  command, within an already-opened session. This surface is available only where that
  surface can attach host-observed session attribution outside model-authored arguments. It
  may serve functions 3–4. A host that cannot prove such attribution MUST NOT expose this
  surface for session-bound functions.

**Session attribution.** Every journaling invocation carries the `sessionId` of the session it
runs in, and the id always comes from the surrounding mechanism — never self-reported by an
agent: hook-triggered functions read it from their own event payload; mediated agent
invocations are attributed by whatever mediates them, but only where that mechanism has a
verifiable host-observed attribution channel. There is no invocation without a session, and no
journaling or step-scoped function refuses a correctly-attributed session.

**Mediated attribution rule.** A trusted mediated-invocation surface is valid only when it can
populate `sessionId` and `parentSessionId` from a host-observed source that the model
did not author. Acceptable mechanisms include a per-session process environment or a
host-native command/tool surface that passes session identity outside the tool arguments
visible to the agent. A tool-boundary stamp that merely rewrites a model-authored command
argument is not sufficient. If a host lacks such a mechanism, functions 3–4 are not exposed
as mediated commands for that host; whatever specifies that host's binding must state that
limitation.

**Narrow tool-boundary-stamp exception.** A host binding MAY still expose functions 3–4
through a tool-boundary stamp — otherwise insufficient per the rule above — ONLY when ALL of
the following hold, each stated explicitly in that host's binding:

1. The stamped invocation's shape is **fully framework-authored and structurally
   recognized** (a fixed command pattern the framework's own instructions dictate verbatim),
   never a loosely-matched heuristic over open-ended agent phrasing.
2. Any invocation already carrying model-authored attribution is **denied outright**, never
   merely overwritten.
3. The [mediated-invocation backstop](#invocation-surfaces-one-command-system) below is in
   force, so an unresolvable or unregistered id fails closed at the harness itself regardless
   of the stamp's own reliability.
4. The binding names this an **explicit, interim exception** — not a permanent design — and
   tracks the structural fix that would remove the need for it (a host-native surface that
   passes attribution outside model-visible arguments, per the rule above, once one exists
   for that host).

Without all four, the rule above applies without exception: the host MUST NOT expose
functions 3–4 as mediated commands.

**Mediated-invocation backstop.** Independent of whatever attribution mechanism a host
binding uses — sufficient, exception-qualified, or neither — every mediated invocation's
`sessionId` is validated
against the harness's own session registry before functions 3–4 proceed: an id that does not
resolve to a currently open, registered session (C7/C8) is rejected outright, never silently
trusted and never defaulted to some other session. This is a backstop, not a substitute for
the rule above: it bounds the damage of a weaker-than-required host mechanism (a
misattributed or model-guessed id fails closed at the harness itself, visibly, rather than
silently corrupting a foreign session's log) but does not by itself make that mechanism
sufficient — a host binding relying on the exception above still MUST state it explicitly.

There is no hook logic inside the harness core: the **harness core** (`src/` + the function
commands) is hook- and host-blind by package graph — it exposes exactly one command per
function and nothing else, no hook command — so agents invoke the
same twelve pure commands directly. Whatever normalizes host events into boundaries and
session attribution, and however many hosts it serves, is entirely outside this
specification's concern: the harness core's only obligation is to expose its twelve commands
under their JSON contracts and trust nothing but what those contracts carry — this document
never needs to reference how, or by what, a given host is bound.

### Boundary Normalization

Host events are normalized to harness boundaries before any policy is applied, by whatever
mechanism embeds the harness for a given host. Concretely, that mechanism's one irreducible
job is session identification: using whatever host-specific signals it alone has access to,
decide which agent session a given host event or mediated call belongs to. The harness core
never performs this resolution and never sees the host-specific data behind it — only the
resolved `sessionId` / `parentSessionId` it carries (C4). This specification names the
boundaries only and requires nothing further of that mechanism beyond correct session
attribution (above). Dispatch tools carry the **step boundary** — the subagent session IS the
step, so step pre- and postconditions apply before and after it — while write tools carry the
**write boundary** (authorization and schema validity, never step conditions).

| Boundary | Structural evidence | Functions |
| --- | --- | --- |
| session-started | Session-open event with no correlated unresolved `step-resolution` entry | 0 (opening — always first) + 1, 2 |
| step-starting | Dispatch about to open a step session | 5 (preconditions — THE enforcement point) |
| step-started | Session-open event correlated to an unresolved `step-resolution` entry | 0 (opening — always first) + 6, 7 |
| write-starting | Write tool about to mutate an artifact path | 8 (authorization + staging baseline — can deny) |
| write-ended | Write tool has landed a staged write on an artifact path | 9 (schema validity — the commit gate) |
| step-ended | Dispatch returned after the step session ended | 10 (postconditions — THE evaluation point) |
| session-ended | Session-end event, whenever the surrounding mechanism can observe it | 11 (closing — best-effort, C8) |

This keeps the harness env-agnostic: normalizing host-specific event names and tool payloads,
and deciding which tools are dispatches and which are writes, is entirely someone else's job.
The step-starting and step-ended boundaries occur in the dispatching (orchestrator) session —
their entries journal to its log; the step session's own log carries its registration (0), its
context resolutions (6–7), and its write-boundary entries (8–9).
Observational host events that do not enter one of these boundaries are outside this
specification's concern, and they are not written to the harness journal.

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
    session_lifecycle/ # SessionLifecycle + its results: SessionStartReport (0), SessionEndReport (11)
    step_resolution/  # StepResolver + its result: StepResolution
    model_resolution/ # StepModelResolver + its result: ModelProfileReport
    checking/         # the checkers + ConditionEvaluator + their result reports and ConditionCheck
    context_resolution/ # the four context resolvers + their instruction/skill report classes
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
- **Contract identities follow the GSM `gsmarc://` `$id` convention.** Every schema declares a
  canonical `$id` of the form `gsmarc://saf/<path>/<stem>/v1`, where `<path>` mirrors the file's
  location under `contracts/` or `artifacts/` and `<stem>` is the filename without
  `.schema.json`. This aligns SAFE with GSM/ITIP/SIE: scheme `gsmarc://`, product segment `safe`,
  logical path, filename stem as the identity segment, and `/v1` version suffix. `$ref` links
  use these canonical ids (or repo-relative paths where the loader resolves them), so a
  contract can be moved or renamed only by updating its `$id` and every reference to it.
- **Canonical slug convention, centralized.** Every framework entity named by a slug uses the
  same URL-safe form: lowercase letters, digits, and hyphens (`[a-z0-9-]+`). Every `*Slug`
  definition lives in ONE shared library,
  [contracts/slugs.schema.json](../../contracts/slugs.schema.json) — no contract
  defines its own copy; every reference is a `$ref` to it, in-repo (from within the library
  itself) or cross-file via `gsmarc://saf/contracts/slugs/v1#/definitions/<slug>`. The
  slug is also the filename stem used to locate the entity's definition:
  - `workflowSlug` = stem of `conf/workflows/<slug>.workflow.conf.yaml`,
  - `stepSlug` = a step slug, unique within its workflow,
  - `conditionSlug` = a condition slug, unique within its step,
  - `agentSlug` = `name` frontmatter value and stem of `agents/<slug>.agent.md`,
  - `artifactSlug` = stem of `artifacts/<slug>.artifact.schema.json`,
  - `roleSlug` = stem of the role block inside `conf/access-control-list.conf.yaml`,
  - `skillSlug` = stem of `skills/<slug>.skill.md` under `FRAMEWORK_SKILLS_DIR`,
  - `instructionSlug` = stem of `instructions/<slug>.instructions.md` under `FRAMEWORK_INSTRUCTIONS_DIR`,
  - `templateSlug` = stem of `templates/<slug>.artifact-template.md` under
    `FRAMEWORK_TEMPLATES_DIR` — admits `/`-separated path segments in the slug itself
    (`^[a-z0-9-]+(/[a-z0-9-]+)*$`), because templates are organized per layer/actor, not in a
    flat directory,
  - `nodeSlug` = a workspace node's own path segment (`conf/workspace.conf.yaml`) — may embed
    `<name>` VARIABLE placeholders, optionally mixed with literal text.
  The harness resolves a referenced entity by joining its canonical directory, the slug,
  and the fixed extension — never by scanning arbitrary files.
  The directories themselves are declared by the framework in `.env`
  (`FRAMEWORK_AGENTS_DIR`, `FRAMEWORK_ARTIFACTS_DIR`, `FRAMEWORK_SKILLS_DIR`,
  `FRAMEWORK_TEMPLATES_DIR`, `FRAMEWORK_WORKFLOWS_DIR`, `FRAMEWORK_INSTRUCTIONS_DIR`),
  all relative to `FRAMEWORK_DIR`.
- **A contract is loaded by the layer that owns the boundary it guards.** The
  `*.conf.schema.json` contracts guard the configuration boundary, so `config/` loads them —
  parsing and validation are one act there. The data contracts (the artifact base contract, the
  log-entry contract, and the framework's artifact schemas) guard the workspace read/write
  boundary, so `stores/` loads them (`artifact_store/` the artifact schemas, `session_log_store/`
  the log-entry contract) — that is where valid-by-construction (C6) is enforced. Services never
  touch a raw schema: they receive typed configuration views and valid-by-construction entities.
- **Shared mechanics live in `utils/`.** `config/` and `stores/` both parse files and
  validate instances against JSON Schema contracts, so the mechanical primitives are one shared
  package: the `.env` loader, the safe YAML loader, the JSON / JSONL readers, and the schema
  validator (contract compilation, `$ref` registry, instance validation returning raw
  reports). `utils/` is
  strictly domain-free — it knows no artifact, workflow, or ACL, depends on nothing internal,
  and returns plain data; each caller layer turns raw reports into its own typed views or
  entities. Anything with domain meaning belongs to its owning layer, never to `utils/`.
- **Host-aware code never enters `src/`.** Event classification, session identification,
  boundary orchestration, and decision rendering are entirely outside this specification's
  concern — whatever embeds the harness for a given host owns that code, and depends on
  exactly one thing in the core: the twelve function commands, never `services`, `stores`, or
  `config`. `src/` is host-blind structurally: nothing in it needs to know that such a thing
  exists, let alone how it is built.

### Classes

The class diagram [`harness-src-classes.puml`](harness-src-classes.puml) is the
prescriptive class model of `src/` — **the harness core only**: **1 folder =
1 module = 1 package** in the diagram. Classes
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
dependency direction, as in the source layout: `commands → services →
{stores, config}`, with `utils` beneath `stores` and `config`.

![Harness source classes diagram](harness-src-classes.png)

- **`application` (src root)** — `Application` is the composition root, a single module at the
  root of `src/`, above every package: it builds every configuration dataclass through
  `ConfigLoader` (fail-fast) and wires the object graph, then dispatches `argv` to one command
  (`dispatch_command`).
- **`commands`** — `Command` is the single interface (`execute_function`), realized by
  **twelve commands — exactly one per harness function** — each holding its
  service(s) as private attributes: parse the function's `in` object, invoke, render the typed
  result to the `out` object. No command composes services: functions 3 and 4 are fully
  independent, and whoever needs both composes them outside the harness core (the
  orchestrator per its instructions, or whatever mediates a given host's hook events). Commands
  are hook-
  and host-blind: any event→boundary→functions orchestration happens entirely outside `src/`,
  never inside a command.
- **`services`** — all harness logic, one service per function, grouped in **subpackages
  by family**, and each result dataclass homed in the subpackage of the classes returning it —
  **exclusively with them**:
  - `session_lifecycle/` — `SessionLifecycle` (0 → `SessionStartReport`; 11 →
    `SessionEndReport`);
  - `context_resolution/` — the four context resolvers (1–2, 6–7) with their report classes:
    `WorkflowInstructionsReport`, `WorkflowSkillsReport`, `StepInstructionsReport`, and
    `StepSkillsReport`;
  - `step_resolution/` — `StepResolver` (3 → `StepResolution`);
  - `model_resolution/` — `StepModelResolver` (4 → `ModelProfileReport`);
  - `checking/` — `StepPreconditionChecker` (5), `StepPostconditionChecker` (10),
    `StepAuthorizationChecker` (8 — live only: `check_step_authorization(actor,
    artifact_path)`), `StepArtifactChecker` (9), plus `ConditionEvaluator` (the CEL
    machinery functions 5 and 10 share, via cel-python) — with their results `CheckReport`
    (outcome + findings + condition checks) and `ConditionCheck`. The internal workspace
    sweep is not a checker service: it is store capability (`ArtifactStore.scan_raw_paths` +
    `validate_artifact`), commandless by design (see
    [Internal validation](#internal-validation--not-functions)).

  **Report identity rule:** every service returns a concrete `Report` subtype; every command
  returns that same report object as its `out`; and the log entry stores that exact object
  under `report`. `outcome` and `context` live on the `Report` base (homed in
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
  `WorkspaceLayout`/`ArtifactNode`/`FolderNode`, `WorkflowCatalog`/`Workflow`/`Step`/`StepCondition`/`StateCondition`.
  Each `load_*` method performs parse + contract-validation + semantic rules
  - dataclass construction as ONE act; there is **no aggregate `FrameworkConfig`**: each
  service receives exactly the dataclasses it needs.
- **`utils`** — the domain-free mechanics, in two layers. LOADERS — one per format:
  `EnvLoader`, `JsonLoader`, `YamlLoader` (PyYAML), `MarkdownLoader` (frontmatter + body).
  HIGHER-LEVEL utilities abstracting the loaders: `SchemaValidator` (jsonschema; loads
  contracts through `JsonLoader`) and `JsonlStore` (the log file: `load_entries` /
  `append_entry` — a store, not a reader, because it persists too). Plain data
  in, plain
  data out.

### Configuration plane

Every configuration source has a contract and a configuration dataclass (in `src/config/`,
beside the `ConfigLoader` that builds it):

| Configuration | Contract | Typed view |
|---|---|---|
| `.env` layout environment | required-variable set (below) | `FrameworkLayout` |
| `conf/access-control-list.conf.yaml` | `conf/framework/access-control-list.conf.schema.json` | `AccessControlList` |
| `conf/model-profiles.conf.yaml` | `conf/framework/model-profiles.conf.schema.json` | `ModelProfiles` |
| `conf/workspace.conf.yaml` | `conf/framework/workspace.conf.schema.json` | `WorkspaceLayout` |
| `conf/workflows/*.workflow.conf.yaml` | `conf/framework/workflow.conf.schema.json` | `WorkflowCatalog` / `Workflow` / `Step` |

The framework's **layout is environment, not file configuration**: the framework declares WHERE
its pieces live via environment variables, loaded from a `.env` file at the framework root
(process environment takes precedence — CI and containers override without editing files).
`FRAMEWORK_DIR` anchors the layout: it is the one ABSOLUTE path — defaulting to the directory
containing the `.env` file — and every other layout variable resolves relative to it:

```bash
FRAMEWORK_DIR=/abs/path/to/saf-agentic-organization   # the anchor: absolute; all other paths resolve relative to it
FRAMEWORK_AGENTS_DIR=agents
FRAMEWORK_ARTIFACTS_DIR=artifacts
FRAMEWORK_SKILLS_DIR=.github/skills
FRAMEWORK_TEMPLATES_DIR=templates
FRAMEWORK_WORKFLOWS_DIR=conf/workflows
FRAMEWORK_INSTRUCTIONS_DIR=instructions
FRAMEWORK_WORKSPACE_DIR=../saf-agentic-workspace
```

`FrameworkLayout` is the dataclass over these variables — validated fail-fast like any other
configuration source (every variable present, every path existing — see
[Internal validation](#internal-validation--not-functions)).
WHAT the framework declares (grants, catalogs, workflows) stays in `conf/` files under contract
schemas.

Two planes, never conflated: the **framework** is the application embedding the harness; the
**workspace** is the data plane the harness checks. The harness's own contracts are
harness-owned and resolved structurally from the harness code, never declared in `conf/`.

Parsing and validation are one act: `ConfigLoader` parses the source and validates it against
the contract in the same step — an unvalidated parse never escapes the config package. Loading a
workflow configuration additionally enforces the semantic rules JSON Schema cannot express
(non-empty steps, unique step slugs, resolvable `stepCondition` `step` references, unique
condition slugs within a step, an acyclic step DAG, at least one positive capability weight per
step); the workflow catalog additionally validates the advisory workflow graph (references
resolve, acyclic).
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
(a foreign commit can land invalid bytes; the internal workspace sweep detects, readers fail
loudly, agents
remediate); **the canonical branch** — guaranteed: protected, no direct pushes, every change
merging only through a required, unskippable CI status check running the internal workspace
sweep clean (see [Internal validation](#internal-validation--not-functions)). The gate
constrains **validity, not authorship** — anyone may author artifacts; nothing
invalid becomes shared state. A framework-shipped advisory pre-commit hook (running the same
sweep locally) narrows the local window — ergonomics, never the guarantee.

**4. Logs ride outside the gate.** Logs are workspace state (C0) but harness-authored,
append-only, and single-writer: they have no invalid-write window, so they are exempt from the
commit gate and read directly from the working tree. The commit gate governs exactly the
agent-authored plane — artifacts — and nothing else.

### Logging

One append-only JSONL file per session:
`<workspace>/logs/<sessionId>.log.jsonl`

**1 session = 1 writer = 1 log file.** `start-session` (function 0) creates the file —
its opening entry is the first line — so nothing logs at a session's level before
opening, and no two writers ever contend for one file: appends are single-writer by
construction (no locking). `end-session` (function 11) appends the ending entry as the
last line when the surrounding mechanism can observe the session ending (C8) — but a log may
also simply stop growing without one, best-effort not being a guarantee; either way growth
partitions by session lifecycle and no rotation policy is needed.

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

Contract schema — [contracts/log-entry.schema.json](../../contracts/log-entry.schema.json).

**The status rule.** The only status is the function's outcome, and each function defines
and owns it in its own I/O contract, inside its report — under ONE shared field name,
`outcome`, so every report carries one, even where a single value is possible: 0
`started`; 1–2, 4, and 6–7 `resolved`; 3 `step-resolution`/`no-next-step`;
5 and 10 `pass`/`fail`; 8 `allowed`/`denied`; 9 `valid`/`reverted`; 11 `ended`; all functions
may also
return `input-error`, `state-error`,
`configuration-error`, `invocation-error`, or `system-error`. Error outcomes are ordinary outcomes
in the same report envelope; they carry the `error` detail object — required on error statuses,
forbidden on success statuses — rather than using a
separate error-report type. One field name for uniform handling, function-owned values — no
envelope status field, no global status enum. Two consequences:

- **Every other status is derived.** No step, session, instance, or workspace owns a stored
  status: any lifecycle, for any view, is reconstructed from the entry sequences —
  `function` plus the report's outcome. A step is *executed* when its latest function-10
  entry reports `outcome: pass` (function 3's cursor); the workspace is *valid* when the
  internal workspace sweep runs clean now. Verdicts are journaled, entity states are
  recomputed — the
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
- **Sanitization (a caller obligation)** — `sessionId` becomes a log filename: whatever
  surrounds an agent session's opening
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
- **O**pen/closed — behavior extends through configuration (workflows, catalog, ACL, model
  catalog), never by modifying the harness core.
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

`tests/unit/` is a structural mirror of `src/`: one test module per src module
(`tests/unit/<package>/test_<module>.py` ↔ `src/<package>/<module>.py`), one test class per src
class. Isolation comes from the constructor-injection convention — collaborators are replaced
by fakes and tmp-dir workspaces, never by monkey-patching internals.

### Functional testing (proposal)

`tests/functional/` — one test module per harness FUNCTION (twelve), exercising the real
command entry point over a fixture framework configuration and a fixture workspace:

- each test asserts the full Interface (In → Out), the Postconditions (exact log entries
  appended; workspace untouched — or the staged write committed / discarded for function 9),
  and the invariants observable
  from outside;
- log replay tests: re-running against a golden fixture set of session logs must be
  byte-stable per log and merge-stable per instance view — the determinism check;
- CI runs both suites on every push; the workspace and framework repositories are
  additionally gated by the internal validation (the workspace sweep as the workspace repo's
  required status check; any harness invocation's fail-fast configuration load as the
  framework repo's — see [Internal validation](#internal-validation--not-functions)).
