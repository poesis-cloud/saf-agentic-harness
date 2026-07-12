# VS Code GitHub Copilot Chat adapter

The host binding for VS Code with the GitHub Copilot Chat extension
(microsoft/vscode-copilot-chat). **`def/spec.md` is this adapter's canonical specification** —
the hook-by-hook contract (In/Out, output construction, pre/postconditions, invariants) and the
inconsistencies it surfaced against the harness spec. Canonical harness documentation lives in
`../../def/spec.md`; the adapters-folder overview in `../README.md`.

This adapter contains only the host-specific binding:

- `def/spec.md` — the adapter specification: boundary binding, agent-session semantics
  (1 agent session = 1 execution until return; the turn IS the agent session on this host),
  session identity binding, the hook specs (H0–H7) with their JSON snippets, the invocation
  plumbing/contract layering, and the harness-spec findings (I1–I13)
- `contracts/hook-stdin.schema.json` / `contracts/hook-stdout.schema.json` — the adapter's
  normative host I/O contracts (seams 1 and 4 of the plumbing)
- `hooks.yaml` — the workspace hook registration (YAML source of truth), rendered to
  `.github/hooks/safe-harness.json` in the workspace; every entry calls the shared
  `../dispatch.sh <Event> vscode-github-copilot-chat` with `cwd` pinned to the framework root.
  The session-started boundary (H0) is NOT here: it is an agent-scoped `UserPromptSubmit`
  hook rendered into each framework orchestrator's `.agent.md` frontmatter (see `def/spec.md`)
- `tools.yaml` — host tool names, write verbs, and payload keys (snake_case hook envelope)
- `models.yaml` — canonical model slug → host `runSubagent` model id (`"Model Name (copilot)"`)

## Required VS Code settings

The adapter is inert without these (workspace-recommended via `.vscode/settings.json`; see
`def/spec.md` — Required VS Code settings):

- `chat.useHooks: true` — master switch; hook files are discovered but not executed without it
- `chat.hookFilesLocations` — must include `.github/hooks` (the host default; verify it is
  not overridden)
- `chat.useClaudeHooks` — not required (Copilot-format hooks only)

Hooks are a VS Code preview feature — the harness's fail-fast adapter-binding validation at
instantiation (harness spec — Internal validation) is the intended drift detector for
setting/event renames.
