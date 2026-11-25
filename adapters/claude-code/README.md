# Adapter — `claude-code`

This adapter binds the harness to the **Claude Code** agent runtime (the `claude` CLI and the
surfaces that run it). The full specification, including the hook → boundary table, plugin
mode and the limits of this runtime, is
[`def/adapter/claude-code/spec.md`](../../def/adapter/claude-code/spec.md). The gating logic
is the shared adapter base, [`adapters/common/saf_host_adapter`](../common/saf_host_adapter);
this folder holds only what is specific to Claude Code.

```text
adapters/claude-code/
  adapter.py             hook entry:  <python> adapter.py hook --event <Event> --framework-dir <fw>
  render_plugin.py       renders the harness PLUGIN for Claude Code — `make plugin-claude`
  plugin/                the plugin's launcher.py, shim (bin/) and README
  hooks.yaml             global hooks (source of truth, rendered into hooks/hooks.json)
  tools.yaml             tool names + payload keys (validated by contracts/conf/adapters/tools.conf.schema.json)
  models.yaml            canonical model slug -> Claude Code model alias
  contracts/             hook-stdin / hook-stdout JSON Schemas
  claude_code_adapter/   the host profile and decision shapes
```

## Install the plugins (users)

The harness ships as the Claude Code plugin **`poesis-saf-agentic-harness`**, beside the
framework's own plugin (for example `poesis-saf-agentic-organization`). Until both are
published in a marketplace, render and load them locally:

```bash
make plugin-claude CLAUDE_PLUGIN_DIR=../saf-harness-claude-plugin      # this repository
make plugin-claude CLAUDE_PLUGIN_DIR=../saf-organization-claude-plugin # saf-agentic-organization
cd <your SAF workspace>
claude --plugin-dir ../saf-harness-claude-plugin --plugin-dir ../saf-organization-claude-plugin \
       --agent poesis-saf-agentic-organization:release-train-engineer
```

Prerequisites: **Python ≥ 3.10** (or `SAF_HARNESS_PYTHON`), **Git ≥ 2.38** with a Git identity
in the workspace, and network access once (or a mirror through `PIP_INDEX_URL` /
`UV_INDEX_URL`). The first session provisions the harness requirements, hash-checked, into
the plugin's data folder (`CLAUDE_PLUGIN_DATA`). A folder is governed when its Git top-level
holds a committed `.saf/workspace.yaml`:

```yaml
framework: poesis-saf-agentic-organization
root: portfolio
```

Elsewhere every tool call passes. In a governed folder, or once an orchestrator was selected,
a missing prerequisite denies tool calls with the exact remedy.

## Using it

1. **Open the session in the SAF workspace** and **select an orchestrator** as the
   conversation's agent: `claude --agent poesis-saf-agentic-organization:<orchestrator>`, or
   the `agent` setting. Plugin agents are namespaced `<plugin-name>:<agent>` by the runtime,
   and so is every dispatch's `subagent_type`; the adapter strips the namespace.
2. The orchestrator asks the harness with ONE plain `Bash` command, alone in its tool call:
   `python <harness.py> resolve-step --workflow-slug <slug>` or
   `python <harness.py> resolve-step-model`. The injected context gives both lines with the
   installed paths. **The shell does not run this command, and the adapter never edits it.**
   The `PreToolUse` hook runs the function for the agent's own session and returns the report
   as a hook denial whose reason starts with `SAFe agentic harness RESULT (<function>)`. That
   is the successful answer. Only `SAFe agentic harness REJECTED` means the request was
   refused.
3. Steps are dispatched with the `Agent` tool, **with `run_in_background: false`**: a dispatch
   that omits it can run in the background, so the adapter denies it. Writes go through
   `Write`/`Edit`, which the harness authorizes (H3) and validates (H5).

## Model binding

`models.yaml` maps every model-profile slug of the framework catalogue to a model the `Agent`
tool accepts. Claude Code accepts only the family aliases there (`sonnet`, `opus`, `haiku`,
`fable`), and each alias runs the account's current model of that family. Every hook re-reads
the catalogue and refuses one that names a slug `models.yaml` does not bind.

## Known limitations

These are detailed, with their fallbacks, in the spec's *Limited or unsupported boundaries*.

- **Timeouts and crashes fail open** in this runtime (only exit 2 blocks). The adapter and the
  launcher exit 2 at `PreToolUse` on every failure, and stop every harness call well before
  the hook's timeout.
- **Shell writes are not mediated.** The adapter denies `Bash` commands that name the
  workspace or `logs/`; the core's commit-set check is the backstop.
- **The harness answer arrives as a "denial"**, the only channel a `PreToolUse` hook has to
  answer without running or editing the command.
- **Dispatcher correlation.** `SubagentStart` does not name the dispatcher. The adapter
  attributes a started subagent to the pending dispatch of its agent that it let through; when
  several agents dispatched the same agent at once, the subagent is refused (fail closed).
- **Windows** is not verified yet.
