# Agentic Harness

The harness is the deterministic execution core of an agentic framework: it resolves steps,
models, and agents' session context (instructions and skills — injected at session open),
checks steps' conditions, authorization, and artifacts, and logs all of it —
deterministically, from persisted state and validated configuration only. The harness core
is fully host-agnostic; host-specific bindings live in separate adapter specifications.

The canonical harness-core specification — terminology, invariants, the eleven-function
contract, design, and implementation — lives in **[`def/core/spec.md`](def/core/spec.md)**.
Read that first; this file is
only an orientation pointer. Each host binding has its own specification under
`def/adapter/<host>/spec.md` (e.g.
[`def/adapter/vscode-github-copilot-chat/spec.md`](def/adapter/vscode-github-copilot-chat/spec.md)).

## Layout

- `def/core/` — the harness-core specification (`spec.md`) and its diagrams:
  `harness-functions.puml` (the sequence diagram) and `harness-src-classes.puml` (the class
  diagram) — `src/` only, host-blind.
- `def/adapter/<host>/` — one specification + class diagram per host binding (e.g.
  `vscode-github-copilot-chat/`).
- `adapters/` — host-specific bindings (e.g. `vscode-github-copilot-chat/`).
- `contracts/` — JSON Schema contracts for configuration, artifacts, and the journal.
- `src/` — the Python implementation.
- `tests/` — unit and functional suites.

## Validation

```bash
make -C harness verify
make -C harness check-catalog
make -C harness full
```

See [`def/core/spec.md`](def/core/spec.md#validation-surface) for what each target runs.
