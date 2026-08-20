# TDD execution plan — harness core and `vscode-github-copilot-chat` adapter

## 1. Purpose and standing directives

This plan governs the multi-agent, test-driven implementation of two things at once: the
harness core specified in [`core/spec.md`](core/spec.md) — twelve functions, five packages,
one dependency direction — and the host binding specified in
[`adapter/vscode-github-copilot-chat/spec.md`](adapter/vscode-github-copilot-chat/spec.md).
It is the shared contract between the pairs working those two lanes in parallel: what the
oracle is, who challenges whom, in what order the work lands, and what "done" means at each
gate.

The specs remain authoritative. Where this plan and a spec disagree, the spec wins and this
plan is the thing that gets corrected.

**STANDING DIRECTIVE — NO GIT NETWORK OPERATIONS.** `origin` on this machine is SSH and
interactive-passphrase-only: any network verb blocks on a passphrase prompt that an agent
cannot answer, and a blocked prompt poisons the terminal for every concurrent agent sharing
it. Therefore:

- no `push`, no `fetch`, no `pull`, no `clone`, no `ls-remote` — none of them, at any point,
  for any reason;
- local commits only; the branch simply accumulates ahead of `origin/main`;
- if a task genuinely appears to need the remote, stop and escalate to the user rather than
  attempting it.

Further standing directives, applying to every agent on every commit:

- **QA gate first, then commit.** The suite must pass before `git add`; a red suite is never
  committed around.
- **DCO sign-off is mandatory** — `git commit -s`, never a bare `git commit`.
- **Every commit body carries the attribution trailer:**

