"""Functional tests for unreadable session logs at the command boundary.

Spec (Outcomes rule 1): "an unreadable log" is the environment failing — `system-error`.
Spec (rule 4 / Invocation surfaces): no exception crosses the command boundary — every
invocation either answers a contract-valid report or refuses on the command exit plane.
A log that cannot be read (bytes that are not JSONL, a path the process cannot open) is
therefore a `system-error` report carrying the `log-unreadable` code, never a traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from functional_fixtures import FunctionalHarness, assert_contract_round_trip

SESSION = "orchestrator-session"
OTHER_SESSION = "other-session"

# One contract-valid `in` object per harness function, attributed to SESSION.
INQUIRIES: Mapping[str, Mapping[str, Any]] = {
    "start-session": {"sessionId": SESSION, "agent": "orchestrator"},
    "resolve-workflow-instructions": {"sessionId": SESSION},
    "resolve-workflow-skills": {"sessionId": SESSION},
    "resolve-step": {"sessionId": SESSION, "workflowSlug": "planning"},
    "resolve-step-model": {"sessionId": SESSION},
    "check-step-preconditions": {"sessionId": SESSION},
    "resolve-step-instructions": {"sessionId": SESSION},
    "resolve-step-skills": {"sessionId": SESSION},
    "check-step-authorization": {
        "sessionId": SESSION,
        "artifactPath": "reports/a.json",
        "action": "create",
    },
    "check-step-artifact": {"sessionId": SESSION, "artifactPaths": ["reports/a.json"]},
    "check-step-postconditions": {"sessionId": SESSION},
    "end-session": {"sessionId": SESSION},
}


def _log_path(harness: FunctionalHarness, session_id: str) -> Path:
    return harness.workspace_dir / "logs" / f"{session_id}.log.jsonl"


def _corrupt_with_malformed_jsonl(harness: FunctionalHarness, session_id: str) -> None:
    path = _log_path(harness, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write('{"timestamp": "2026-08-17T13:00:00Z", "report": \n')


def _corrupt_with_unopenable_path(harness: FunctionalHarness, session_id: str) -> None:
    # A directory where the log file belongs: `open()` fails with an OSError for any
    # user (unlike a chmod, which root ignores).
    path = _log_path(harness, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def _corrupt_with_non_object_line(harness: FunctionalHarness, session_id: str) -> None:
    path = _log_path(harness, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write("[1, 2, 3]\n")


def _corrupt_with_non_entry_object(harness: FunctionalHarness, session_id: str) -> None:
    path = _log_path(harness, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write('{"not": "an entry"}\n')


CORRUPTIONS: Mapping[str, Callable[[FunctionalHarness, str], None]] = {
    "malformed-jsonl": _corrupt_with_malformed_jsonl,
    "unopenable-path": _corrupt_with_unopenable_path,
    "non-object-line": _corrupt_with_non_object_line,
    "non-entry-object": _corrupt_with_non_entry_object,
}


def _assert_log_unreadable_report(harness: FunctionalHarness, run) -> None:
    assert run.exit_code == 0, run.stderr
    assert "Traceback" not in run.stderr
    report = assert_contract_round_trip(harness, run)
    assert report["outcome"]["status"] == "system-error"
    assert report["outcome"]["error"]["code"] == "log-unreadable"


class TestUnreadableOwnLog:
    """The invoking session's own log cannot be read."""

    @pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
    @pytest.mark.parametrize("function", sorted(INQUIRIES))
    def test_every_function_answers_system_error_without_an_exception(
        self, harness: FunctionalHarness, function: str, corruption: str
    ) -> None:
        """Spec (rule 1): an unreadable log is `system-error`; rule 4: nothing crosses
        the command boundary as an exception."""
        if corruption != "unopenable-path":
            harness.invoke("start-session", sessionId=SESSION, agent="orchestrator")
        CORRUPTIONS[corruption](harness, SESSION)

        run = harness.invoke(function, **INQUIRIES[function])

        _assert_log_unreadable_report(harness, run)


class TestUnreadableForeignLog:
    """Another session's log cannot be read while a cross-log scan needs it."""

    @pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
    def test_instance_deduction_answers_system_error_without_an_exception(
        self, harness: FunctionalHarness, corruption: str
    ) -> None:
        """Spec (Logging — instance views are discovery scans across every log): the
        scan meeting an unreadable log is the environment failing, `system-error`."""
        harness.invoke("start-session", sessionId=SESSION, agent="orchestrator")
        CORRUPTIONS[corruption](harness, OTHER_SESSION)

        run = harness.invoke("resolve-step", sessionId=SESSION, workflowSlug="planning")

        _assert_log_unreadable_report(harness, run)
