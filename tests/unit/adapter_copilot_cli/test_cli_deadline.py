"""A stalled harness call must become a DENIAL before the host's hook timeout.

The runtime treats a timed-out `preToolUse` as an allow (host fact 3). So every harness call
of a firing is bounded by a deadline well inside the event's registered `timeoutSec`, and
expiry is a `HarnessCommandError`, which every gating boundary renders as a denial.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli_rig import HOST, WORKSPACE, Rig, load_script, pre_tool_use
from copilot_cli_adapter.runner import HarnessCommandError, SubprocessCommandRunner

ENTRY = load_script("copilot_cli_adapter_entry_deadline", "adapter.py")


def _runner(tmp_path: Path, deadline: float, script: str = "print('{}')") -> SubprocessCommandRunner:
    entry = tmp_path / "harness.py"
    entry.write_text(script, encoding="utf-8")
    return SubprocessCommandRunner(entry, {}, deadline=deadline)


def test_a_command_is_not_started_once_the_deadline_has_passed(tmp_path: Path) -> None:
    spawned = []
    runner = SubprocessCommandRunner(
        tmp_path / "harness.py",
        {},
        run_process=lambda *arguments: spawned.append(arguments) or (0, "{}", ""),
        deadline=10.0,
        clock=lambda: 10.0,
    )
    with pytest.raises(HarnessCommandError, match="deadline has passed"):
        runner.run_function("check-step-authorization", {"sessionId": "s"})
    assert spawned == []


def test_each_command_gets_only_the_time_left(tmp_path: Path) -> None:
    timeouts = []
    now = [100.0]

    def process(argv, environment, timeout):
        timeouts.append(timeout)
        now[0] += 30.0
        return 0, "{}", ""

    runner = SubprocessCommandRunner(
        tmp_path / "harness.py", {}, run_process=process, deadline=190.0, clock=lambda: now[0]
    )
    runner.run_function("check-step-authorization", {})
    runner.run_function("check-step-authorization", {})
    assert timeouts == [90.0, 60.0]


def test_a_stalled_harness_process_is_stopped_at_the_deadline(tmp_path: Path) -> None:
    runner = _runner(tmp_path, time.monotonic() + 0.5, "import time\ntime.sleep(30)\n")
    started = time.monotonic()
    with pytest.raises(HarnessCommandError, match="before the hook deadline"):
        runner.run_function("check-step-preconditions", {"sessionId": "s"})
    assert time.monotonic() - started < 10


def test_a_stalled_authorization_denies_the_write(rig: Rig, tmp_path: Path) -> None:
    rig.open_turn()
    rig.runner.script["check-step-authorization"] = HarnessCommandError(
        "harness command 'check-step-authorization' did not answer before the hook deadline"
    )
    decision = rig.fire(
        "preToolUse", pre_tool_use(("create", {"path": WORKSPACE + r"\portfolio\a.md"}))
    )
    assert decision is not None and decision["permissionDecision"] == "deny"
    assert "unavailable" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "event, scoped, budget",
    [
        ("preToolUse", False, 90.0),
        ("sessionStart", False, 22.5),
        ("errorOccurred", False, 10.0),
        ("userPromptTransformed", True, 90.0),
    ],
)
def test_the_deadline_sits_well_inside_the_registered_timeout(event, scoped, budget) -> None:
    assert ENTRY.hook_deadline(event, scoped, 1000.0) == pytest.approx(1000.0 + budget)


def test_an_unregistered_event_has_no_deadline_and_fails_closed() -> None:
    with pytest.raises(ValueError):
        ENTRY.hook_deadline("noSuchEvent", False, 0.0)


def test_the_entry_hands_the_deadline_to_the_build(rig: Rig) -> None:
    import io
    import json

    seen = []

    def build(*arguments):
        seen.append(arguments)
        return rig.adapter, rig.binding

    stdin = io.BytesIO(json.dumps({"sessionId": HOST}).encode())
    before = time.monotonic()
    code = ENTRY.run_hook(
        ["hook", "--event", "preToolUse"], stdin, io.StringIO(), io.StringIO(), build=build
    )
    assert code == 0
    deadline = seen[-1][3]
    assert before + 85 < deadline < time.monotonic() + 91


def test_the_real_subprocess_runner_honors_its_timeout() -> None:
    from copilot_cli_adapter.runner import run_subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"], dict(os.environ), 0.3
        )