```text
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

- **Stage only your own lane.** Pairs run concurrently over one working tree; `git add -A` is
  forbidden. Name your paths. If the index is locked by a concurrent commit, pause briefly and
  retry once.
- **`python3`, never `python`** — the bare name is not guaranteed to exist on this machine.
- **Run pytest directly and unpiped** — `python3 -m pytest tests -q`. Piping Python or Maven
  output through `cat`/`tee`/`grep` destabilizes the WSL terminal and hides live progress.

## 2. The test oracle

The oracle is not invented by the pairs. It is the core spec's
[Outcomes, failure modes, and refusals](core/spec.md) — five normative rules whose stated
purpose is to make "every branch of every function testable from this specification alone."
Rule 5 is the rule that turns the spec into a test list, and it is quoted here verbatim
because every red test in this plan is derived from it:

> **5. The precondition classification rule.** Every precondition in this specification is one
> of: **(E) enforced** — the function checks it and **names its violation outcome inline**, in
> place, as part of the precondition itself; **(C) by construction** — guaranteed upstream
> (fail-fast configuration load, boundary normalization, physical file ordering), so the
> function carries no runtime branch for it and tests exercise the upstream guarantee instead;
> **(O) caller obligation** — outside the harness's assertion boundary (C2), documented but
> not asserted. TDD derives one red test per (E) violation, per invariant, per example — plus,
> for every function, the three uniform failure tests of rule 1.

### The three uniform failure tests

Rule 1 names four shared error statuses (`inquiry-error`, `state-error`,
`configuration-error`, `system-error`) and singles out three conditions that apply uniformly
and are therefore never restated per function. Every function's test class carries one test
for each:

1. **Contract validation of the inquiry** — `inquiry-error` with code `invalid-inquiry`
   (a non-slug `sessionId`, a missing `agent`). This one is pre-attribution: it produces no
   report and surfaces at the **command exit plane** (stderr, nonzero exit), like a crashed
   invocation. The unit-level assertion is the raised `InquiryError` carrying
   `code == "invalid-inquiry"`; the exit-plane behavior itself is asserted in Wave 3.
2. **The C8 refusal** — a session-bound call against a session whose log already carries an
   ending entry returns `state-error` with code `session-ended`, and journals nothing.
3. **`system-error`** — the environment fails (log append failure, unreadable log, Git index
   lock). The report is still returned; the entry is lost, best-effort.

One correction to the naive reading of "every function": the C8 test does **not** apply to all
twelve. The spec scopes it explicitly — "the C8 refusal (rule 3 — functions 0–10; function 11
is exempt, C8)". Function 11 is what *writes* the ending entry, so it cannot refuse on its
account. Functions 0–10 carry the C8 test; function 11 carries the other two.

### What is and is not asserted

- **(E) enforced** preconditions each get a red test asserting the exact status and
  `error.code` the spec names inline at that precondition.
- **(C) by-construction** preconditions get **no branch and no direct test** in the function.
  They are covered by testing the **upstream guarantee** instead — the fail-fast configuration
  load, boundary normalization, physical file ordering. A function-level test that manufactures
  a (C) violation is testing a branch the spec says must not exist.
- **(O) caller obligations** are **not asserted at all**. They lie outside the harness's
  assertion boundary (C2). Documented, never enforced.
- **Normative vs advisory.** Statuses, `error.code` values, and structured fields are the
  normative test surface. `error.message` and every `failureMessage` are advisory prose —
  assert their presence and the key facts they must name (a path, a privilege), never their
  exact wording. A test that pins prose is a test that will break on an editorial pass.

### The journaling matrix

The spec's default is 1 invocation = 1 entry. Four cases depart from it, each forced by the
absence of an open target log, and each is its own assertion — the test must check both the
returned report *and* the log's byte length:

| Outcome | Report returned | Journaled | Why |
| --- | --- | --- | --- |
| `not-applicable` (functions 0, 4, 5, 10) | Yes | **Never** | Persisted state names no target; there is no session log it belongs to. The one explicit exception to 1 invocation = 1 entry. Hook planes render it as pass-through (C7). |
| C8 refusal (`state-error` / `session-ended`) | Yes | **Never** | No entry ever follows the ending entry (function 11, invariant 1). The only `state-error` that does not journal. |
| `inquiry-error` / `session-unregistered` | Yes | No | The report is constructible — the id is a valid slug — but there is no log to journal to. |
| `inquiry-error` / `invalid-inquiry` | **No report at all** | No | A contract-valid report cannot be built when the inquiry's own `sessionId` is missing or malformed. Surfaces at the command exit plane. |

Every other error outcome — including `configuration-error` and `system-error` — is an
ordinary outcome and journals to the attributed session's log. A completed invocation whose
log append fails still returns its report and surfaces `system-error`.

## 3. Multi-agent execution model

### Roles

Roles are the framework's own agents, in
[`../saf-agentic-organization/agents/`](../../saf-agentic-organization/agents/):

| Agent | File | Role in this plan |
| --- | --- | --- |
| `developer` | `developer.agent.md` | Driver and Navigator in the pair micro-cycle; writes the red test, writes the implementation, reviews the diff. |
| `quality-engineer` | `quality-engineer.agent.md` | The QA gate on every commit and the full-suite pass in Wave 5. Blocks forward motion on real, unresolved risk. |
| `system-architect` | `system-architect.agent.md` | Deadlock arbiter during execution; the architecture gate at the end. |
| `product-owner` | `product-owner.agent.md` | The traceability gate at the end. |

### The pair protocol — ping-pong with adversarial challenge

One cycle, roles swapping at the end of each:

1. **Driver writes the RED test**, citing the spec clause it comes from in the test docstring.
   The test must fail for the stated reason before anything else happens.
2. **Navigator CHALLENGES — before any implementation exists.** This is the load-bearing step;
   it is adversarial by design, and "looks fine" is not an acceptable response. Four standing
   challenges:
   - *Is this the right status and error code?* Rule 1 assigns status by kind, never by
     per-function taste. A plausible-but-wrong code is the most common defect here.
   - *Does it respect the journaling matrix?* Does the test assert the log length as well as
     the report? A `not-applicable` or C8 test that never checks that the log stayed untouched
     is asserting half the clause.
   - *Has the oracle drifted from the spec's wording?* Is the test pinning advisory prose, or
     asserting a branch that rule 5 classifies as (C) or (O)?
   - *Is the fixture contract-valid?* A fixture that violates the very contract it feeds is a
     test that passes for the wrong reason — or fails at journaling time for a reason that has
     nothing to do with the clause under test.
3. **Driver implements to green** — the minimum that satisfies the clause, nothing speculative.
4. **Navigator reviews the diff** for minimality (no unrequested abstraction, no branch without
   a clause) and for the Python conventions of the core spec: frozen dataclasses, verb+subject
   method names, full typing, `__all__`, no import-time side effects, constructor injection.
5. **Roles swap.** The Navigator drives the next cycle.

A pair that cannot converge — genuine disagreement about what a clause requires, not a
preference — **escalates to the `system-architect`** rather than picking a reading and moving
on. The architect's ruling is recorded in the commit body of the cycle that resolves it.

### The QA gate — every commit

The `quality-engineer` runs, and records the outcome in the commit body:

- the full suite, directly and unpiped: `python3 -m pytest tests -q`;
- a traceability audit — every new test carries a docstring citing its spec clause;
- fixture contract-validation — every fixture used as function I/O validates against its own
  contract;
- absence of suppressions — no `# type: ignore`, no blanket `except Exception`, no silenced
  warning that hides a real defect (root-cause-first: fix the cause, and if suppression is
  genuinely unavoidable, scope it to one statement with a one-line reason);
