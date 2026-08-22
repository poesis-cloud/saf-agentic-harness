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
## it. ALL THREE targets in one run: the workspace hooks file, the per-orchestrator
## agent-scoped UserPromptSubmit block (H0) that opens the session, and the workspace settings
## that make the host EXECUTE them — without `chat.useHooks` the other two are discovered and
## never run. Everything rendered into the first two is MACHINE-SPECIFIC (absolute dispatch
## path + absolute framework root) and is never committed — re-run this after moving either
## checkout.
##
## PIPELINE ORDERING — a framework ships its agents through a host bundle, and the H0 block
## belongs in the agents the host is actually given. So the framework's OWN bundle renderer
## runs FIRST, and this target injects on top of its output:
##
##   (in the framework checkout)  python3 builds/<host>/render_bundle.py . "$$BUNDLE"
##   (here)                       make install-hooks FRAMEWORK_DIR=<framework> BUNDLE_DIR=$$BUNDLE
##
## There is no harness target for the first step: the bundle renderer is the framework's
## artifact, in the framework's repo, and the harness neither ships nor versions it.
##
##   FRAMEWORK_DIR  required — the framework root; becomes every hook's cwd and the anchor the
##                  harness itself reads from the environment.
##   BUNDLE_DIR     the rendered bundle root. AGENTS_DIR and AGENTS_DEST both default to
##                  $(BUNDLE_DIR)/agents — the H0 block is injected in place, on top of the
##                  bundle's own frontmatter. There is NO framework-anchored default: it would
##                  install a second copy of every orchestrator beside the delivered one.
##   HOOKS_DEST     defaults to $(FRAMEWORK_DIR)/.github/hooks — the host collects
##                  .github/hooks/*.json from the workspace folder it has OPEN, which is the
##                  framework workspace the agents run in, not this harness checkout.
##   AGENTS_DIR     agent sources to read; set it with AGENTS_DEST for a framework whose agents
##                  reach the host some other way (e.g. the workspace .github/agents/ scope).
##   AGENTS_DEST    where the rendered agents land.
##   SETTINGS_DEST  directory holding the workspace settings file; defaults to the location the
##                  adapter's settings map names, under $(FRAMEWORK_DIR). That file is COMMITTED
##                  and hand-maintained, so the required keys are merged into it and everything
##                  else — other settings, comments, indentation — is left as it was.
install-hooks:
	python3 adapters/render_hooks.py \
		--env vscode-github-copilot-chat \
		--framework-dir "$(FRAMEWORK_DIR)" \
		--dest "$(HOOKS_DEST)" \
		--bundle-dir "$(BUNDLE_DIR)" \
		--agents-dir "$(AGENTS_DIR)" \
		--agents-dest "$(AGENTS_DEST)" \
		--settings-dest "$(SETTINGS_DEST)"
