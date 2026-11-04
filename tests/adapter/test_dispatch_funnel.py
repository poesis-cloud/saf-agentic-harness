import os
import re
import subprocess
from pathlib import Path

import pytest


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
        assert 'PYTHON="${SAF_HARNESS_PYTHON:-python3}"' in code
        assert '"$PYTHON" "$ADAPTER" hook --event "$EVENT" ${AGENT:+--agent "$AGENT"}' in code

    def test_dispatch_is_a_pure_stdin_forwarder_not_a_payload_parser(self, repo_root):
        """Adapter spec (Invocation plumbing, seam 2): dispatch has no contract of its own —
        it is a pure forwarder that runs the adapter with stdin unchanged, so the host
        payload is parsed in exactly one place, against the stdin contract."""
        dispatch = repo_root / "adapters" / "dispatch.sh"
        text = dispatch.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

        assert not re.search(r"(^|\s)(read|cat|tee|jq|python3\s+-c)(\s|$)", code)
        assert '"$PYTHON" "$ADAPTER"' in code


def _fake_interpreter(tmp_path: Path, body: str) -> Path:
    interpreter = tmp_path / "fake-python"
    interpreter.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    interpreter.chmod(0o755)
    return interpreter


def _run_dispatch(repo_root: Path, environment: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(repo_root / "adapters" / "dispatch.sh"), "PreToolUse", "vscode-github-copilot-chat"],
        input="{}",
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), **environment},
    )


class TestDispatchBootstrap:
    """Adapter spec (Bootstrap failure): the host fails OPEN on any exit but 0 and 2, so
    the funnel maps every other exit — a missing interpreter, a crashed adapter — to exit
    2 with a diagnostic on stderr: visible, and fail-closed."""

    def test_runs_the_adapter_under_the_interpreter_the_registration_records(
        self, repo_root, tmp_path
    ):
        interpreter = _fake_interpreter(tmp_path, 'echo "ran $1 $2 $3 $4"')

        result = _run_dispatch(repo_root, {"SAF_HARNESS_PYTHON": str(interpreter)})

        assert result.returncode == 0
        assert result.stdout.strip() == (
            f"ran {repo_root / 'adapters' / 'vscode-github-copilot-chat' / 'adapter.py'} "
            "hook --event PreToolUse"
        )

    @pytest.mark.parametrize("exit_code", [0, 2])
    def test_passes_the_adapter_own_answer_through(self, repo_root, tmp_path, exit_code):
        interpreter = _fake_interpreter(tmp_path, f'echo answer; exit {exit_code}')

        result = _run_dispatch(repo_root, {"SAF_HARNESS_PYTHON": str(interpreter)})

        assert (result.returncode, result.stdout) == (exit_code, "answer\n")

    @pytest.mark.parametrize("exit_code", [1, 3, 127])
    def test_maps_every_other_adapter_exit_to_a_visible_blocking_failure(
        self, repo_root, tmp_path, exit_code
    ):
        interpreter = _fake_interpreter(tmp_path, f"exit {exit_code}")

        result = _run_dispatch(repo_root, {"SAF_HARNESS_PYTHON": str(interpreter)})

        assert result.returncode == 2
        assert "failing closed" in result.stderr
        assert f"exit {exit_code}" in result.stderr

    def test_a_missing_interpreter_fails_closed_visibly(self, repo_root, tmp_path):
        result = _run_dispatch(
            repo_root, {"SAF_HARNESS_PYTHON": str(tmp_path / "no-such-python")}
        )

        assert result.returncode == 2
        assert "failing closed" in result.stderr
