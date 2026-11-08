# SAFe agentic harness: Copilot plugin

The **harness plugin** for the GitHub Copilot agent runtime. It runs in the Copilot CLI, the
Copilot app, and VS Code with **Session Target: Copilot**. It governs the sessions of a
framework plugin (an agentic organization), installed beside it. See the
[adapter specification](https://github.com/poesis-cloud/saf-agentic-harness/blob/main/def/adapter/github-copilot/spec.md),
section *Plugin mode*.

## Prerequisites

- **Python ≥ 3.10** on `PATH` (`py -3`, `python3` or `python`), or `SAF_HARNESS_PYTHON`
  naming an interpreter.
- **Git ≥ 2.38**, and a Git identity (`user.name` and `user.email`) in each governed
  workspace.
- **Network access once**, at the first session, to install the harness requirements from
  `requirements.lock`, hash-checked and binaries only. A mirror works through
  `PIP_INDEX_URL` or `UV_INDEX_URL`.

## What happens at runtime

- At `sessionStart`, the plugin records itself in the per-user registry and provisions its
  Python environment in its data folder (`PLUGIN_DATA`). Nothing is installed in any other
  event.
- A folder is governed when its Git top-level holds a committed `.saf/workspace.yaml` that
  names the framework plugin. Elsewhere, every tool call passes.
- In a governed folder, or once an orchestrator was selected, a missing prerequisite denies
  tool calls with the exact remedy.
- VS Code's **Local** session target is not supported: select **Session Target: Copilot**.

## Settings

| Variable | Effect |
| --- | --- |
| `SAF_HARNESS_PYTHON` | Use this interpreter as is: nothing is provisioned. It must import the harness requirements. |
| `SAF_AGENTIC_REGISTRY_DIR` | The per-user registry folder (default `%LOCALAPPDATA%\saf-agentic\registry`, or `$XDG_STATE_HOME/saf-agentic/registry`). |
| `SAF_AGENTIC_REGISTRY_WAIT_SEC` | How long the stable launcher waits for the registry (default 8). |
| `PIP_INDEX_URL`, `UV_INDEX_URL` | The package index to provision from. |

Auto-update is off unless you opt in for the marketplace. Updating the plugin during a session
makes that session refuse further governed calls: start a new session.
