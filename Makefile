# Reusable orchestration-harness verification core.
#
# ONE entry point, reused everywhere:
#   - the agent's fast inner loop runs `check-step` per step;
#   - CI runs the SAME `make verify` target.
#
# `verify` runs the pytest workflow-constitution suite; it exits non-zero on any failure. Runtime
# workspace-artifact validation lives in the CLI (`check-artifact` / `check-step`), invoked per-unit
# by the orchestrator — never in the verification gate.

REPO := $(shell git rev-parse --show-toplevel)
PYTEST := PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider
HARNESS := PYTHONDONTWRITEBYTECODE=1 python3 harness.py
# The on-disk workspace folder name is framework-specific. This framework uses `portfolio/`;
# override with `make full WORKSPACE=/path/to/workspace` for other methodologies.
WORKSPACE ?= $(REPO)/portfolio

.PHONY: verify test check-catalog full install-copilot-hooks

## verify: workflow-constitution gate — the full pytest suite (workflow contracts + the two
## structural invariants + cross-workflow integrity + the ACL plane + the hook funnel + the
## artifact schema/template catalog). Blocks the push on any failure. Workspace artifact CONTENT is
## validated per-unit via the runtime `check-artifact` / `check-step` commands, not here.
verify:
	$(PYTEST) tests/ -q

## check-catalog: run just the artifact schema/template catalog check (also part of verify).
check-catalog:
	$(PYTEST) tests/test_catalog.py -q

## test: alias for the constitution gate (same pytest suite as verify).
test: verify

## full: the constitution suite + the full workspace artifact/derived-field sweep (opt-in)
full: verify
	$(HARNESS) --workspace-root $(WORKSPACE) check-artifact

## install-copilot-hooks: render the Copilot CLI hook map into the repo's .copilot/ (review/merge first)
install-copilot-hooks:
	@mkdir -p $(REPO)/.copilot
	python3 -c "import json,yaml; json.dump(yaml.safe_load(open('adapters/github-copilot/hooks.yaml')), open('$(REPO)/.copilot/hooks.json','w'), indent=2)"
	@echo "installed: $(REPO)/.copilot/hooks.json (rendered from adapters/github-copilot/hooks.yaml — the YAML map is the source of truth)"
