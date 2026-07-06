# Agentic Harness

The harness is the deterministic execution core of an agentic framework. It owns:

- resolution of step, workflow, and model,
- checking of step's conditions, authorization, and artifacts,
- injection of agent's instructions, skills, and prompts,
- logging of all that.

Agents stay limited to the irreducible work: generating content, judging within a step, and actuating host tools.

The harness does not orchestrate: the **orchestrator agent** orchestrates (converses with the
user, starts workflows on assent, dispatches subagent's steps). The harness **resolves, checks, injects, and records** — deterministically, from persisted state and validated configuration only.

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
  workflow, workflow instance, step.
- [General invariants](#general-invariants) — C0–C7: workspace state definition, assertion
  scope, agnosticism, schema binding, workspace validity, framework-agent scope.
- [The harness functions](#the-harness-functions) — the functional contract: thirteen functions,
  each specified by its interface (with JSON I/O contract), pre/postconditions, and invariants.
  - Resolution (1–2): [`resolve-step`](#1-resolve-step) ·
    [`resolve-step-model`](#2-resolve-step-model)
  - Step (3–6): [`check-step-preconditions`](#3-check-step-preconditions) ·
    [`check-step-postconditions`](#4-check-step-postconditions) ·
    [`check-step-authorization`](#5-check-step-authorization) ·
    [`check-step-artifacts`](#6-check-step-artifacts)
  - Injection (7–10): [`inject-workflow-instructions`](#7-inject-workflow-instructions) ·
    [`inject-workflow-skills`](#8-inject-workflow-skills) ·
    [`inject-step-instructions`](#9-inject-step-instructions) ·
    [`inject-step-skills`](#10-inject-step-skills)
  - Audit (11): [`audit-workflow-instance`](#11-audit-workflow-instance)
  - Global checks (12–13): [`check-workspace`](#12-check-workspace) ·
    [`check-configuration`](#13-check-configuration)
- [Design](#design) — trigger planes, hook event normalization, source layout, class design,
  hook adapter layout, configuration plane, logging.
- [Implementation](#implementation) — Python conventions, SOLID, TDD, unit and functional
  testing, installation, and the validation surface.

## Terminology

- **Framework** — the agentic framework application embedding the harness (this repo's SAFe methodology). Its
  layout is declared in environment variables loaded from a `.env` file at the framework root
  (see the [configuration plane](#configuration-plane)).
- **Workspace** — the writable data plane the harness checks, constituted exclusively of
  **artifacts** and **logs** — one workspace, two authors, no overlap: agents author artifacts,
  the harness authors logs.
- **Artifact** — the framework's content: every business, governance, or orchestration
  deliverable persisted under the workspace state paths. Schema-bound (C5),
  valid-by-construction once persisted (C6), and the sole basis for every check the harness
  performs (C1–C2). Artifacts — status transitions, human decisions, and authored deliverables
  included — are produced by dispatched agents (or the user) and then checked by the harness,
  which never authors artifact content: the one deliberate exception is the corrective revert
  at the write boundary, which restores or deletes, never writes new content. Artifacts attach
  to **steps**, never to workflows: each step delivers exactly one artifact — its `artifact`
  field names it by schema slug (the artifact kind) or URI (a specific instance) — and a
  workflow's deliverables are simply the union of its steps' artifacts (in this framework:
  epics, features, stories, …).
- **Log** — the harness's record: the append-only JSONL file of one workflow instance (the
  *instance journal*). Every function invocation appends one entry, and the journal contract
  schema-binds every entry. Logs are persisted workspace state (C0) — a condition may read log
  evidence and assert it as state (C5) — but they are not artifacts: agents never write logs,
  and no step produces one.
- **Workflow** — a configuration entity (`conf/workflows/*.workflow.conf.yaml`): an atomic,
  artifact-delivering unit of the methodology, made of steps. Each workflow declares its
  **facilitator** — the orchestrator agent that drives its instances; the facilitator's
  catalog map is injected at its session open (function 7).
- **Workflow instance** — one run of one workflow, the way an object is an instance of a
  class: the workflow configuration is the definition; the instance is one dated execution of
  it, journaled in its own log with its own step cursor. An instance carries no input of its
  own — it is identified by its minted id and recognized by its content: the artifacts its
  steps have journaled delivered. A workflow delivers MANY artifacts — one per step — so no
  single artifact is attached to the instance; artifacts attach to steps.
- **Step** — one agent turn. **1 step = 1 agent = 1 session = 1 artifact**: the session
  lifecycle IS the step lifecycle, and each step delivers exactly one artifact — its
  `artifact` declares it.

## General invariants

### C0 - Workspace state definition

Workspace state is the union of:

- Persisted **artifacts** (the framework's content under the workspace state paths).
- Persisted **logs** (the harness's instance journals under the workspace logs paths).

Both are first-class state for deterministic checks and replay.

### C1 - Workspace-state scope

All preconditions and postconditions are evaluated strictly against persisted workspace state.

### C2 - Harness assertion boundary

Condition checks executed by the harness only assert workspace state. The harness does not
assert private agent memory or transient, non-persisted host context.

### C3 - Agent sourcing freedom

The actor agent may source from workspace data, external systems, tools, or web context. Harness
pass or fail is based only on persisted workspace state.

### C4 - Methodology and host agnosticism

The harness is generic: it is not hard-coded to any methodology, host environment, or artifact
taxonomy. Methodology-specific semantics come from the embedding framework's workflow configs,
artifact schemas, skills, and templates. Host-specific event mapping comes from the adapter. The
harness only interprets the generic primitives it is given: workflow graphs, conditions,
artifact schemas, ACL grants, and the model catalog.

### C5 - Schema-bound persistence

Every artifact is cataloged and schema-bound; every log entry is bound to the journal contract.
The selector model has one selector type: selecting persisted artifacts. Where a condition
depends on log evidence, that evidence is read from the instance logs (part of workspace state)
and asserted as state. Artifacts are the framework's content; logs are the harness's record —
one workspace, two authors, no overlap.

### C6 - Workspace validity

The workspace contains exclusively schema-valid artifacts and valid state, at all times.

- Maintained at the write boundary: every agent write is validated and, if
  invalid, reverted and denied with the schema reports so the agent retries. This revert is
  the harness's single deliberate write.
- Relied on by every reader: the artifact repository is valid-by-construction (`discover()`
  raises rather than yield a schema-invalid artifact). Validators that must enumerate invalids
  read the raw universe (`scan_raw`) instead.
- Enables safe asynchronous / remote workspace sync: a synced replica is trustably valid.

### C7 - Framework-agent scope

The harness governs the framework's agents, not the host. A hook event is processed only when
its actor resolves to a framework agent (a normalized identity the ACL declares); events from
any other host agent or session pass through untouched and unlogged.

---

# The harness functions

This is the functional contract: everything the harness does is exactly one of these thirteen
functions, every log entry names the function that produced it, and every command or hook is
an entry point into one of them. Each function's interface is a JSON `in`/`out` object pair;
the normative schemas live at `harness/contracts/functions/<function>.io.schema.json` — the
snips in each section reproduce them.

| # | Function | What it answers |
|---|---|---|
| 1 | [`resolve-step`](#1-resolve-step) | What is the next eligible step of this workflow instance, with its full step resolution — or is there no next step to resolve? |
| 2 | [`resolve-step-model`](#2-resolve-step-model) | Which model serves this step's dispatch? |
| 3 | [`check-step-preconditions`](#3-check-step-preconditions) | May this step start? |
| 4 | [`check-step-postconditions`](#4-check-step-postconditions) | Did this step deliver? |
| 5 | [`check-step-authorization`](#5-check-step-authorization) | Is this write a granted privilege of the acting agent? |
| 6 | [`check-step-artifacts`](#6-check-step-artifacts) | Are the step's written artifacts schema-valid? |
| 7 | [`inject-workflow-instructions`](#7-inject-workflow-instructions) | Which workflow-context guidance does the orchestrator's session load? |
| 8 | [`inject-workflow-skills`](#8-inject-workflow-skills) | Which skills does the orchestrator's session load? |
| 9 | [`inject-step-instructions`](#9-inject-step-instructions) | Which behavioral guidance does this step's session load? |
| 10 | [`inject-step-skills`](#10-inject-step-skills) | Which skills does this step's session load? |
| 11 | [`audit-workflow-instance`](#11-audit-workflow-instance) | Were all of this instance's recorded writes granted — verified after the fact? |
| 12 | [`check-workspace`](#12-check-workspace) | Is the workspace, as a whole, in a valid state right now? |
| 13 | [`check-configuration`](#13-check-configuration) | Is the embedding application's configuration a valid harness input? |

Functions 1–2 are **resolution-scoped**: pure functions over the instance journal plus validated
configuration; they read no artifacts. Functions 3–6 are **step-scoped**: 1 step = 1 agent = 1
session = 1 artifact, so they attach to the step's lifecycle — act (5, 6 per write) → close (4
evaluated
against the state the step left). Functions 7–10 **inject authored context at session start**,
split by session kind: a session with no open dispatch is the orchestrator's and loads the
workflow context (7–8 — catalog map, return-handling instructions, procedure skills); a
session correlating to an open dispatch is a step session and loads exactly its step's
declared context (9–10). Function 11 is the **instance-scoped audit**: a retrospective sweep
over one instance journal, agent- or CI-triggered. Functions 12–13 are **globally-scoped**
checks: instance-less
sweeps over the whole workspace or the whole configuration — no step, no session — triggered
by CI pipelines or by an agent on demand.

The sequence diagram [`def/harness-functions.puml`](def/harness-functions.puml) shows all
thirteen functions in play across one workflow instance — framework user, orchestrator agent,
step subagents, host, and harness.

## 1. resolve-step

The resolution core and the orchestrator's main loop. The orchestrator agent drives a workflow
instance by calling this function repeatedly — once after starting the instance on user assent,
then once after each step's outcome journals — and relaying each result verbatim into the host.
The harness alone governs workflow/step sequencing: no agent selects steps, and there is no
`previous` direction — a failed or reopened step is simply not journaled executed, so plain
forward resolution returns it again. Retry is re-resolution, not selection.

**Interface**

- **In** — the workflow slug (`workflowSlug`); the workflow-instance id (`workflowInstanceId`)
  — **omitted to open a new instance**: the harness
  mints the workflow-instance id, creates its log, and returns the id in the context (there is
  no instance before the first resolution, and an instance needs no other input — the artifacts
  belong to its steps, not to it).
- **Out** — exactly one of:
  - a **step resolution**: the configured step object itself, verbatim from the workflow
    configuration — `slug`, `actor`, `skills`, `instructions`, `prompts`, `artifact` (schema
    slug / URI), pre/postconditions, `capabilities` — plus the harness-resolved additions:
    the routed `model` (function 2's binding) and the instance context — always carrying the
    **workflow-instance id**, the orchestrator's handle for every subsequent call on this
    instance;
  - `no-next-step`: every authored step currently has a passing journaled execution — a
    reversible observation, with the advisory successors attached — static configuration: the
    workflows whose advisory `after` names this one (same facilitator). Advisory, never
    constraining: assent, not the DAG, starts a workflow.
- **Caller usage** — the orchestrator handles each return per its injected return-handling
  instructions (function 7). On a step resolution — per `step-resolution-handling` — it relays
  the resolution verbatim into the host dispatch (actor, model, skills, instructions, prompts
  exactly as given), awaits the step's outcome journals, and calls again — after a failed
  outcome (per `reports-handling`) exactly the same way: the cursor returns the failed step.
  On `no-next-step` — per `no-next-step-handling` — it returns the instance's END-TO-END
  workflow results (the delivered artifacts) to the user and presents the workflow-level
  options — reiterate the workflow or take an advisory successor: the user decides.

Example:

```json
{
  "in": { "workflowSlug": "verification", "workflowInstanceId": "verification-01J9XQ" },
  "out": {
    "result": "step-resolution",
    "step": {
      "slug": "review", "actor": "qa-engineer", "skills": ["code-review"],
      "instructions": ["instructions/review.instructions.md"], "prompts": [],
      "artifact": "review-report",
      "preconditions": [{ "id": "after_build", "after": "build" }],
      "postconditions": [{ "id": "report_exists", "state": "artifacts.exists(a, a.id == artifact)" }],
      "capabilities": { "deep-reasoning": 9, "code-review": 7 }
    },
    "model": { "model": "claude-sonnet-4", "score": 144, "cost_rank": 2, "reason": "highest weighted capability score" },
    "context": { "workflowSlug": "verification", "workflowInstanceId": "verification-01J9XQ" }
  }
}
```

Schema — `contracts/functions/resolve-step.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["workflowSlug"], "additionalProperties": false,
    "properties": {
      "workflowSlug": { "type": "string" }, "workflowInstanceId": { "type": "string" }
    }
  },
  "out": {
    "oneOf": [
      {
        "type": "object", "required": ["result", "step", "model", "context"],
        "properties": {
          "result": { "const": "step-resolution" },
          "step": { "$ref": "../workflow.conf.schema.json#/$defs/step" },
          "model": { "$ref": "resolve-step-model.io.schema.json#/$defs/binding" },
          "context": {
            "type": "object", "required": ["workflowSlug", "workflowInstanceId"],
            "properties": { "workflowSlug": { "type": "string" }, "workflowInstanceId": { "type": "string" } }
          }
        }
      },
      {
        "type": "object", "required": ["result", "successors"],
        "properties": {
          "result": { "const": "no-next-step" },
          "successors": { "type": "array", "items": { "type": "string" } }
        }
      }
    ]
  }
}
```

**Preconditions**

- The configuration is validated (fail-fast at load): the workflow exists, its step DAG is
  acyclic, every step routes.
- The user assented to the workflow. An instance id is given to continue an existing instance;
  omitted, this invocation OPENS the instance (mints the id, creates the log).
- Trigger — the orchestrator agent, each time it asks "what's next?" on a driven instance:
  after starting a workflow on user assent, and after each step's outcome journals — including
  after a failed outcome, where the same call re-resolves the failed step.

**Postconditions**

- Exactly one log entry records the invocation: the resolved action (`step-resolution` with the
  resolution / `no-next-step` with the advisory successors).
- When no instance id was given, the new instance exists: its id is minted and its
  log file created — the opening is this very entry, and the returned
  context carries the id.
- No artifact is written, no step is started — nothing beyond the log entry changes.

**Invariants**

1. The step cursor derives from the instance log only: a step counts as **executed** when
   its LATEST journaled `check-step-postconditions` outcome for this instance reports its
   postconditions holding (latest wins — a replayed or reopened step drops back out).
2. Eligibility follows the authored step order: the first remaining step whose `after`
   predecessors are all journaled executed. In a validated configuration (acyclic step DAG,
   resolvable `after` references — enforced at load) an instance with a remaining step always
   has exactly one next eligible step: there is no runtime "blocked" state.
3. The step resolution is fully resolved by the harness — the agent relays it verbatim into the
   host dispatch (e.g. `runSubagent`), never chooses for itself. Model resolution delegates to
   `resolve-step-model` (function 2); the config plane guarantees every step routes (function 2,
   invariant 4), so the harness never passes `Auto`.
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

## 2. resolve-step-model

The model resolution function: which model serves this step's dispatch. Resolved from two static
configuration layers, deterministically, with no artifact reads and no per-instance estimation.
It is resolved exactly once per step resolution — inside every function-1 step resolution — and
nowhere else: the binding rides the step resolution the orchestrator relays verbatim (per its
`step-resolution-handling` instruction, function 7), so the resolved model is what reaches the
dispatch.

**Interface**

- **In** — the step's weighted `capabilities` map. **Out** — the model binding
  `{model, score, cost_rank, reason}`.
- **Caller usage** — function 1 embeds the binding in the step resolution it returns; the
  orchestrator relays it verbatim into the dispatch — the binding is resolved once, here, never
  at any later boundary.

Example:

```json
{
  "in": { "capabilities": { "deep-reasoning": 9, "code-review": 7 } },
  "out": { "model": "claude-sonnet-4", "score": 144, "cost_rank": 2, "reason": "highest weighted capability score" }
}
```

Schema — `contracts/functions/resolve-step-model.io.schema.json`:

```json
{
  "$defs": {
    "binding": {
      "type": "object", "required": ["model", "score", "cost_rank"],
      "properties": {
        "model": { "type": "string" }, "score": { "type": "number" },
        "cost_rank": { "type": "integer" }, "reason": { "type": "string" }
      }
    }
  },
  "in": {
    "type": "object", "required": ["capabilities"],
    "properties": { "capabilities": { "type": "object", "additionalProperties": { "type": "number" } } }
  },
  "out": { "$ref": "#/$defs/binding" }
}
```

**Preconditions**

- The model catalog and the step's capability map are loaded and validated (fail-fast at load).
- Trigger — inside every `resolve-step` step resolution (invariants 1–4), and nowhere else:
  one resolution per step resolution.

**Postconditions**

- The model binding rides in function 1's step-resolution log entry — no separate entry, no
  later re-resolution. No `Auto` and no unknown model id ever rides a step resolution.

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

   Highest score wins; ties break toward lower `cost_rank`. Cost sensitivity emerges
   structurally: low, sparse weights compress candidate scores into a narrow band where the
   cheap-model tie-break dominates; high weights on discriminating tags let capability dominate
   cost.

   *Worked example* — a step weighting `deep-reasoning: 9`, two models scoring 9 vs 7 on it:
   $A = 81$, $B = 63$ — A wins on capability. At weight 3 the spread narrows ($27$ vs $21$) and,
   across a larger candidate set, the `cost_rank` tie-break routes cheaper.

4. An empty catalog or an all-zero effective weight is **unroutable** — rejected at
   configuration load (invariant 1 plus catalog validation), so it cannot occur at runtime and
   is never papered over with a silent `Auto`.
5. **One resolution, no boundary re-resolution**: the binding is computed exactly once, inside
   function 1's step resolution, and relayed verbatim by the orchestrator per its
   `step-resolution-handling` instruction — never `Auto`, never re-resolved downstream. The
   harness resolves; the instructed agent relays.

## 3. check-step-preconditions

May this step start? The gate the orchestrator consults between resolution and dispatch, and
the entry check of the step's own session. Evaluated strictly against persisted workspace state
(C1) and the instance log — never against anything an agent merely remembers.

**Interface**

- **In** — the step (its declared preconditions).
- **Out** — per-condition outcomes (`pass` / `fail` / `skipped`, keyed by condition id) + the
  aggregate verdict.
- **Caller usage** — the orchestrator dispatches only on a passing verdict; on `fail` — per its
  `reports-handling` instruction (function 7) — it reports exactly which persisted state is
  missing so the user or a predecessor step produces it — it never overrides a failing
  precondition.

Example:

```json
{
  "in": { "workflowSlug": "verification", "workflowInstanceId": "verification-01J9XQ", "stepSlug": "review" },
  "out": {
    "verdict": "fail",
    "conditions": [
      { "id": "after_build", "outcome": "pass" },
      { "id": "report_exists", "outcome": "fail", "report": "no artifact matches 'review-report'" }
    ]
  }
}
```

Schema — `contracts/functions/check-step-preconditions.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["workflowSlug", "workflowInstanceId", "stepSlug"],
    "properties": {
      "workflowSlug": { "type": "string" }, "workflowInstanceId": { "type": "string" },
      "stepSlug": { "type": "string" }
    }
  },
  "out": {
    "type": "object", "required": ["verdict", "conditions"],
    "properties": {
      "verdict": { "enum": ["pass", "fail", "skipped"] },
      "conditions": {
        "type": "array",
        "items": {
          "type": "object", "required": ["id", "outcome"],
          "properties": {
            "id": { "type": "string" },
            "outcome": { "enum": ["pass", "fail", "skipped"] },
            "report": { "type": "string" }
          }
        }
      }
    }
  }
}
```

**Preconditions**

- A resolved step is in hand, carrying its declared precondition list from the workflow
  configuration.
- Persisted workspace state and the instance log are readable.
- Trigger — the host `preToolUse` on the dispatch tool (step-starting boundary — THE enforcement
  point: a failing precondition denies the dispatch); the step session's opening (host
  `sessionStart`, step-started boundary) re-checking entry conditions; optionally the
  orchestrator, probing before
  dispatch for early feedback — the probe is advisory, the boundary enforces.

**Postconditions**

- One log entry records the invocation: per-condition outcomes under their condition ids
  plus the aggregate counts.
- The workspace is untouched — checking never mutates state.

**Invariants**

1. `after` conditions: every referenced predecessor step must be journaled executed in this
   instance. With no log in scope, `after` conditions are reported `skipped`, never silently
   passed.
2. `state` conditions: a CEL selector picks a set of persisted artifacts (the step's declared
   `artifact` ref is in scope as a runtime constant) and a CEL predicate must hold over the
   selection. Every
   `<alias>.<property>` reference is statically validated against the aliased artifact schema —
   an undeclared property is a hard error, not a false pass.
3. Condition ids are the audit handle: unique within a step, and every outcome (`pass` / `fail`
   / `skipped`) logs under its condition id.

## 4. check-step-postconditions

Did this step deliver? The same condition machinery as function 3, applied to the step's
declared postconditions — and the producer of the step outcome that drives the whole instance:
function 1's cursor reads nothing else.

**Interface**

- **In** — the step (its declared postconditions) + final persisted
  state.
- **Out** — per-condition outcomes + the step outcome.
- **Caller usage** — on a passing outcome the orchestrator calls function 1 for the next step;
  on a failing one — per its `reports-handling` instruction (function 7) — it handles the
  reports and calls function 1 again — the failed step is not
  journaled executed, so the cursor resolves it again, the reports feeding the new pass. The
  failure stays inside the workflow: the user sees end-to-end workflow results only (function
  1, invariant 7).

Example:

```json
{
  "in": { "workflowSlug": "verification", "workflowInstanceId": "verification-01J9XQ", "stepSlug": "review" },
  "out": {
    "outcome": "pass",
    "conditions": [{ "id": "report_exists", "outcome": "pass" }]
  }
}
```

Schema — `contracts/functions/check-step-postconditions.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["workflowSlug", "workflowInstanceId", "stepSlug"],
    "properties": {
      "workflowSlug": { "type": "string" }, "workflowInstanceId": { "type": "string" },
      "stepSlug": { "type": "string" }
    }
  },
  "out": {
    "type": "object", "required": ["outcome", "conditions"],
    "properties": {
      "outcome": { "enum": ["pass", "fail"] },
      "conditions": { "$ref": "check-step-preconditions.io.schema.json#/out/properties/conditions" }
    }
  }
}
```

**Preconditions**

- The step was dispatched (an open dispatch in the instance log) and its execution is being
  evaluated — at the step-ended boundary.
- Trigger — the host `postToolUse` on the dispatch tool (step-ended boundary — THE evaluation
  point: the step's session has ended, the state it left is final).

**Postconditions**

- One log entry per step evaluation, carrying the step's outcome — the exact input of
  function 1's cursor.

**Invariants**

1. `state` assertions evaluate over persisted artifacts only — never agent memory (C2).
2. Postconditions are evaluated ONCE per step pass, at the step-ended boundary — the step's
   session has ended, the state it left is final; the step's own session end adds no second
   evaluation of the same state.
3. The step's outcome logs from this function: its postconditions hold, or they do not.
   This journaled outcome is exactly what function 1's cursor reads — a step whose latest
   outcome passes counts as executed.

## 5. check-step-authorization

Is this write a granted privilege of the acting agent? Plain whole-resource RBAC over
`<action>_<resource>` privileges from `conf/access-control-list.conf.yaml` (actors → roles →
privileges), guarding every agent write live at the boundary. (The after-the-fact audit of a
whole instance is function 11, `audit-workflow-instance`.)

**Interface**

- **In** — the normalized actor + the **artifact path** (`artifactPath`, the pending
  write's target: the input is a path; the resource — the artifact's schema slug — is derived
  from it, invariant 2). **Out** — allow, or deny naming the
  missing `<action>_<resource>` privilege.
- **Caller usage** — the hook adapter enforces the live verdict (a denied tool call never
  executes); the orchestrator then routes the change through a privileged author and re-runs.

Example:

```json
{
  "in": { "actor": "qa-engineer", "artifactPath": "portfolio/epics/epic-payments.md" },
  "out": { "allowed": false, "missing": "update_epic" }
}
```

Schema — `contracts/functions/check-step-authorization.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["actor", "artifactPath"],
    "properties": { "actor": { "type": "string" }, "artifactPath": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["allowed"],
    "properties": { "allowed": { "type": "boolean" }, "missing": { "type": "string" } }
  }
}
```

**Preconditions**

- The ACL, workspace layout, and schema catalog are loaded (path → resource resolution needs
  them).
- Trigger — the host `preToolUse` hook on write tools, once per pending write (the
  write-starting boundary).

**Postconditions**

- One log entry per authorization decision (allow / deny with the missing privilege).
- On a deny, the write never lands — the workspace never sees unauthorized bytes.

**Invariants**

1. The actor is the AGENT (normalized agent identity), never the skill.
2. The resource is the artifact's schema identity, resolved from the write path — via the
   workspace layout's singleton map for well-known single-instance files, else via the schema
   catalog's path patterns (disambiguated by the artifact's `type` when several match).
3. Authorization is whole-resource: any `#property` suffix on an artifact path is ignored.
4. A write nobody granted is denied at the boundary — denial is the enforcement.

## 6. check-step-artifacts

Are the step's written artifacts schema-valid? The write-boundary enforcement of C6 — the
function that keeps the workspace valid-by-construction for every reader, one write at a time.
(The workspace-wide validation on demand is function 12, this function's instance-less
counterpart.)

**Interface**

- **In** — the written artifact path. **Out** — valid, or the schema reports plus the revert
  record.
- **Caller usage** — the agent receives the reports and rewrites the artifact correctly; a
  write never silently corrupts the workspace.

Example:

```json
{
  "in": { "artifactPath": "portfolio/payments/features/feature-refunds.md" },
  "out": {
    "valid": false,
    "reports": ["frontmatter.status: 'shipped' is not one of the enum values"],
    "revert": { "action": "restored", "from": "HEAD" }
  }
}
```

Schema — `contracts/functions/check-step-artifacts.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["artifactPath"],
    "properties": { "artifactPath": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["valid"],
    "properties": {
      "valid": { "type": "boolean" },
      "reports": { "type": "array", "items": { "type": "string" } },
      "revert": {
        "type": "object", "required": ["action"],
        "properties": { "action": { "enum": ["restored", "deleted"] }, "from": { "type": "string" } }
      }
    }
  }
}
```

**Preconditions**

- The schema catalog is loaded and the written path resolves to its artifact schema.
- Trigger — the host `postToolUse` hook after every write.

**Postconditions**

- C6 holds again: the written artifact is schema-valid, or the write has been reverted and
  denied.
- One log entry per write validation (valid / reverted+denied with reports). The revert
  logs as its own auditable act.

**Invariants**

1. Every artifact write is validated against its matched artifact schema (path patterns + `type`
   disambiguation; schemas extend the harness base contract via `$ref`).
2. An invalid write is **reverted** — restore the last-committed version if tracked, else delete
   the new file — and denied with the schema reports so the agent retries. This revert is the
   harness's single deliberate write and logs as its own auditable act.

## 7. inject-workflow-instructions

Which workflow-context guidance does the orchestrator's session load? Deterministic injection
at the session-started boundary of the facilitator's workflow context: the catalog map (each
workflow it
facilitates, with its advisory position and guidance) and the **return-handling instructions** —
one named instruction per harness return the orchestrator must handle, so every harness return
meets an instructed reaction, never an improvised one:

- `workflow-selection-handling` — how to select from the injected catalog map: match the
  user's intent to one facilitated workflow, propose it (or the continuation of an instance
  the user names), ask when ambiguous, await assent.
- `step-resolution-handling` — how to use function 1's step resolution: relay it verbatim into
  the host dispatch, keep the instance id as the handle.
- `no-next-step-handling` — how to close: return end-to-end workflow results and the
  workflow-level options.
- `reports-handling` — how to react to any negative return (denied dispatch, failed outcome):
  produce the missing state, re-resolve — never override, never surface step details to the
  user.

**Interface**

- **In** — the facilitator: the session's normalized agent identity (the hook plane resolves
  the session-start event to it — the catalog is keyed by facilitator).
- **Out** — the catalog map + the return-handling instruction refs.
- **Caller usage** — the adapter renders the refs into the host's session context; the
  orchestrator starts its conversation already knowing its workflows and how to handle every
  harness return.

Example:

```json
{
  "in": { "facilitator": "scrum-master" },
  "out": {
    "catalog": {
      "pair-programming": { "after": [], "guidance": "Implement a story pair-wise." },
      "verification": { "after": ["pair-programming"], "guidance": "Verify a delivered story." }
    },
    "instructions": [
      "instructions/workflow-selection-handling.instructions.md",
      "instructions/step-resolution-handling.instructions.md",
      "instructions/no-next-step-handling.instructions.md",
      "instructions/reports-handling.instructions.md"
    ]
  }
}
```

Schema — `contracts/functions/inject-workflow-instructions.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["facilitator"],
    "properties": { "facilitator": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["catalog", "instructions"],
    "properties": {
      "catalog": { "type": "object",
        "additionalProperties": { "type": "object", "properties": {
          "after": { "type": "array", "items": { "type": "string" } },
          "guidance": { "type": "string" } } } },
      "instructions": { "type": "array", "items": { "type": "string" } }
    }
  }
}
```

**Preconditions**

- A session opened with no open dispatch correlating to its actor — an orchestrator session
  (a step session loads functions 9–10 instead).
- The session's actor resolves to a framework facilitator agent (C7 — foreign sessions pass
  through untouched).
- Trigger — the host `sessionStart` hook.

**Postconditions**

- The session context contains the facilitator's catalog map and the return-handling
  instruction refs — nothing more, nothing chosen by the agent.
- The injection rides the session-started log entry (what was injected, for which facilitator).

**Invariants**

1. The catalog map derives from configuration only: the workflows whose `facilitator` is the
   session's actor, each with its advisory `after` and guidance — static, no journal reads.
2. The return-handling instructions are framework-authored refs, one per harness return kind
   the orchestrator handles; every function-1 return the orchestrator receives — and the
   catalog-map selection itself — is covered by an injected instruction.
3. Injection is deterministic and facilitator-scoped: the configuration decides, never the
   agent.

## 8. inject-workflow-skills

Which skills does the orchestrator's session load? The same session-kind correlation and
determinism as function 7, for skills: the facilitator's procedure skills — its selection skill
and one procedure skill per workflow it facilitates.

**Interface**

- **In** — the facilitator: the session's normalized agent identity.
- **Out** — the skill ids to load.
- **Caller usage** — the adapter loads the skills into the session; the orchestrator's toolbox
  is its facilitator role's toolbox, by construction.

Example:

```json
{
  "in": { "facilitator": "scrum-master" },
  "out": { "skills": ["workflow-selection", "pair-programming-procedure", "verification-procedure"] }
}
```

Schema — `contracts/functions/inject-workflow-skills.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["facilitator"],
    "properties": { "facilitator": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["skills"],
    "properties": { "skills": { "type": "array", "items": { "type": "string" } } }
  }
}
```

**Preconditions**

- A session opened with no open dispatch correlating to its actor — an orchestrator session.
- Trigger — the host `sessionStart` hook.

**Postconditions**

- The session loads exactly the facilitator's declared skill set.
- The injection rides the session-started log entry alongside function 7's.

**Invariants**

1. The skill set derives from configuration only: the selection skill plus each facilitated
   workflow's procedure skill.
2. Injection is deterministic: the configuration decides, never the agent.

## 9. inject-step-instructions

Which behavioral guidance does this step's session load? Deterministic injection of the step's
authored context at the step-started boundary: 1 step = 1 agent = 1 session = 1 artifact — the
step's authored constraints
reach the agent with no discretion of its own.

**Interface**

- **In** — the workflow-instance id + the step slug: the step whose session opened. The
  function takes the step key, not the raw event — the hook plane resolves the session-start
  event to this In (the open-dispatch correlation is normalization's job, not the injector's).
- **Out** — the step's declared instruction and prompt refs.
- **Caller usage** — the adapter renders the refs into the host's session context; the agent
  starts its turn already carrying its constraints.

Example:

```json
{
  "in": { "workflowInstanceId": "verification-01J9XQ", "stepSlug": "review" },
  "out": {
    "stepSlug": "review",
    "instructions": ["instructions/review.instructions.md"],
    "prompts": []
  }
}
```

Schema — `contracts/functions/inject-step-instructions.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["workflowInstanceId", "stepSlug"],
    "properties": { "workflowInstanceId": { "type": "string" }, "stepSlug": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["stepSlug", "instructions"],
    "properties": {
      "stepSlug": { "type": "string" },
      "instructions": { "type": "array", "items": { "type": "string" } },
      "prompts": { "type": "array", "items": { "type": "string" } }
    }
  }
}
```

**Preconditions**

- A session opened and the instance logs are readable (the correlation source).
- An open dispatch addressed to this actor exists in the instance logs — a step session (a
  session with none is the orchestrator's and loads functions 7–8 instead).
- The session's actor resolves to a framework agent (C7 — foreign sessions pass through
  untouched).
- Trigger — the host `sessionStart` hook.

**Postconditions**

- The session context contains exactly its step's declared refs — nothing more, nothing chosen
  by the agent.
- The injection rides the step-started log entry (what was injected, for which step).

**Invariants**

1. Instruction and prompt refs are declared per step in the workflow configuration
   (`instructions:` / `prompts:` — contract/repo-relative refs).
2. At session open, the hook plane correlates the new session to its step (the most recent
   open dispatch addressed to the session's actor in the instance logs) and calls this
   function with THAT step's key; the injection itself is a pure configuration lookup.
3. Injection is deterministic and step-scoped: the workflow configuration decides, never the
   agent.

## 10. inject-step-skills

Which skills does this step's session load? The same correlation and determinism as function
9, for skills — a session's capabilities are step-scoped by construction.

**Interface**

- **In** — the workflow-instance id + the step slug (resolved by the hook plane, as function
  9).
- **Out** — the skill ids to load.
- **Caller usage** — the adapter loads the skills into the session; the agent's toolbox is its
  step's toolbox, by construction.

Example:

```json
{
  "in": { "workflowInstanceId": "verification-01J9XQ", "stepSlug": "review" },
  "out": { "stepSlug": "review", "skills": ["code-review"] }
}
```

Schema — `contracts/functions/inject-step-skills.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["workflowInstanceId", "stepSlug"],
    "properties": { "workflowInstanceId": { "type": "string" }, "stepSlug": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["stepSlug", "skills"],
    "properties": {
      "stepSlug": { "type": "string" },
      "skills": { "type": "array", "items": { "type": "string" } }
    }
  }
}
```

**Preconditions**

- A session opened with an open dispatch correlating to its actor — a step session.
- Trigger — the host `sessionStart` hook.

**Postconditions**

- The session loads exactly its step's declared skills.
- The injection rides the step-started log entry alongside function 9's.

**Invariants**

1. Skill ids are declared per step (`skills:` in the workflow configuration) — per step, not per
   workflow: a session loads exactly its step's skills.
2. Correlation identical to function 9 (open-dispatch lookup).
3. Injection is deterministic: the step declaration decides.

## 11. audit-workflow-instance

Were all of this instance's recorded writes granted? The retrospective audit of one workflow
instance: every write recorded in the instance journal is re-verified against the ACL, after
the fact — the same rule function 5 enforces live, replayed over the whole journal for audit.
Instance-scoped, not step-scoped: it reads one journal end to end.

**Interface**

- **In** — the workflow-instance id.
- **Out** — the audit report over every recorded write (per write: actor, path, allowed,
  missing privilege where denied-in-hindsight).
- **Caller usage** — an agent (or a CI job) runs it on demand over a delivered instance; the
  report feeds audit — nothing is reverted, nothing re-executed.

Example:

```json
{
  "in": { "workflowInstanceId": "verification-01J9XQ" },
  "out": {
    "passed": true,
    "writes": [
      { "artifactPath": "portfolio/payments/stories/story-checkout.md", "actor": "qa-engineer", "allowed": true }
    ]
  }
}
```

Schema — `contracts/functions/audit-workflow-instance.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["workflowInstanceId"],
    "properties": { "workflowInstanceId": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["passed", "writes"],
    "properties": {
      "passed": { "type": "boolean" },
      "writes": { "type": "array", "items": {
        "type": "object", "required": ["artifactPath", "actor", "allowed"],
        "properties": {
          "artifactPath": { "type": "string" }, "actor": { "type": "string" },
          "allowed": { "type": "boolean" }, "missing": { "type": "string" }
        } } }
    }
  }
}
```

**Preconditions**

- The instance journal exists and is readable; the ACL, workspace layout, and schema catalog
  are loaded (path → resource resolution, as function 5).
- Trigger — an agent command on demand (after a workflow return, on a user's audit request);
  optionally a CI job over delivered instances.

**Postconditions**

- One log entry records the audit sweep and its verdict in this instance's own journal.
- Nothing is mutated: the audit reports; remediation — if any — goes through agents.

**Invariants**

1. The audit applies exactly function 5's rule (actor → privileges → resource from path),
   re-computed against the CURRENT ACL — a later ACL change can turn a past allow into a
   reported violation; the journal records what was decided, the audit reports what holds now.
2. Every recorded write of the journal is verified — the report is complete, never truncated
   at the first violation.
3. Read-only over artifacts: the only write is its own journal entry.

## 12. check-workspace

Is the workspace, as a whole, in a valid state? The instance-less proof of C6: where function 6
guards each write as it lands, this function proves the invariant globally, on demand — the
audit and CI face of workspace validity.

**Interface**

- **In** — the scope: full workspace, one artifact subtree, or one file.
- **Out** — the reports (per artifact, per rule).
- **Caller usage** — CI fails the pipeline on reports, blocking a merge that would break C6;
  the orchestrator routes reports to the responsible agents for remediation and re-runs until
  clean.

Example:

```json
{
  "in": { "scope": "subtree", "root": "portfolio/payments" },
  "out": {
    "valid": false,
    "reports": [
      { "artifact": "portfolio/payments/features/feature-refunds.md", "rule": "schema", "report": "frontmatter.status: invalid enum value" }
    ]
  }
}
```

Schema — `contracts/functions/check-workspace.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["scope"],
    "properties": {
      "scope": { "enum": ["workspace", "subtree", "file"] },
      "root": { "type": "string" }
    }
  },
  "out": {
    "type": "object", "required": ["valid", "reports"],
    "properties": {
      "valid": { "type": "boolean" },
      "reports": { "type": "array", "items": {
        "type": "object", "required": ["artifact", "rule", "report"],
        "properties": {
          "artifact": { "type": "string" }, "rule": { "type": "string" }, "report": { "type": "string" }
        } } }
    }
  }
}
```

**Preconditions**

- The schema catalog and workspace layout are loaded (fail-fast at load).
- Trigger — a CI pipeline on the workspace repository (every push / pull request); the
  orchestrator agent on demand (before proposing, after an external sync, or when the user asks
  "is the workspace sound?").

**Postconditions**

- The reports exist for the scope; zero reports prove C6 globally for that scope.
- When invoked inside an instance context, one log entry records the sweep. CI invocations
  are instance-less: the pipeline log is the record, and the exit status drives the gate.
- The workspace is untouched — validation never mutates state.

**Invariants**

1. Every artifact in scope is validated against its matched artifact schema, plus the
   cross-artifact rules a single write cannot see: scope/frontmatter coherence, parent linkage
   resolution, blocking open items.
2. Validation reads the raw universe (`scan_raw`) — the sweep must be able to enumerate invalid
   artifacts that valid-by-construction readers refuse to yield.
3. The sweep never mutates: it reports. Remediation goes through agents (unlike function 6's
   live revert, this function's write-boundary counterpart).
4. Scope is selectable: the full workspace, one artifact subtree, or one file.

## 13. check-configuration

Is the embedding application's configuration a valid input to the harness? This validates
everything the other twelve functions trust: the configuration plane of the integrating application
(e.g. this repo's SAFe agentic framework) — the layout environment, every configuration file,
the semantic rules beyond schema, cross-configuration coherence, and the adapter bindings.

**Interface**

- **In** — the framework root (the `.env` layout environment + `conf/` + `harness/adapters/`).
- **Out** — the reports (per file, per rule).
- **Caller usage** — CI gates merges to the framework repository; an agent that edited
  configuration validates before considering its edit delivered; the harness itself runs the
  same validation at startup and refuses to serve on reports.

Example:

```json
{
  "in": { "root": "." },
  "out": {
    "valid": false,
    "reports": [
      { "source": "conf/workflows/verification.workflow.conf.yaml", "rule": "semantic", "report": "step 'review': after reference 'biuld' does not resolve" },
      { "source": ".env", "rule": "layout", "report": "FRAMEWORK_SKILLS_DIR points to a missing directory" }
    ]
  }
}
```

Schema — `contracts/functions/check-configuration.io.schema.json`:

```json
{
  "in": {
    "type": "object", "required": ["root"],
    "properties": { "root": { "type": "string" } }
  },
  "out": {
    "type": "object", "required": ["valid", "reports"],
    "properties": {
      "valid": { "type": "boolean" },
      "reports": { "type": "array", "items": {
        "type": "object", "required": ["source", "rule", "report"],
        "properties": {
          "source": { "type": "string" }, "rule": { "type": "string" }, "report": { "type": "string" }
        } } }
    }
  }
}
```

**Preconditions**

- The framework repository (its `.env`, `conf/`, and the harness adapters) is readable; no
  workspace is needed.
- Trigger — a CI pipeline on the framework repository (every push / pull request); an agent on
  demand, after editing any configuration.

**Postconditions**

- Full reports exist (validation never stops at the first report).
- Instance-less by nature: no log entry; the pipeline log or the agent report is the record.

**Invariants**

1. Every configuration file validates against its contract schema — parsing and validation are
   one act; an unvalidated parse never escapes.
2. The semantic rules JSON Schema cannot express are enforced: non-empty steps, unique step
   slugs, resolvable step `after` references, unique condition ids per step, acyclic step
   DAGs, at least one positive capability weight per step; at catalog level, the advisory
   workflow graph resolves and is acyclic.
3. Cross-configuration coherence holds: workflow actors exist in the ACL, capability tags belong
   to the model catalog's vocabulary, step `artifact` slugs resolve to cataloged schemas, and
   instruction/prompt/skill refs resolve to files in the framework layout.
4. The layout environment is validated like any file configuration: every required layout
   variable is present (from the process environment or the `.env` file) and points to an
   existing directory.
5. Adapter bindings are validated like any other configuration (against the adapter contract):
   host tool names, write verbs, and model id bindings that map to canonical profiles.
6. This function is the explicit, reportable form of the same validation that runs implicitly,
   fail-fast, at every harness start — the two can never diverge.

---

# Design

## Three trigger planes, one command system

Every harness function is exposed as a harness command. The same command system is entered from
three places:

- **Hook-triggered**: the host calls the harness on lifecycle events — functions 3–10 at their
  natural boundaries.
- **Agent-triggered**: the orchestrator agent calls the resolution functions (1–2), may invoke
  any check (3–6) explicitly, and runs the instance audit (11) and the global checks (12–13)
  on demand.
- **CI-triggered**: pipelines run the global checks as gates — function 12 on the workspace
  repository, function 13 on the framework repository.

There is no separate hook logic outside the harness: the harness mediates all host hook events,
so the host adapter never becomes a second source of truth. The adapters only forward host
events and render the harness decision back into the host format.

## Hook Event normalization

Host events are normalized to harness boundaries before any policy is applied. The boundary is
determined by the event AND the tool class (from the adapter binding): dispatch tools
(`runSubagent`) carry the **step boundary** — the subagent session IS the step, so step pre-
and postconditions apply before and after it — while write tools carry the **write boundary**
(authorization and schema validity, never step conditions).

| Host event | Tool class | Boundary | Functions |
|---|---|---|---|
| `sessionStart` | — | session-started (no open dispatch: the orchestrator's session) or step-started (open dispatch: a step session) | 7, 8 (workflow injection) or 9, 10 (step injection) + 3 (entry re-check, step sessions) |
| `userPromptSubmit` | — | observing | record only |
| `preToolUse` | dispatch | step-starting | 3 (preconditions — THE enforcement point, before the step's session opens: this boundary can deny) |
| `postToolUse` | dispatch | step-ended | 4 (postconditions — THE evaluation point, after the step's session ended) |
| `preToolUse` | write | write-starting | 5 (authorization — can deny) |
| `postToolUse` | write | write-ended | 6 (schema validity + revert) |
| `stop` / `sessionEnd` | — | step-ending (the step session ending) | record only |

This keeps the harness env-agnostic: only the adapter maps host-specific event names and tool
payloads, and only the adapter binding decides which tools are dispatches and which are writes.

The boundary names are lifecycle participles precisely to dissolve the step/session clash the
1 step = 1 session invariant creates: **step-starting** (the dispatch about to open the step —
it can deny) and **step-started** (the step session having opened — it can only inject) coincide
in time but differ in capability; **step-ending** (the step session ending — record only) and
**step-ended** (the dispatch return — THE evaluation point) likewise. No merged "step-start"
boundary could carry both capabilities — no host guarantees a session hook can veto its own
session.

**Framework-agent gate (C7)** — normalization resolves the event's actor first: only events
whose actor is a framework agent proceed to any function; everything else passes through
untouched and unlogged. The harness governs the framework's agents, never the host's other
inhabitants.

## Source layout

Six packages, one dependency direction — `commands → services → {workspace, config} → models`,
with `utils` beneath everything:

```text
src/
  application.py      # the composition root: builds the object graph (config dataclasses fail-fast) and dispatches argv to one command
  commands/           # usage entry points (≈ web controllers): parse input, invoke the service(s), render the result — no domain logic
  services/           # the logical domain services commands use: all harness logic lives here
    step_resolution/  # StepResolver + its result: StepResolution
    model_resolution/ # StepModelResolver + its result: ModelBinding
    checking/         # the checkers, the auditor, ConditionEvaluator + their results: CheckReport, ConditionOutcome
    injection/        # the four injectors + their results: Injection, WorkflowPosition
    hooks/            # the hook plane: HookNormalizer (+ its Boundary), SessionCorrelator
  models/             # the PERSISTED dataclasses: Artifact + the journal (Log, LogEntry, Report — persisted in every entry)
  workspace/          # data access: the Workspace facade over artifacts + instance journals — used by services exclusively
  config/             # ConfigLoader + the configuration dataclasses it constructs (parse + contract-validate + semantic rules)
  utils/              # domain-free mechanics shared by config and workspace: .env/YAML/JSON(L) loading, JSON Schema validation
```

Three placement rules settle the boundary questions:

- **A contract is loaded by the layer that owns the boundary it guards.** The
  `*.conf.schema.json` contracts guard the configuration boundary, so `config/` loads them —
  parsing and validation are one act there. The data contracts (the artifact base contract, the
  journal contract, and the framework's artifact schemas) guard the workspace read/write
  boundary, so `workspace/` loads them — that is where valid-by-construction (C6) is enforced.
  Services never touch a raw schema: they receive typed configuration views and
  valid-by-construction entities.
- **Adapter configuration goes through `config/` like everything else.** The split between
  framework-supplied configuration (`conf/`, declared by the embedding application) and
  harness-internal configuration (`harness/adapters/<env>/`, resolved structurally from the
  harness tree) is a difference of *provenance*, not *mechanism*. One loader discipline parses
  and contract-validates every configuration file; the config plane keeps the two provenances
  distinct in its API (`ConfigLoader.load_layout/load_acl/…` for `conf/` and `.env`,
  `ConfigLoader.load_adapter_binding(env)` for adapters) so
  neither leaks into the other.
- **Shared mechanics live in `utils/`.** `config/` and `workspace/` both parse files and
  validate instances against JSON Schema contracts, so the mechanical primitives are one shared
  package: the `.env` loader, the safe YAML loader, the JSON / JSONL readers, and the schema
  validator (contract compilation, `$ref` registry, instance validation returning raw
  reports). `utils/` is
  strictly domain-free — it knows no artifact, workflow, or ACL, depends on nothing internal,
  and returns plain data; each caller layer turns raw reports into its own typed views or
  entities. Anything with domain meaning belongs to its owning layer, never to `utils/`.

## Class design

The class diagram [`def/harness-src-classes.puml`](def/harness-src-classes.puml) is the
prescriptive class model of `src/`: **1 folder = 1 module = 1 package** in the diagram. Classes
expose their public interface — what other classes call — plus one **private, constructor-
injected attribute per dependency**, typed after the collaborator class (`-_workspace :
Workspace`); dataclasses expose public typed attributes. **Method names are verb + subject**
(`resolve_step`, `check_step_preconditions`, `load_workflow_catalog`, `append_log_entry`).
Methods return **typed results, never bare dicts**: services return the result dataclasses
homed in their own family subpackage, and the JSON dict appears only at the command boundary,
where the contract
lives. External dependencies are drawn: **cel-python** (CEL compilation/evaluation, used by
`ConditionEvaluator`), **jsonschema** (contract validation, used by `SchemaValidator`), and
**PyYAML** (safe loading, used by `YamlLoader`) — the harness's only third-party imports. One
dependency direction, as in the source layout: `commands → services → {workspace, config} →
models`, with `utils` beneath `workspace` and `config`.

- **`application` (src root)** — `Application` is the composition root, a single module at the
  root of `src/`, above every package: it builds every configuration dataclass through
  `ConfigLoader` (fail-fast) and wires the object graph, then dispatches `argv` to one command
  (`dispatch_command`).
- **`commands`** — `Command` is the single interface (`execute_function`), realized by
  **fourteen commands — one per harness function plus `HookCommand`** — each holding its
  service(s) as private attributes: parse the function's `in` object, invoke, render the typed
  result to the `out` object. `ResolveStepCommand` is the one composing command: function 1's
  `out` embeds function 2's binding, so it invokes `StepResolver` then `StepModelResolver` and
  merges the two results — the services stay independent of each other. `HookCommand` is the
  hook entry point: `HookNormalizer` gives the boundary, `SessionCorrelator` resolves
  session-start events to the injectors' In (facilitator, or workflow-instance id + step slug
  via the open-dispatch lookup), and the boundary's commands run — no policy of its own.
- **`services`** — all harness logic, one service per function, grouped in **four subpackages
  by family**, and each result dataclass homed in the subpackage of the classes returning it —
  **exclusively with them**:
  - `step_resolution/` — `StepResolver` (1 → `StepResolution`);
  - `model_resolution/` — `StepModelResolver` (2 → `ModelBinding`);
  - `checking/` — `StepPreconditionChecker` (3), `StepPostconditionChecker` (4),
    `StepAuthorizationChecker` (5 — live only: `check_step_authorization(actor,
    artifact_path)`), `StepArtifactChecker` (6), `WorkflowInstanceAuditor` (11 —
    `audit_workflow_instance` replays every recorded write of the journal against the ACL),
    `WorkspaceChecker` (12), `ConfigurationChecker` (13), plus `ConditionEvaluator` (the CEL
    machinery functions 3–4 share, via cel-python) — with their results `CheckReport` (passed
    + reports + condition outcomes) and `ConditionOutcome`;
  - `injection/` — the four injectors (7–10) with their results `Injection` and
    `WorkflowPosition`;
  - `hooks/` — `HookNormalizer` with its `Boundary` enum, and `SessionCorrelator` (hook plane
    only — injectors take their resolved keys, never raw events).

  Results are not models: nothing here persists — they are rendered to contract JSON by the
  commands and carried as journal-entry payloads.
- **`models`** — the **persisted** dataclasses only: `Artifact`, `Log` (attributes
  `workflow_instance_id`, `entries`; derived queries `list_executed_steps()`,
  `find_latest_outcome(step_slug)`, `find_open_dispatch(actor)`), `LogEntry` (command,
  envelope fields, **`report` — present on every entry**, payload), and `Report` — persisted
  inside journal entries. Frozen dataclasses: public typed attributes, no getters/setters.
- **`workspace`** — one class: the `Workspace` facade owns both persisted data kinds —
  artifacts (`discover_artifacts` raises on an invalid artifact, C6; `scan_raw_paths`
  enumerates the raw universe; `validate_artifact` produces schema `Report`s;
  `revert_artifact` is the single deliberate write) and instance journals
  (`open_workflow_instance` mints a workflow-instance id and creates its log;
  `load_instance_log` hydrates a `Log`; `append_log_entry` writes one contract-bound entry).
- **`config`** — `ConfigLoader` plus the configuration dataclasses it constructs, homed
  together: `FrameworkLayout`, `AccessControlList`, `ModelProfiles`/`ModelProfile`,
  `WorkspaceLayout`/`WorkspaceNode`, `WorkflowCatalog`/`Workflow`/`Step`/`Condition`,
  `AdapterBinding`. Each `load_*` method performs parse + contract-validation + semantic rules
  + dataclass construction as ONE act; there is **no aggregate `FrameworkConfig`**: each
  service receives exactly the dataclasses it needs.
- **`utils`** — the domain-free mechanics both `config/` and `workspace/` share: `EnvLoader`,
  `YamlLoader` (PyYAML), `JsonlStore` (the journal file: `load_entries` / `append_entry` — a
  store, not a reader, because it persists too), `SchemaValidator` (jsonschema) — plain data
  in, plain
  data out.

## Hook adapter layout

The dispatch script is shared and generic across every host; only the per-host registration and
tool binding live under each adapter's own subfolder. See `harness/adapters/README.md` for the
full adapter contract.

`dispatch.sh` is intentionally thin and env-agnostic: it takes the event name and the environment
id as its two arguments and forwards the raw event payload to `harness.py hook --event <name>
--env <env>`, exiting with the harness result. Each adapter's `hooks.yaml` supplies its own
environment id as the second argument.

```text
.env                  # framework layout environment: where skills, agents, schemas, templates, workspace live
conf/                 # env-agnostic framework configuration (owned by the embedding framework)
  access-control-list.conf.yaml  # authorization grants (actors -> roles -> privileges)
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
  contracts/          # generic harness schemas: the artifact base contract, the journal contract,
                      # one <name>.conf.schema.json contract per configuration file, and
                      # functions/<function>.io.schema.json — one I/O contract per harness function
  src/
    application.py    # the composition root (builds the object graph, dispatches to one command)
    commands/         # usage entry points: argparse dispatch
    services/         # domain logic in family subpackages: step_resolution/ model_resolution/ checking/ injection/ hooks/ (each with its result dataclasses)
    models/           # persisted dataclasses (Artifact, Log, LogEntry, Report)
    workspace/        # workspace data access (the Workspace facade: artifacts + journals)
    config/           # ConfigLoader + the configuration dataclasses (from conf/, .env, adapters)
    utils/            # domain-free mechanics: .env/YAML/JSON(L) loading, JSON Schema validation
```

## Configuration plane

Every configuration source has a contract and a configuration dataclass (in `src/config/`,
beside the `ConfigLoader` that builds it):

| Configuration | Contract | Typed view |
|---|---|---|
| `.env` layout environment | required-variable set (below) | `FrameworkLayout` |
| `conf/access-control-list.conf.yaml` | `access-control-list.conf.schema.json` | `AccessControlList` |
| `conf/model-profiles.conf.yaml` | `model-profiles.conf.schema.json` | `ModelProfiles` |
| `conf/workspace.conf.yaml` | `workspace.conf.schema.json` | `WorkspaceLayout` |
| `conf/workflows/*.workflow.conf.yaml` | `workflow.conf.schema.json` | `WorkflowCatalog` / `Workflow` / `Step` |
| `harness/adapters/<env>/tools.yaml` | `adapter.conf.schema.json` | adapter binding (internal config) |

The framework's **layout is environment, not file configuration**: the framework declares WHERE
its pieces live via environment variables, loaded from a `.env` file at the framework root
(process environment takes precedence — CI and containers override without editing files):

```bash
# .env — framework layout (paths relative to the framework root)
FRAMEWORK_SKILLS_DIR=.github/skills
FRAMEWORK_AGENTS_DIR=agents
FRAMEWORK_SCHEMAS_DIR=schemas
FRAMEWORK_TEMPLATES_DIR=templates
FRAMEWORK_WORKSPACE_ROOT=../safe-agentic-portfolio   # default; --workspace-root overrides
```

`FrameworkLayout` is the dataclass over these variables — validated fail-fast like any other
configuration source (every variable present, every path existing — function 13, invariant 4).
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

## Logging

One append-only JSONL file per workflow instance:
`<workspace>/workflow-instances/workflow-instance-<workflowSlug>-<workflowInstanceId>.jsonl`

Every entry names the harness FUNCTION that produced it in its `command` field, with a shared
envelope (workflowInstanceId / stepSlug / artifact / actor / status / **report**) and a typed
per-function payload (the
function's `out` object, per its I/O contract). Every entry carries a `report`: the
human-readable one-line account of what the invocation concluded — no silent entries. The file
is the complete, ordered, replayable history of one workflow instance: every resolution, every
check, every injection, every authorization decision, every revert — auditable end to end. The
log is the audit and replay trace, but not the branching input: `resolve-step` reads
only journaled step outcomes; every other check recomputes from workflow definitions plus
current artifacts.

---

# Implementation

## Python conventions

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

## SOLID

- **S**ingle responsibility — one reason to change per class; the re-implementation target is a
  1:1 function → service → command alignment (each service realizes exactly one harness
  function).
- **O**pen/closed — behavior extends through configuration (workflows, catalog, ACL, adapter
  bindings), never by modifying the engine.
- **L**iskov substitution — every typed view and entity honors its base contract (`Artifact`
  subtypes are substitutable wherever an artifact is read).
- **I**nterface segregation — small, intent-named service surfaces (`resolve`, `check`,
  `inject`, `validate`); no god objects.
- **D**ependency inversion — services depend on constructor-injected collaborators; only the
  composition root knows the concrete wiring.

## TDD

Red–green–refactor against the functional contract: each function's Invariants, Preconditions,
Postconditions, and Interface are the test oracle. Write the failing test from the contract
clause first, then the code that satisfies it. A contract clause without a test does not exist;
a code branch justified by no clause is a candidate for deletion.

## Unit testing

`harness/tests/unit/` is a structural mirror of `harness/src/`: one test module per src module
(`tests/unit/<package>/test_<module>.py` ↔ `src/<package>/<module>.py`), one test class per src
class. Isolation comes from the constructor-injection convention — collaborators are replaced
by fakes and tmp-dir workspaces, never by monkey-patching internals.

## Functional testing (proposal)

`harness/tests/functional/` — one test module per harness FUNCTION (thirteen), exercising the real
command entry point over a fixture framework configuration and a fixture workspace:

- each test asserts the full Interface (In → Out), the Postconditions (exact journal entries
  appended; workspace untouched — or reverted for function 6), and the invariants observable
  from outside;
- journal replay tests: re-running against a golden instance journal must be byte-stable — the
  determinism check;
- CI runs both suites on every push; functions 12 and 13 additionally run as pipeline gates on
  the workspace and framework repositories respectively.

## Installation

Render the GitHub Copilot hook registration into the repository `.copilot` folder:

```bash
make -C harness install-copilot-hooks
```

That command renders `harness/adapters/github-copilot/hooks.yaml` into `.copilot/hooks.json`.

## Validation surface

Use the harness make targets for deterministic validation.

```bash
make -C harness verify
make -C harness check-catalog
make -C harness full
```

`verify` runs the unit and functional suites. `check-catalog` runs the workflow-catalog subset
of function 13 (`check-configuration`); `full` chains both.