- conventions — the Python conventions and SOLID rules of the core spec's Development section.

### The final gates — once all tests exist

**`system-architect` gate.** Source-layout conformance against the core spec, in its own words:
"Five packages, one dependency direction — `commands → services → {stores, config}`, with
`utils` beneath everything". Concretely: `utils` is domain-free and imports nothing internal;
`stores` and `config` do not import `services`; `services` do not import `commands`; the SOLID
rules hold, in particular the 1:1 function → command and function → service alignment
(`SessionLifecycle` being the single deliberate exception — functions 0 and 11 open and close
the same file). And the adapter rule **I15 — "the adapter's only dependency is the command
API"**: the adapter holds no dependency on `services`, `stores`, or `config`, not even for its
own binding, which it loads with its own tools rather than `ConfigLoader`.

**`product-owner` gate.** A traceability audit against the spec's own TDD rule: *a contract
clause without a test does not exist; a code branch justified by no clause is a candidate for
deletion.* Walk every function's Interface, Preconditions, Postconditions, and Invariants, plus
every worked example, and produce a clause → test map. Unmapped clauses are defects. Unmapped
branches are deletion candidates.

## 4. Waves

Parallelism is bounded by dependency, not by ambition. A wave starts when the previous wave's
QA gate has passed.

### Wave 0 — plan and scaffolding

This file, the test package scaffolding (`tests/unit/` mirroring `src/`, `conftest.py`
placement, path setup), and the shared pytest configuration. One pair.

### Wave 1 — three pairs in parallel

- **Pair A — foundations.** `errors.py` (the error model as code: `HarnessError` plus one
  subtype per status), `utils/` (env/JSON/JSONL/YAML/markdown loaders, `SchemaValidator`,
  `JsonlStore`), `config/ConfigLoader` (parse + contract-validate + semantic rules, fail-fast),
  and the two stores (`artifact_store/`, `session_log_store/`). Everything else depends on
  this, so it goes first and alone in its lane.
- **Pair B — adapter unit TDD.** `HookBinding`, `HookClassifier` + `EventClass`,
  `SessionTracker`, `Adapter`, `HookRenderer`, and the command-runner port. This runs in
  parallel with Pair A **precisely because of I15**: the adapter's only dependency into the
  core is the command API, so it can be driven end to end against a **fake command runner**
  long before a single command class exists. That is the architectural rule paying for itself
  in schedule.
- **Pair C — fixture and oracle library**, plus a **cel-python capability spike**. The shared
  fixture builders (framework configuration, workspace, session logs) and the assertion
  helpers that encode the journaling matrix once instead of thirty times. The spike answers
  what `cel-python` can and cannot evaluate for the condition language before Wave 2's
  checkers commit to a design.

### Wave 2 — three pairs in parallel

- **Pair A — the session and context plane.** `SessionLifecycle` (functions 0 and 11 — one
  service, two functions, one file) together with the four context resolvers: functions 1
  (`resolve-workflow-instructions`), 2 (`resolve-workflow-skills`), 6
  (`resolve-step-instructions`), 7 (`resolve-step-skills`).
- **Pair B — the resolution plane.** `StepResolver` (function 3) with `StepModelResolver`
  (function 4). Paired because function 4's `not-applicable` branch is defined by exactly the
  in-flight-step state function 3 creates.
