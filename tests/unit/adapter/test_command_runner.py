"""Unit tests for the adapter's command-runner port — its ONE edge into the harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from command_runner import HarnessCommandError, SubprocessCommandRunner


class _RecordingProcess:
    """A stand-in for the process boundary, injected instead of monkeypatched."""

    def __init__(self, exit_code: int = 0, stdout: str = "{}", stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.argv: tuple[str, ...] = ()

    def __call__(self, argv: Sequence[str]) -> tuple[int, str, str]:
        self.argv = tuple(argv)
        return self.exit_code, self.stdout, self.stderr


class TestSubprocessCommandRunner:
    """Adapter spec — Invocation plumbing, seam 3: twelve pure function commands."""

    def test_invokes_one_command_per_function_with_kebab_case_flags(self) -> None:
        """Adapter spec — Invocation plumbing: the invoked surface is the harness core's
        command API — `harness.py`, ONE command per function, hook/host-blind.
        """
        report = {
            "context": {"function": "check-step-authorization", "sessionId": "s-1"},
            "outcome": {"status": "allowed"},
        }
        process = _RecordingProcess(stdout=json.dumps(report))
        runner = SubprocessCommandRunner(
            harness_entrypoint=Path("/framework/harness.py"), run_process=process
        )

        answered = runner.run_function(
            "check-step-authorization",
            {
                "sessionId": "s-1",
                "parentSessionId": None,
                "artifactPath": "portfolio/a.md",
                "action": "create",
            },
        )

        assert process.argv == (
            "python3",
            "/framework/harness.py",
            "check-step-authorization",
            "--session-id",
            "s-1",
            "--artifact-path",
            "portfolio/a.md",
            "--action",
            "create",
        )
        assert answered == report

    def test_repeats_a_flag_for_each_entry_of_a_set_based_argument(self) -> None:
        """Adapter spec H5, invariant 2: function 9 is set-based — ONE invocation carries
        the whole path set.
        """
        process = _RecordingProcess(
            stdout=json.dumps(
                {
                    "context": {"function": "check-step-artifact", "sessionId": "s-1"},
                    "outcome": {"status": "valid"},
                }
            )
        )
        runner = SubprocessCommandRunner(
            harness_entrypoint=Path("/framework/harness.py"), run_process=process
        )

        runner.run_function(
            "check-step-artifact",
            {"sessionId": "s-1", "artifactPaths": ["portfolio/a.md", "portfolio/b.md"]},
        )

        assert process.argv[-4:] == (
            "--artifact-path",
            "portfolio/a.md",
            "--artifact-path",
            "portfolio/b.md",
        )

    def test_raises_when_the_command_fails(self) -> None:
        """Adapter spec H2/H3, deny-by-default: a command that produced no report is a
        failure the caller must render as a denial, never as a silent allow.
        """
        process = _RecordingProcess(exit_code=1, stdout="", stderr="boom")
        runner = SubprocessCommandRunner(
            harness_entrypoint=Path("/framework/harness.py"), run_process=process
        )

        with pytest.raises(HarnessCommandError) as failure:
            runner.run_function("end-session", {"sessionId": "s-1"})

        assert "boom" in str(failure.value)

    def test_raises_when_the_command_answers_something_that_is_not_a_report(self) -> None:
        """Adapter spec — Invocation plumbing: every function answers a contract report on
        stdout; anything else is an unusable outcome, not an implicit success.
        """
        process = _RecordingProcess(stdout="not json")
        runner = SubprocessCommandRunner(
            harness_entrypoint=Path("/framework/harness.py"), run_process=process
        )

        with pytest.raises(HarnessCommandError):
            runner.run_function("end-session", {"sessionId": "s-1"})
