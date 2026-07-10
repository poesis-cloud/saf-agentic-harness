# Mock subagent hook test

This adapter folder contains a throwaway test for verifying that
`subagentStart` hooks receive inputs at each nesting level and that a subagent
can spawn another subagent.

## Files

| File | Purpose |
|------|---------|
| `mock-subagent-echo.sh` | Mock `subagentStart` hook: logs the payload and echoes it back as `additionalContext` for `mock-parent` and `mock-child` only. |
| `../../../.github/hooks/mock-subagent.json` | Workspace hook registration that wires `subagentStart` to the mock script. |
| `../../../agents/mock-parent.agent.md` | Parent test agent; uses the `agent` tool to invoke `mock-child`. |
| `../../../agents/mock-child.agent.md` | Child test agent; returns the hook input it received. |
| `../../../.github/prompts/mock-subagent-test.prompt.md` | Prompt that asks the default agent to run `mock-parent` as a subagent. |

## How to trigger from Copilot Chat

1. Reload the window if you just added the files (`Developer: Reload Window`).
2. Open Copilot Chat.
3. Type `/mock-subagent-test` and submit.
4. The default agent should use the `agent` tool to invoke `@mock-parent`.
5. `mock-parent` then uses the `agent` tool to invoke `@mock-child`.
6. The `subagentStart` hook fires **twice** — once for each level — and the mock
   script echoes the payload into each subagent's context.
7. Inspect the hook log:

   ```bash
   cat /tmp/safe-mock-subagent-hook.log.jsonl
   ```

Each line is a JSON object with `timestamp`, `agent` (`mock-parent` or
`mock-child`), and the full `input` payload (`sessionId`, `cwd`,
`transcriptPath`, `agentName`, `agentDisplayName`, `agentDescription`, etc.).

## Cleaning up

Delete these test files before merging:

- `.github/hooks/mock-subagent.json`
- `.github/prompts/mock-subagent-test.prompt.md`
- `agents/mock-parent.agent.md`
- `agents/mock-child.agent.md`
- `harness/adapters/github-copilot/mock-subagent-echo.sh`
- `harness/adapters/github-copilot/mock-subagent-test.md`