- **Pair C — the checking plane.** `ConditionEvaluator` with the pre/postcondition checkers
  (functions 5 and 10), `StepAuthorizationChecker` (function 8), and `StepArtifactChecker`
  (function 9). Function 9 is exercised over **real temporary Git repositories** — staging,
  commit, and revert are the behavior under test, and a mocked Git plane would assert nothing.

### Wave 3 — one pair, at the integration point

The twelve command classes, the `application.py` composition root (which builds the object
graph and dispatches argv), and the `harness.py` wiring. Deliberately **not** parallel: this is
the one place where every lane's object graph meets, and concurrent edits to a composition root
are merge pain with no schedule payoff.

The distinguishing assertion of this wave is the exit plane: `invalid-inquiry` **exits at the
command plane** — stderr plus a nonzero status — and produces **no report**, exactly like a
crashed invocation. That behavior is unobservable at the service level and can only be tested
here.

### Wave 4 — two to three pairs in parallel

- **Functional suite, functions 0–5** and **functions 6–11** (one module per function,
  splitting the twelve across two pairs): the real command entry point over a fixture framework
  configuration and a fixture workspace, asserting contract-validated I/O, the full Interface
  (In → Out), the Postconditions (exact log entries appended; workspace untouched — or the
  staged write committed / discarded for function 9), the externally observable invariants, and
  the C0–C8 scenarios. Plus **golden-fixture log replay**: re-running against a golden fixture
  set of session logs must be "byte-stable per log and merge-stable per instance view — the
  determinism check".
- **Adapter functional tests** end to end through `dispatch.sh`: real stdin envelopes in, real
  stdout decision objects out, both validated against the adapter's own seam-1 and seam-4
  contracts.

One caveat to carry into this wave: the core spec titles its functional-testing section
**"Functional testing (proposal)"**. Its layout and assertions are therefore the least settled
part of the oracle. Treat divergence found here as a spec question for the `system-architect`,
not as a licence to improvise.

### Wave 5 — gates

`quality-engineer` full-suite pass, then the `system-architect` gate, then the `product-owner`
gate, in that order. Findings from any gate re-enter as ordinary pair cycles.

## 5. Test layout and conventions

`tests/unit/` is a **structural mirror of `src/`**:

```text
tests/unit/<package>/test_<module>.py   ↔   src/<package>/<module>.py
```

One test module per source module, **one test class per source class**. The mirror is the
navigation aid and the coverage map at once — a source module with no facing test module is
visible at a glance.

The other three suites:

- `tests/functional/` — one module per harness function (twelve), over the real command entry
  point.
- `tests/unit/adapter/` — the adapter's class-level suite (`HookBinding`, `HookClassifier`,
  `EventClass`, `SessionTracker`, `Adapter`, `HookRenderer`, command runner).
- `tests/adapter/` — the adapter's **host-binding** suite: the hook map, the adapter
  configuration, the dispatch funnel, and the two seam contracts. These assert the declarative
  binding data and its contracts, not Python behavior.

### Isolation

Isolation comes from **constructor injection with fakes and temporary-directory workspaces**.
The core spec is explicit and prohibitive on the alternative: collaborators are replaced by
fakes and tmp-dir workspaces, "never by monkey-patching internals."

**Local convention, called out as such:** monkeypatching the **process environment**
(`monkeypatch.setenv` / `monkeypatch.delenv`) *is* used, in the configuration-layout precedence
tests, where the thing under test is precisely how the loader reads `.env` and environment
variables. This is a team convention adopted for that narrow case, not something the spec
sanctions. It patches the environment, never a harness internal, and it must not spread beyond
layout-precedence tests.

### Docstrings

Every test method carries a docstring citing the spec clause it exists to satisfy — the
function number and the clause ("Rule 1/4", "invariant 3", "precondition (E) 2", "C8"). This is
what makes the `product-owner` traceability gate mechanical rather than archaeological.

## 6. Current status — 2026-08-21

Verified directly: `git log --oneline -8` and `python3 -m pytest tests -q` (352 passed).

