# Agentic Harness

[![CI](https://github.com/poesis-cloud/saf-agentic-harness/actions/workflows/ci.yaml/badge.svg)](https://github.com/poesis-cloud/saf-agentic-harness/actions/workflows/ci.yaml)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](.github/workflows/ci.yaml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

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

## SAF context

This repository is the **engine** product of the Systemic Agentic Framework (SAF). It knows
nothing about any particular methodology: the embedding framework and the data plane are
supplied to it as environment-anchored paths (`FRAMEWORK_DIR`, `FRAMEWORK_WORKSPACE_DIR`).

- [`saf-agentic-organization`](https://github.com/poesis-cloud/saf-agentic-organization) —
  the SAFe-shaped framework application (agents, skills, workflows, instructions, artifacts,
  templates) that embeds this harness.
- [`saf-agentic-workspace`](https://github.com/poesis-cloud/saf-agentic-workspace) — the shared data
  plane the harness reads, checks, and commits into.

## Layout

- `def/` — `harness.sd.puml`, the sequence diagram spanning one workflow instance across
  framework user, orchestrator agent, step subagents, host, and harness. It sits above
  `core/` and `adapter/` because it depicts host-mediated steps (hooks, dispatch) alongside
  the host-agnostic ones, so it is not core-only.
- `def/core/` — the harness-core specification (`spec.md`) and its class diagram
  (`harness-src-classes.puml`) — `src/` only, host-blind.
- `def/adapter/<host>/` — one specification + class diagram per host binding (e.g.
  `vscode-github-copilot-chat/`).
- `adapters/` — host-specific bindings (e.g. `vscode-github-copilot-chat/`).
- `contracts/` — JSON Schema contracts for configuration, artifacts, and the journal.
- `src/` — the Python implementation.
- `tests/` — unit and functional suites.

## Validation

```bash
make verify
make check-catalog
make full
```

See [`def/core/spec.md`](def/core/spec.md#validation-surface) for what each target runs.
