import os
import re


class TestDispatchFunnel:
    def test_dispatch_script_exists_and_can_be_executed_or_invoked_by_sh(self, repo_root):
        """Adapter spec (Invocation plumbing, seam 2): the registered hook command resolves
        to a real, runnable dispatch script — every hook firing enters through it."""
        dispatch = repo_root / "adapters" / "dispatch.sh"

        assert dispatch.is_file()
        assert os.access(dispatch, os.X_OK) or dispatch.read_text(
            encoding="utf-8"
        ).startswith("#!/usr/bin/env bash")

    def test_dispatch_contract_forwards_event_env_and_optional_agent(self, repo_root):
        """Adapter spec (Invocation plumbing, seam 2): dispatch takes the event, the adapter
        env and an optional scoping agent, and locates that env's own adapter — the argv
        contract H0's agent-scoped registration depends on."""
        dispatch = repo_root / "adapters" / "dispatch.sh"
        text = dispatch.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

        assert "usage: dispatch.sh <event> <env> [<agent>]" in text
        assert 'EVENT="${1:?usage: dispatch.sh <event> <env> [<agent>]}"' in code
        assert 'ENV="${2:?usage: dispatch.sh <event> <env> [<agent>]}"' in code
        assert 'AGENT="${3:-}"' in code
        assert 'ADAPTER="$HERE/$ENV/adapter.py"' in code
        assert 'exec python3 "$ADAPTER" hook --event "$EVENT" ${AGENT:+--agent "$AGENT"}' in code

    def test_dispatch_is_a_pure_stdin_forwarder_not_a_payload_parser(self, repo_root):
        """Adapter spec (Invocation plumbing, seam 2): dispatch has no contract of its own —
        it is a pure forwarder that `exec`s the adapter with stdin unchanged, so the host
        payload is parsed in exactly one place, against the stdin contract."""
        dispatch = repo_root / "adapters" / "dispatch.sh"
        text = dispatch.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

        assert not re.search(r"(^|\s)(read|cat|tee|jq|python3\s+-c)(\s|$)", code)
        assert "exec python3" in code
