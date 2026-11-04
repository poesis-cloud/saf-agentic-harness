# Agentic Harness

[![CI](https://github.com/poesis-cloud/saf-agentic-harness/actions/workflows/ci.yaml/badge.svg)](https://github.com/poesis-cloud/saf-agentic-harness/actions/workflows/ci.yaml)
[![Release](https://img.shields.io/github/v/release/poesis-cloud/saf-agentic-harness)](https://github.com/poesis-cloud/saf-agentic-harness/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](.github/workflows/ci.yaml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

The harness is the deterministic execution core of an agentic framework: it resolves steps,
models, and agents' session context (instructions and skills — injected at session open),
checks steps' conditions, authorization, and artifacts, and logs all of it —
deterministically, from persisted state and validated configuration only. The harness core
is fully host-agnostic; host-specific bindings live in separate adapter specifications.

The canonical harness-core specification — terminology, invariants, the twelve-function
contract, design, and implementation — lives in **[`def/core/spec.md`](def/core/spec.md)**.
Read that first; this file is
only an orientation pointer. Each host binding has its own specification under
`def/adapter/<host>/spec.md`:
[`vscode-github-copilot-chat`](def/adapter/vscode-github-copilot-chat/spec.md) (VS Code +
Copilot Chat) and [`github-copilot-cli`](def/adapter/github-copilot-cli/spec.md) (the GitHub
Copilot app and the Copilot CLI — install: [`adapters/github-copilot-cli/README.md`](adapters/github-copilot-cli/README.md)).

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
- `def/adapter/<host>/` — one specification + class diagram per host binding
  (`vscode-github-copilot-chat/`, `github-copilot-cli/`).
- `adapters/` — host-specific bindings (`vscode-github-copilot-chat/`, `github-copilot-cli/`).
- `contracts/` — JSON Schema contracts for configuration, artifacts, and the journal.
- `src/` — the Python implementation; `harness.py` at the root is its entry point
  (`python3 harness.py [--env-file <layout file>] <function> [flags]`).
- `tests/` — the unit (`tests/unit/`), functional (`tests/functional/`), and adapter
  (`tests/adapter/`) planes.

## Requirements

- Python 3.10–3.12.
- Runtime: `pip install -r requirements.txt` (cel-python, jsonschema, PyYAML). Tests
  additionally need `pip install -r requirements-dev.txt`.
- The interpreter that runs the hooks must have `requirements.txt` installed:
  `make install-hooks` records that absolute interpreter (`HARNESS_PYTHON`, defaulting to the
  interpreter running the renderer, i.e. `PYTHON`) and `FRAMEWORK_DIR` into every rendered
  hook entry's `env`, and refuses to render if the interpreter cannot import the requirements.

## Validation

```bash
make verify       # the full pytest suite (unit + functional + adapter planes); CI runs this
make unit         # the unit plane only (tests/unit)
make functional   # the functional plane only (tests/functional)
make adapter      # the adapter conformance plane only (tests/adapter)
```

`make test` is an alias for `make verify`;
`make install-hooks FRAMEWORK_DIR=<framework> BUNDLE_DIR=<bundle>` renders and installs the
host hook registration. See the [`Makefile`](Makefile) for what each target runs and the
[TDD plan](def/tdd-plan.md) for the test layout.

<!-- roadmap:start -->
## Roadmap

![Roadmap](docs/roadmap.svg)

<!-- roadmap:end -->
