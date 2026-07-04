# Deterministic Harness

The harness is the deterministic execution core of the agentic framework. It owns the workflow graph, step sequencing, gate staging, authorization checks, model routing, context injection, and run logging. Agents stay limited to the irreducible work: generating content, judging within a step, conversing with the human, and actuating host tools.

The harness is methodology-agnostic and host-agnostic: it knows nothing of SAFe, Scrum, or any other methodology, and nothing of GitHub Copilot, VS Code, or any other host. It consumes generic workflow definitions, artifact schemas, an ACL, and an LLM routing map supplied by the framework that embeds it. Any methodology-specific concepts live in the framework's skills, artifact schemas, and workflow configs; any host-specific concepts live in the adapter under `harness/adapters/<env>/`.

This document is the canonical harness description. It replaces the older split between the root harness README and the environment-hooks README.

## Terminology

The harness operates on a **workspace** — the writable data plane that contains artifacts, logs, and state. The default on-disk folder name remains `portfolio/` in this framework for backward compatibility, but the harness code and contracts refer to it generically as the workspace. A framework may mount the workspace at any path via `--workspace-root`.

## Functional role

The harness is check-only and artifact-driven.

- It computes what happens next from workflow definitions plus current artifacts.
- It validates step preconditions, postconditions, and written artifacts.
- It mediates all host hook events so the host adapter never becomes a second source of truth.
- It records one journal entry per harness command, making each orchestration run observable and replayable.

The harness never authors business artifacts itself. Status transitions, gate decisions, and authored deliverables are all produced by dispatched agents and then checked by the harness. The one deliberate exception is enforcement: the postcondition hook may **revert** an invalid write (restore the last-good version or delete the new file) to preserve the Workspace Validity Invariant (C6) — it never authors content.

## Invariants

The harness enforces the following invariants.

### C0 - Workspace state definition

Workspace state is the union of:

- Persisted workspace artifacts (business, governance, and orchestration deliverables under workspace state paths).
- Harness logs (run journals and hook streams) persisted under the workspace logs paths.

Both are first-class state for deterministic checks and replay.

### C1 - Workspace-state scope

All preconditions and postconditions are evaluated strictly against persisted workspace state.

### C2 - Harness assertion boundary

Condition checks executed by the harness only assert workspace state. The harness does not assert private agent memory or transient, non-persisted host context.

### C3 - Agent sourcing freedom

The actor agent may source from workspace data, external systems, tools, or web context. Harness pass or fail is based only on persisted workspace state.

### C5 - Schema-bound persisted artifacts

Every persisted artifact used in condition evaluation must be cataloged and schema-bound. The selector model has one selector type: selecting persisted workspace artifacts.

Where a condition depends on log evidence, that evidence is read from persisted harness logs (part of workspace state) and asserted as state; logs are not business artifacts.

### C4 - Methodology and host agnosticism

The harness is generic: it is not hard-coded to any methodology (SAFe, Scrum, etc.), any host environment (GitHub Copilot, VS Code, etc.), or any specific artifact taxonomy. Methodology-specific semantics are supplied by the embedding framework through its workflow configs, artifact schemas, skills, and templates. Host-specific event mapping is supplied by the adapter under `harness/adapters/<env>/`. The harness only interprets the generic primitives it is given: workflow graphs, conditions, artifact schemas, ACL grants, and LLM routing rules. This keeps the harness reusable across frameworks and host integrations.

### C6 - Workspace validity

The workspace contains exclusively schema-valid artifacts and valid state, at all times.

- Maintained at the write boundary. The postcondition hook validates each agent write against its artifact schema and, if invalid, reverts it (restore the last-committed version if the path is tracked, else delete the new file) and denies with the schema findings so the agent retries. This revert is the harness's single deliberate write.
- Relied on by every reader. The artifact repository is valid-by-construction: `discover()` raises rather than yield a schema-invalid artifact, so no domain code (state selection, step checks, orchestration) ever operates on an invalid artifact. The validators that must enumerate invalids (`check-artifact`, the hook) read the raw universe (`scan_raw`) instead.
- Enables safe asynchronous / remote workspace sync. A synced replica is trustably valid; reconciliation only has to preserve validity, not re-derive it.

### C7 - Workspace terminology

