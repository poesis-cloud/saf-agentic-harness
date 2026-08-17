import os
import re


class TestDispatchFunnel:
    def test_dispatch_script_exists_and_can_be_executed_or_invoked_by_sh(self, repo_root):
        dispatch = repo_root / "adapters" / "dispatch.sh"

        assert dispatch.is_file()
        assert os.access(dispatch, os.X_OK) or dispatch.read_text(
            encoding="utf-8"
        ).startswith("#!/usr/bin/env bash")

    def test_dispatch_contract_forwards_event_env_and_optional_agent(self, repo_root):
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
        dispatch = repo_root / "adapters" / "dispatch.sh"
        text = dispatch.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

        assert not re.search(r"(^|\s)(read|cat|tee|jq|python3\s+-c)(\s|$)", code)
        assert "exec python3" in code
