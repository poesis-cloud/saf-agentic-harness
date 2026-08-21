# Reusable orchestration-harness verification core.
#
# ONE entry point, reused everywhere:
#   - the agent's fast inner loop runs a single slice (`unit`, `functional`, `adapter`);
#   - CI runs the SAME `make verify` target over the whole tree.
#
# `verify` runs the pytest suite; it exits non-zero on any failure. Runtime workspace-artifact
# validation lives in the harness functions themselves (`check-step-artifact`,
# `check-step-preconditions`, …), invoked per-boundary by whatever embeds the harness — never in
# the verification gate.

REPO := $(shell git rev-parse --show-toplevel)
PYTEST := PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider

.PHONY: verify test unit functional adapter install-hooks

## verify: the full pytest suite in one invocation — the unit plane (src/ units + the adapter's
## own units), the functional plane (every harness function end to end through the composition
## root), and the adapter plane (hook map, hook contracts, dispatch funnel). Blocks the push on
## any failure.
verify:
	$(PYTEST) tests -q

## unit: the unit plane only (src/ units + adapters/ units) — the fastest inner loop.
unit:
	$(PYTEST) tests/unit -q

## functional: the functional plane only — every harness function driven through the real
## composition root over a real framework and workspace.
functional:
	$(PYTEST) tests/functional -q

## adapter: the adapter conformance plane only — hooks map, hook contracts, dispatch funnel.
adapter:
	$(PYTEST) tests/adapter -q

## test: alias for the gate (same pytest suite as verify).
test: verify

## install-hooks: render the VS Code hook map into the repo's .github/hooks/ (review/merge first).
## The workspace hooks file only — the per-orchestrator agent-scoped UserPromptSubmit blocks render
## into each orchestrator's .agent.md frontmatter at bundle render time, not here.
install-hooks:
	@mkdir -p $(REPO)/.github/hooks
	python3 -c "import json,yaml; json.dump(yaml.safe_load(open('adapters/vscode-github-copilot-chat/hooks.yaml')), open('$(REPO)/.github/hooks/safe-harness.json','w'), indent=2)"
	@echo "installed: $(REPO)/.github/hooks/safe-harness.json (rendered from adapters/vscode-github-copilot-chat/hooks.yaml — the YAML map is the source of truth)"
