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

PYTEST := PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider
HOOKS_DEST ?= $(FRAMEWORK_DIR)/.github/hooks

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

## install-hooks: render the VS Code hook registration and install it where the host will read
## it. The rendered file is MACHINE-SPECIFIC (absolute dispatch path + absolute framework root)
## and is never committed — re-run this after moving either checkout.
##   FRAMEWORK_DIR  required — the framework root; becomes every hook's cwd.
##   HOOKS_DEST     defaults to $(FRAMEWORK_DIR)/.github/hooks — the host collects
##                  .github/hooks/*.json from the workspace folder it has OPEN, which is the
##                  framework workspace the agents run in, not this harness checkout.
## The workspace hooks file only — the per-orchestrator agent-scoped UserPromptSubmit blocks
## belong in each orchestrator's .agent.md frontmatter and are not rendered here.
install-hooks:
	python3 adapters/render_hooks.py \
		--env vscode-github-copilot-chat \
		--framework-dir "$(FRAMEWORK_DIR)" \
		--dest "$(HOOKS_DEST)"
