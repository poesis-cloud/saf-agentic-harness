# Reusable orchestration-harness verification core.
#
# ONE entry point, reused everywhere:
#   - the agent's fast inner loop runs a single slice (`unit`, `functional`);
#   - CI runs the SAME `make verify` target over the whole tree.
#
# `verify` runs the pytest suite; it exits non-zero on any failure. Runtime workspace-artifact
# validation lives in the harness functions themselves (`check-step-artifact`,
# `check-step-preconditions`, …), invoked per-boundary by whatever embeds the harness — never in
# the verification gate.

PYTHON ?= python3
PYTEST := PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -p no:cacheprovider

.PHONY: verify test unit functional install-hooks plugin

## verify: the full pytest suite in one invocation — the unit plane (src/ units + the adapter's
## own units) and the functional plane (every harness function end to end through the
## composition root, and the adapter as a process). Blocks the push on any failure.
verify:
	$(PYTEST) tests -q

## unit: the unit plane only (src/ units + adapters/ units) — the fastest inner loop.
unit:
	$(PYTEST) tests/unit -q

## functional: the functional plane only — every harness function driven through the real
## composition root over a real framework and workspace, and the adapter as a process.
functional:
	$(PYTEST) tests/functional -q

## test: alias for the gate (same pytest suite as verify).
test: verify

## install-hooks: the DEVELOPER install of the github-copilot adapter — render its hooks
## (adapters/github-copilot) into a framework's rendered plugin bundle: the global hooks.json
## and the orchestrators' agent-scoped H0 block. Machine-specific output, never committed. On
## Windows run install.py directly (see adapters/github-copilot/README.md). Users install the
## harness PLUGIN instead (see `plugin`).
##
##   FRAMEWORK_DIR  required — the framework CHECKOUT (not the bundle).
##   BUNDLE_DIR     required — the framework's rendered Copilot plugin bundle.
##   WORKSPACE_DIR  optional — absolute FRAMEWORK_WORKSPACE_DIR override.
##   HARNESS_PYTHON absolute interpreter the hooks run under; defaults to the interpreter
##                  running the installer ($(PYTHON)). It must import requirements.txt or the
##                  install is refused. Recorded as `exec` and SAF_HARNESS_PYTHON in every entry.
install-hooks:
	$(PYTHON) adapters/github-copilot/install.py \
		$(if $(HARNESS_PYTHON),--python "$(HARNESS_PYTHON)") \
		--framework-dir "$(FRAMEWORK_DIR)" \
		--bundle-dir "$(BUNDLE_DIR)" \
		$(if $(WORKSPACE_DIR),--workspace-dir "$(WORKSPACE_DIR)")

## plugin: build the harness PLUGIN (Agent Plugins 1.0) into PLUGIN_DIR (default
## build/plugin): the manifest, the hooks, the shims and the launcher, and the runtime. Pass
## LOCK=<requirements.lock> to ship a hash-locked lock; CI generates it with
## `uv pip compile --universal --generate-hashes requirements.txt`.
PLUGIN_DIR ?= build/plugin
plugin:
	$(PYTHON) adapters/github-copilot/render_plugin.py --dest "$(PLUGIN_DIR)" \
		$(if $(LOCK),--lock "$(LOCK)")
