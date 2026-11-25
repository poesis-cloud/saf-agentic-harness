# SAFe agentic harness: Claude Code plugin

The **harness plugin** for Claude Code. It governs the sessions of a framework plugin (an
agentic organization), installed beside it. See the
[adapter specification](https://github.com/poesis-cloud/saf-agentic-harness/blob/main/def/adapter/claude-code/spec.md),
section *Plugin mode*.

## Prerequisites

- **Python ≥ 3.10** on `PATH` (`python3` or `python`), or `SAF_HARNESS_PYTHON` naming an
  interpreter.
- **Git ≥ 2.38**, and a Git identity (`user.name` and `user.email`) in each governed
  workspace.
- **Network access once**, at the first session, to install the harness requirements from
  `requirements.lock`, hash-checked and binaries only. A mirror works through
  `PIP_INDEX_URL` or `UV_INDEX_URL`.

## What happens at runtime

- At `SessionStart`, the plugin provisions its Python environment in its data folder
  (`CLAUDE_PLUGIN_DATA`). Nothing is installed in any other event.
- A folder is governed when its Git top-level holds a committed `.saf/workspace.yaml` that
  names the framework plugin. Elsewhere, every tool call passes.
- An orchestrator is the conversation's selected agent (`claude --agent
  <framework-plugin>:<orchestrator>`, or the `agent` setting). In a governed folder, or once
  an orchestrator was selected, a missing prerequisite denies tool calls with the exact
  remedy.

## Settings

| Variable | Effect |
| --- | --- |
| `SAF_HARNESS_PYTHON` | Use this interpreter as is: nothing is provisioned. It must import the harness requirements. |
| `SAF_AGENTIC_REGISTRY_DIR` | The per-user registry folder (default `$XDG_STATE_HOME/saf-agentic/registry`, or `%LOCALAPPDATA%\saf-agentic\registry`). |
| `PIP_INDEX_URL`, `UV_INDEX_URL` | The package index to provision from. |

Updating the plugin during a session makes that session refuse further governed calls:
start a new session.
