"""The scripted harness command API this suite drives the real adapter process against.

Adapter spec I15: the adapter reaches the harness ONLY through the command API, so a
scripted stand-in for that API is enough to exercise every hook end to end — no workflow
catalog, no ACL, no session log.

Every scripted report is validated against its function's own OUTPUT contract before it is
written, reusing `tests/unit/adapter/contract_assertions.py` (architect finding M3): a
report the real harness could never return fails here, in the fixture, instead of
surviving to assert something false about a renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from contract_assertions import assert_report_matches_contract

REPORT_SESSION_ID = "chat-session-guid-t2026-07-11t14-32-07-000z"

_DEFAULT_AUTHORIZATION: Mapping[str, str] = {
    "actor": "qa-engineer",
    "artifactPath": "portfolio/epics/epic-payments.md",
    "action": "update",
    "resource": "epic",
}


def build_report(
    function: str,
    status: str,
    session_id: str = REPORT_SESSION_ID,
    **payload: Any,
) -> dict[str, Any]:
    """Build one report the real function could return — held to its output contract."""
    report: dict[str, Any] = {
        "context": {"function": function, "sessionId": session_id},
        "outcome": {"status": status},
        **payload,
    }
    assert_report_matches_contract(function, report)
    return report


def build_error_report(
    function: str,
    status: str = "state-error",
    code: str = "session-unregistered",
    message: str = "No session log exists for this session.",
    session_id: str = REPORT_SESSION_ID,
) -> dict[str, Any]:
    """Build one ERROR report — the deny-by-default driver, held to the same contract."""
    report: dict[str, Any] = {
        "context": {"function": function, "sessionId": session_id},
        "outcome": {
            "status": status,
            "error": {"code": code, "message": message, "retryable": False},
        },
    }
    assert_report_matches_contract(function, report)
    return report


def build_failing_condition_check(slug: str, failure_message: str) -> dict[str, Any]:
    """Build one failing `conditionChecks[]` entry — H2's and H6's deny/block payload."""
    return {
        "condition": {"kind": "precondition", "slug": slug, "step": "build"},
        "outcome": "fail",
        "failureMessage": failure_message,
    }


def default_script() -> dict[str, dict[str, Any]]:
    """Script every function the adapter can invoke to its plain success answer."""
    successes: tuple[dict[str, Any], ...] = (
        build_report(
            "start-session",
            "started",
            session={"sessionId": REPORT_SESSION_ID, "agent": "qa-engineer"},
        ),
        build_report("end-session", "ended"),
        build_report(
            "resolve-workflow-instructions", "resolved", instructions=["reports-handling"]
        ),
        build_report("resolve-workflow-skills", "resolved", skills=["code-review"]),
        build_report(
            "resolve-step-instructions", "resolved", instructions=["reports-handling"]
        ),
        build_report("resolve-step-skills", "resolved", skills=["code-review"]),
        build_report("check-step-preconditions", "pass", conditionChecks=[]),
        build_report("check-step-postconditions", "pass", conditionChecks=[]),
        build_report(
            "check-step-authorization", "allowed", authorization=dict(_DEFAULT_AUTHORIZATION)
        ),
        build_report("check-step-artifact", "valid"),
    )
    return {
        str(report["context"]["function"]): {"report": report} for report in successes
    }


@dataclass(frozen=True)
class HarnessCall:
    """One invocation of the harness command API, as the stub observed it."""

    function: str
    argv: tuple[str, ...]

    def values(self, flag: str) -> tuple[str, ...]:
        """Answer every value this call carries for a repeatable flag, in order."""
        return tuple(
            self.argv[index + 1]
            for index, token in enumerate(self.argv)
            if token == flag and index + 1 < len(self.argv)
        )

    def value(self, flag: str) -> str | None:
        """Answer the single value this call carries for a flag, or None."""
        values = self.values(flag)
        return values[0] if values else None

    def carries(self, flag: str) -> bool:
        """Tell whether this call carries a flag at all."""
        return flag in self.argv


@dataclass(frozen=True)
class HarnessStub:
    """Script the harness command API and read back what the adapter actually invoked."""

    script_path: Path
    journal_path: Path

    @classmethod
    def create(cls, script_path: Path, journal_path: Path) -> "HarnessStub":
        """Install the default success script and an empty journal, and answer the stub."""
        stub = cls(script_path=script_path, journal_path=journal_path)
        stub._write_script(default_script())
        journal_path.write_text("", encoding="utf-8")
        return stub

    def answers(self, function: str, report: Mapping[str, Any]) -> None:
        """Script one function to answer this report (validated against its contract)."""
        assert_report_matches_contract(function, report)
        self._rewrite(function, {"report": dict(report)})

    def answers_in_turn(
        self, function: str, reports: Sequence[Mapping[str, Any]]
    ) -> None:
        """Script one report per successive call — H3 fans function 8 out path by path."""
        for report in reports:
            assert_report_matches_contract(function, report)
        self._rewrite(function, {"reports": [dict(report) for report in reports]})

    def fails(self, function: str, message: str = "harness command unavailable") -> None:
        """Script one function to fail as a process — the harness-error path."""
        self._rewrite(function, {"failure": message})

    def fails_every_function(self, message: str = "harness command unavailable") -> None:
        """Script EVERY function to fail — the worst case the host must survive."""
        script = self._read_script()
        self._write_script({function: {"failure": message} for function in script})

    @property
    def calls(self) -> tuple[HarnessCall, ...]:
        """Answer every harness invocation so far, in the order the adapter made them."""
        lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        return tuple(_read_call(json.loads(line)) for line in lines if line)

    @property
    def functions(self) -> tuple[str, ...]:
        """Answer the invoked function names, in order."""
        return tuple(call.function for call in self.calls)

    def find_calls(self, function: str) -> tuple[HarnessCall, ...]:
        """Answer every invocation of one function."""
        return tuple(call for call in self.calls if call.function == function)

    def forget_calls(self) -> None:
        """Drop the recorded calls, so a later assertion speaks of one firing only."""
        self.journal_path.write_text("", encoding="utf-8")

    def _rewrite(self, function: str, answer: Mapping[str, Any]) -> None:
        self._write_script({**self._read_script(), function: dict(answer)})

    def _read_script(self) -> dict[str, Any]:
        return json.loads(self.script_path.read_text(encoding="utf-8"))

    def _write_script(self, script: Mapping[str, Any]) -> None:
        self.script_path.write_text(
            json.dumps(script, indent=2, sort_keys=True), encoding="utf-8"
        )


def _read_call(recorded: Mapping[str, Any]) -> HarnessCall:
    return HarnessCall(
        function=str(recorded["function"]), argv=tuple(str(token) for token in recorded["argv"])
    )


__all__ = [
    "HarnessCall",
    "HarnessStub",
    "REPORT_SESSION_ID",
    "build_error_report",
    "build_failing_condition_check",
    "build_report",
    "default_script",
]
