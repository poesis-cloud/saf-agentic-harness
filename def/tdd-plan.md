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

The other suites:

- `tests/functional/` — one module per harness function (twelve), over the real command entry
  point, plus `test_log_replay.py` (golden-fixture log replay).
- `tests/functional/adapter/` — the adapter end to end, one module per hook (H0–H7) plus the
  dispatch plumbing.
- `tests/unit/adapter/` — the adapter's class-level suite (`HookBinding`, `HookClassifier`,
  `EventClass`, `SessionTracker`, session-id derivation, `Adapter`, `HookRenderer`, command
  runner).
- `tests/adapter/` — the adapter's **host-binding** suite: the hook map, the adapter
  configuration, the dispatch funnel, the two seam contracts, and the hook / agent-hook /
  settings rendering. These assert the declarative binding data, its contracts, and the
  rendered output, not adapter Python behavior.

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

## 6. Current status — 2026-11-04

Release **v1.0.0-alpha.1** is in preparation. Verified directly against the tree at
`ead92a3` (the merge of PR #10, `chore/architecture-alignment`) with
`python3 -m pytest --collect-only -q tests/<plane>` and `make verify`.

| Wave | State |
| --- | --- |
| 0 — plan and scaffolding | Complete |
| 1 — foundations, adapter units, fixtures | Complete |
| 2 — services for all twelve functions | Complete |
| 3 — commands, `application.py`, `harness.py` wiring | Complete — `src/commands/` holds one command + inquiry class per function (twelve each), `src/application.py` is the composition root, `harness.py` the entry shim |
| 4 — functional suites | Complete — `tests/functional/` covers all twelve functions plus golden log replay; `tests/functional/adapter/` drives H0–H7 and the dispatch plumbing end to end |
| 5 — gates | Open — see [§7](#7-known-gaps-and-risks) for what still blocks the `product-owner` gate |

Implementation is complete: all twelve harness functions (0 `start-session` … 11
`end-session`) and the `vscode-github-copilot-chat` adapter (`HookBinding`, `HookClassifier`
with `EventClass`, `SessionTracker`, session-id derivation (`session_identity.py`), `Adapter`,
`HookRenderer`, command runner, `adapters/dispatch.sh`, and the `adapters/render_hooks.py`
installer behind `make install-hooks`).

Test planes — all implemented and green under `make verify` (counts are a snapshot at the
time of this refresh; `make verify` is authoritative):

| Plane | Location | Tests |
| --- | --- | --- |
| Unit — core | `tests/unit/{utils,config,stores,services,commands}`, `test_application.py`, `test_errors.py` | 447 |
| Unit — adapter | `tests/unit/adapter/` | 156 |
| Functional — core | `tests/functional/` (one module per function, plus `test_log_replay.py` and `test_unreadable_logs.py`) | 189 |
| Functional — adapter | `tests/functional/adapter/` (H0–H7, dispatch plumbing, hook bootstrap) | 65 |
| Adapter conformance | `tests/adapter/` (hook map, adapter configuration, dispatch funnel, hook contracts, hook / agent-hook / settings rendering) | 85 |
| **Total** | `tests/` | **942** |

## 7. Known gaps and risks

Recorded honestly — every item below is real and currently open unless marked resolved.

**Spec ahead of implementation — step transactionality.** The core spec now makes the
**step instance** (one execution of a step, identified by its step session id) the unit of
acceptance: function 9 commits each validated set onto the step instance's local-only **step
ref** (`refs/harness/steps/<workflowInstanceId>/<stepSlug>/<stepSessionId>`) that function 0
creates and checks out, and function 10 evaluates the **step state** and, on pass,
**promotes** it by fast-forwarding the workspace branch — so workspace state only ever holds
the output of step instances that passed function 10 (core spec: Terminology, C0, C1, C6,
functions 0, 5, 8, 9, 10,
Outcomes rule 1, Internal validation, Workspace Git plane, Logging). Nothing below is
implemented yet; the implementation change follows the spec's approval and owes, each with
its red tests first:

- contracts — `promotion` (`{outcome: promoted | withheld, workspaceCommit}`) on
  `check-step-postconditions.output.schema.json`; the two new normative codes
  `workspace-not-clean` and `step-rebase-conflict` in `report.schema.json`'s code list;
- function 0 — step-instance ref creation and checkout at step-session registration (from
  the previous step instance's ref tip on an unchanged base, else from the workspace branch
  tip; no second ref on replay; a failed checkout is `system-error`) — invariant 6;
- function 5 — the clean-working-tree precondition (`workspace-not-clean`) and, on pass, the
  return of the working tree to the workspace branch (which also covers crash recovery);
- function 8 — invariant 5 against the checked-out step ref, and invariant 7 (no step
  instance in flight with its own ref checked out → `denied`);
- function 9 — commit onto the step ref; vacuous validity against the step ref tip;
- function 10 — rebase, delivery over the whole step state plus the step session's own
  commits (invariant 5), evaluation on the step state, promotion as one compare-and-swap
  fast-forward (the acceptance point; no checkout or ref deletion after it) or withholding,
  step-instance refs kept (invariants 6–7);
- `ArtifactStore` — the step-ref Git operations, reads of the step state, the logs path in
  `.git/info/exclude`, and the sweep reporting step refs as step instances; the class diagram
  (`harness-src-classes.puml`, note already updated) gains them and
  `CheckStepPostconditionsReport.promotion`, and its PNG is re-rendered
  then (the committed PNG is a crop that does not show `artifact_store`);
- adapter specs — restatements of the old commit flow (e.g. `vscode-github-copilot-chat` H5
  "state advanced by the validated commit", I6 "unmediated writes never commit") are aligned
  with the core when the adapters are.

**Resolved since 2026-08-21.** The `Makefile` is no longer stale: `verify` runs the whole
pytest suite, `unit` / `functional` / `adapter` run one plane each, `test` aliases `verify`, and
the `check-catalog` / `tests/integration/` references are gone.

**Traceability gap — blocks the `product-owner` gate.** Most of the original gap is closed —
`tests/unit/stores/session_log_store/` and every `tests/adapter/` module now cite a clause per
test. What remains without a per-test docstring (11 of the suite's test functions):

- `tests/unit/stores/artifact_store/test_artifact_store.py` — 20 tests, **11** docstrings
  (a partial gap);
- `tests/unit/utils/test_named_file.py` — 2 tests, **0** docstrings.

A secondary inconsistency, still open: `tests/unit/utils/` and `tests/unit/config/` use a
literal `Spec:` docstring prefix, while `tests/unit/services/` and `tests/unit/adapter/` cite
the clause without it ("Rule 1/4: …"). Both cite; only one form is greppable. Settle on one
before the gate.

**Two commits landed without peer review.** `5d29f15` (services) and `0ce213f` (adapter) were
committed outside the pair protocol — no navigator challenge, no diff review. Both have since
landed on `main`, but the repository records no close-out of the **catch-up `system-architect`
review**; it stays open until the Wave 5 `system-architect` gate ratifies them. Until then, a
finding against them is a normal correction, not a regression.

**Adapter open items I13(a)/(d) remain open — live-host verification only.** They are
the permanently tracked items in
[`adapter/vscode-github-copilot-chat/spec.md`](adapter/vscode-github-copilot-chat/spec.md).
I4 is closed: both adapters now run functions 3–4 from their own `PreToolUse`/`preToolUse`
hook with the payload's session and never edit an agent's command, so the core's former
tool-boundary-stamp exception is retired and nothing tracks it any more.
I13(a) and I13(d) are out of test scope: (a) whether a steering message
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