The harness refers to the state plane as the **workspace**, not the portfolio. A portfolio is one possible methodology-specific organization of a workspace; the harness must remain agnostic to such organizations.

## Two trigger planes, one command system

Every harness behavior is exposed as a harness command. The same command system is entered from two places.

- Hook-triggered commands: the host calls the harness on lifecycle events such as `sessionStart`, `preToolUse`, `postToolUse`, and `sessionEnd`.
- Agent-triggered commands: the orchestrator agent calls the harness drive loop with `orchestrate`, plus targeted checks such as `check-step` and `check-artifact`.

There is no separate hook logic outside the harness. The adapters only forward host events and render the harness decision back into the host format.

## Core commands

| Command | Trigger | Purpose | Output |
|---|---|---|---|
| `orchestrate <workflow-id>` | orchestrator agent | Resolve the next workflow action from artifacts and workflow state | `dispatch`, `halt`, or `done` |
| `check-step` | orchestrator or hook flow | Evaluate a step's preconditions and postconditions | typed report |
| `check-artifact` | post-write validation | Validate the written artifact against schema and state rules | typed report |
| `hook --event <name> --env <env>` | host adapter | Normalize a host lifecycle event and route it through the deterministic checks | typed report / host decision |

## Hook adapter layout

The dispatch script is shared and generic across every host; only the per-host registration and
tool binding live under each adapter's own subfolder. See `harness/adapters/README.md` for the full
adapter contract (adding a new host, etc.).

```text
conf/                 # env-agnostic framework configuration (owned by the embedding framework)
  access-control-list.conf.yaml  # authorization grants (actors -> roles -> privileges)
  workspace.conf.yaml            # workspace layout blueprint (nodes: path -> schema/template/cardinality)
  model-profiles.conf.yaml       # canonical model catalog: capability_scores + cost_rank per model
  workflows/                     # *.workflow.conf.yaml — steps declare actor, kind, and weighted capabilities
harness/
  adapters/
    dispatch.sh       # shared, generic dispatcher: stdin JSON -> harness hook command (every adapter calls this)
    github-copilot/
      hooks.yaml      # YAML source rendered to .copilot/hooks.json
      tools.yaml      # host tool names, write verbs, payload keys
      models.yaml     # host model id bindings to canonical profiles
  contracts/          # generic harness schemas: the artifact base contract, the journal contract,
                      # and one <name>.conf.schema.json contract per conf/ configuration file
  src/
    config/           # the configuration plane: one typed view per conf file + the shared loader
    models/           # workspace domain entities (Artifact, Log, Section, Finding, Report)
    mappers/          # workspace data access (Workspace, ArtifactMapper, LogMapper)
    services/         # domain logic (checkers, CEL, authorization, routing, orchestration, hooks)
    cli/              # the composition root + argparse dispatch
```

## Configuration plane

Every `conf/` file has a contract schema in `harness/contracts/` and a typed view class in
`harness/src/config/`:

| Configuration | Contract | Typed view |
|---|---|---|
| `conf/access-control-list.conf.yaml` | `access-control-list.conf.schema.json` | `AccessControlList` |
| `conf/model-profiles.conf.yaml` | `model-profiles.conf.schema.json` | `ModelProfiles` |
| `conf/workspace.conf.yaml` | `workspace.conf.schema.json` | `WorkspaceLayout` |
| `conf/workflows/*.workflow.conf.yaml` | `workflow.conf.schema.json` | `WorkflowCatalog` / `Workflow` / `Step` |

Parsing and validation are one act: `ConfigLoader` parses the YAML and validates it against the
contract in the same step — an unvalidated parse never escapes the config package. `FrameworkConfig`
aggregates the four views and is built once at `Application` initialization, so every interaction
with the harness (check, hook, orchestrate) fails fast — with the full findings report — on an
invalid configuration before any command logic runs.

The workspace content is the database the harness reads its domain entities from (`models/` via
`mappers/`); `conf/` is the framework's static configuration (`config/`). Workflows and steps are
configuration entities, not workspace entities — they live in the config package.

## Model Routing
Model routing is resolved from two static configuration layers, both read deterministically at
dispatch time (no artifact reads, no per-instance estimation):

