# Agentic Harness

The harness is the deterministic execution core of an agentic framework: it resolves steps,
models, and agents' session context (instructions and skills — injected by the host
adapter), checks steps' conditions, authorization, and artifacts, and logs all of it —
deterministically, from persisted state and validated configuration only.

The canonical specification — terminology, invariants, the thirteen-function contract, design,
and implementation — lives in **[`def/spec.md`](def/spec.md)**. Read that first; this file is
only an orientation pointer.

## Layout

- `def/` — the specification (`spec.md`) and its diagrams: `harness-functions.puml` (the
  sequence diagram) and `harness-src-classes.puml` (the class diagram).
- `adapters/` — host-specific bindings (e.g. `github-copilot/`).
- `contracts/` — JSON Schema contracts for configuration, artifacts, and the journal.
- `src/` — the Python implementation.
- `tests/` — unit and functional suites.

## Validation

```bash
make -C harness verify
make -C harness check-catalog
make -C harness full
```

See [`def/spec.md`](def/spec.md#validation-surface) for what each target runs.