```text
0ce213f feat: vscode-github-copilot-chat adapter — classification, tracking, orchestration, rendering
5d29f15 feat: harness services — session lifecycle, context, step, model, and checking
d97537d feat: configuration plane — fail-fast ConfigLoader and typed configuration views
5adc337 feat: artifact store — valid-by-construction workspace reads over committed state
2d1c26c feat: harness foundations — errors, utils, and the session log store
3dd1d9b Refactor artifact check handling in harness
24524f5 refactor: align contracts and class design — inquiry/report envelopes, typed I/O, canonical refs
f2ff248 refactor: update sibling reference to saf-agentic-organization
```

`3dd1d9b` is `origin/main`; the five commits above it are local and unpushed, per the standing
directive.

| Wave | State |
| --- | --- |
| 0 — plan and scaffolding | Complete |
| 1 — foundations, adapter units, fixtures | Complete (`2d1c26c`, `5adc337`, `d97537d`, `0ce213f`) |
| 2 — services for all twelve functions | Complete (`5d29f15`) |
| 3 — commands, `application.py`, `harness.py` wiring | **In flight** — `src/commands/` and `src/application.py` do not yet exist |
| 4 — functional suites | **Not started** — `tests/functional/` does not yet exist |
| 5 — gates | Not started |

Landed across the five local commits: `errors.py`, `utils/`, `stores/session_log_store/`,
`stores/artifact_store/`, `config/` (fail-fast `ConfigLoader` plus the typed configuration
views), the services for all twelve functions (`session_lifecycle`, `context_resolution`,
`step_resolution`, `model_resolution`, `checking`), and the adapter (`HookBinding`,
`HookClassifier` with `EventClass`, `SessionTracker`, `Adapter`, `HookRenderer`, command
runner).

Suite total: **352 passing**, across `tests/unit/{utils,config,stores,services,adapter}` and
`tests/adapter/`.

## 7. Known gaps and risks

Recorded honestly — every item below is real and currently open.

**Traceability gap — blocks the `product-owner` gate.** Several test modules carry no
per-test spec citation, so the clause → test map cannot be built mechanically for them:

- `tests/unit/stores/session_log_store/test_session_log_store.py` — 13 tests, **0** docstrings;
- `tests/adapter/` — all four modules (`test_hooks_map.py`, `test_adapter_conf.py`,
  `test_dispatch_funnel.py`, `test_hook_contracts.py`), 21 tests, **0** docstrings;
- `tests/unit/stores/artifact_store/test_artifact_store.py` — 12 tests, **3** docstrings
  (a partial gap, in addition to the two named above).

A secondary inconsistency: `tests/unit/utils/` and `tests/unit/config/` use a literal `Spec:`
docstring prefix, while `tests/unit/services/` and `tests/unit/adapter/` cite the clause without
it ("Rule 1/4: …"). Both cite; only one form is greppable. Settle on one before the gate.

**Two commits landed without peer review.** `5d29f15` (services) and `0ce213f` (adapter) were
committed outside the pair protocol — no navigator challenge, no diff review. Both are under a
**catch-up `system-architect` review**. Until it closes, treat their content as unratified: a
finding against them is a normal correction, not a regression.

**The `Makefile` `verify` target is stale.** It still references `tests/integration/test_catalog.py`
(via `check-catalog`, which the `verify` documentation describes as part of the gate) and
describes a pre-refactor CLI shape (`check-artifact` / `check-step`) that the twelve-command
surface replaced. `tests/integration/` does not exist. Fixing it belongs to Wave 3 or later —
the `Makefile` is explicitly outside this document's lane.

**Adapter open items I13(a) and I13(d) are out of test scope.** (a) whether a steering message
submitted mid-execution fires `UserPromptSubmit` on the native panel path, and (d) hooks being
a preview feature whose setting names and event set may drift. Neither is closable by a test —
(a) needs live host experimentation, and (d) is a drift risk whose intended detector is the
fail-fast adapter-binding validation at instantiation, not a unit test. Do not write tests that
pretend to resolve them.

**Process traps hit during execution** — both cost real time, both have the same mitigation:

- **Subagent dispatch can fail on a plan usage limit.** A delegated pair simply does not start.
- **A GitHub API outage can block delegated sessions from authenticating.** Same symptom,
  different cause.

In both cases the work continues locally, in-session, under the same protocol — the pair
collapses to a single agent playing both roles sequentially, and the navigator challenge is
written out explicitly in the commit body so the review is still auditable. Neither trap is a
reason to reach for the network.