1. **Step → `capabilities` (weighted, static)**. Each workflow step declares a weighted map of
   capability tag -> weight (0-10), all nine tags explicit — e.g. an architecture-review step
   weights `deep-reasoning: 9, coding: 4, …`. Authored once per step in
   `conf/workflows/*.workflow.conf.yaml`, exactly like `skills`. The step is the dispatch — its
   kind of work fixes both WHICH tags matter and HOW MUCH; SAFe's splitting discipline homogenizes
   per-unit complexity (stories sized to fit an iteration, features to fit a PI), so two instances
   of the same step carry the same weights. A human `gate` step weights every tag 0 — it is never
   dispatched to a model.

2. **Model catalog → `capability_scores` + `cost_rank` (static)**. `conf/model-profiles.conf.yaml`'s
   0-10 `capability_scores` per tag and `cost_rank` per model. The catalog owns the canonical tag
   vocabulary (`model-profiles.conf.schema.json#/definitions/capabilities`).

### The score
The score is a pure weighted capability sum:

$\text{Score}(m) = \sum_{\text{tag}} \text{capability\_score}_m[\text{tag}] \times \text{step.capabilities}[\text{tag}]$

Highest score wins; ties break toward lower `cost_rank`. Cost sensitivity emerges structurally:
steps with low, sparse weights compress the candidate scores into a narrow band where the
cheap-model tie-break dominates, while steps with high weights on discriminating tags let
capability differences dominate cost.

**Worked example** — a step with `capabilities: {deep-reasoning: 9}`, two models differing by 2
points on `deep-reasoning` (9 vs 7):

$\text{A} = 9 \times 9 = 81 \qquad \text{B} = 7 \times 9 = 63$

A wins on capability. With `capabilities: {deep-reasoning: 3}` the spread narrows
($27$ vs $21$) and, across a larger candidate set, the `cost_rank` tie-break routes toward
cheaper models on genuinely low-stakes steps.

A step with no positive weight (a gate) or an empty catalog is **unroutable**: `orchestrate`
returns `halt` with `reason: unroutable` rather than silently passing `Auto`. At the hook plane,
a `runSubagent` dispatch is denied unless its model is a known catalog id (never `Auto`, never
omitted). `OrchestrationService` resolves each dispatch's model via `ModelRouter` from the step's
`capabilities` and returns the binding in the dispatch payload (`routing: {model, score,
cost_rank, reason}`).

`dispatch.sh` is intentionally thin and env-agnostic: it takes the event name and the environment
id as its two arguments and forwards the raw event payload to `harness.py hook --event <name> --env
<env>`, exiting with the harness result. Each adapter's `hooks.yaml` supplies its own
environment id as the second argument.

## Event normalization

Host events are normalized to workflow concepts before any policy is applied.

| Host event | Normalized phase | Harness responsibility |
|---|---|---|
| `sessionStart` | `session-open` | inject deterministic context, verify step entry conditions |
| `userPromptSubmit` | `observe` | record observation only |
| `preToolUse` | `precondition` | authorize writes and validate dispatch route |
| `postToolUse` | `postcondition` | validate the produced artifact |
| `stop` / `sessionEnd` | `session-close` | review step exit conditions and session outcome |

This keeps the harness env-agnostic: only the adapter maps host-specific event names and tool payloads.

## Runtime model

One session corresponds to one structurant step. At session open, the harness correlates the session to the most recent open dispatch for the actor and injects the step-local context.

- Skills are injected per step, not globally per workflow.
- Instruction files are injected from the step's declared invariants.
- Model choice is resolved by the harness and only relayed by the agent into `runSubagent`.
- Authorization remains plain artifact-level RBAC over `<action>_<resource>` privileges.

## Logging

The run journal is append-only and command-granular.

- Per-run journal: `<workspace>/logs/<run>.jsonl` (default on-disk: `portfolio/logs/<run>.jsonl`)
- Per-session hook stream: `<workspace>/logs/hooks/<session>.jsonl` (default on-disk: `portfolio/logs/hooks/<session>.jsonl`)

Each harness command appends exactly one entry with a shared envelope and a typed payload. The journal is the audit and replay trace, but not the branching input. Sequencing is always recomputed from workflow definitions plus current artifacts.

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

`verify` runs the constitution tests. Runtime artifact validation remains in the CLI and hook path rather than the static verification suite.
