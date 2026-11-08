"""Functional tests for the harness compatibility contract (core spec, Harness compatibility).

The framework declares the harness versions it supports (`conf/harness.conf.yaml`,
`require`); the fail-fast load refuses an installed version outside it. Function 0 stamps
the installed version into every session it starts, and every later call of functions 0–10
by another version is refused with `harness-version-changed`, unjournaled. Function 11 is
exempt.
"""

from __future__ import annotations

import json

import pytest

from functional_fixtures import FunctionalHarness, assert_contract_round_trip
from harness_version import HARNESS_VERSION

OTHER_VERSION = "0.9.0"

# Every session-bound function but 11, as a host would call it against an open session.
VERSION_REFUSED_INVOCATIONS: tuple[tuple[str, dict[str, str]], ...] = (
    ("start-session", {"agent": "orchestrator"}),
    ("resolve-workflow-instructions", {}),
    ("resolve-workflow-skills", {}),
    ("resolve-step", {"workflowSlug": "planning"}),
    ("resolve-step-model", {}),
    ("check-step-preconditions", {}),
    ("resolve-step-instructions", {}),
    ("resolve-step-skills", {}),
    ("check-step-postconditions", {}),
    ("check-step-authorization", {"artifactPath": "report/x.json", "action": "create"}),
    ("check-step-artifact", {"artifactPaths": ["report/x.json"]}),
)


def _write_require(harness: FunctionalHarness, require: str) -> None:
    (harness.framework_dir / "conf" / "harness.conf.yaml").write_text(
        f"require: {json.dumps(require)}\n", encoding="utf-8"
    )


def _restamp(harness: FunctionalHarness, session_id: str, version: str | None) -> None:
    """Rewrite the session's start entry as if another harness version had started it."""
    path = harness.log_path(session_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    if version is None:
        first["report"]["session"].pop("harnessVersion", None)
    else:
        first["report"]["session"]["harnessVersion"] = version
    lines[0] = json.dumps(first, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestCompatibilityCheck:
    """The fail-fast load compares the installed version against `require`."""

    def test_an_admitted_version_loads(self, harness: FunctionalHarness) -> None:
        _write_require(harness, f"=={HARNESS_VERSION}")

        run = harness.invoke("start-session", sessionId="root-session", agent="orchestrator")

        assert run.status == "started"

    def test_a_version_outside_the_range_is_a_configuration_error(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec: exit 3, no report, "requires harness <range>; installed <version>"."""
        _write_require(harness, ">=99.0")

        run = harness.invoke_entry_shim(
            "start-session", sessionId="root-session", agent="orchestrator"
        )

        assert run.exit_code == 3 and run.stdout == ""
        assert f"requires harness >=99.0; installed {HARNESS_VERSION}" in run.stderr
        assert not harness.is_session_logged("root-session")

    def test_a_missing_declaration_fails_the_load(self, harness: FunctionalHarness) -> None:
        """Spec: the file is required."""
        (harness.framework_dir / "conf" / "harness.conf.yaml").unlink()

        run = harness.invoke_entry_shim(
            "start-session", sessionId="root-session", agent="orchestrator"
        )

        assert run.exit_code == 3 and "harness.conf.yaml" in run.stderr

    @pytest.mark.parametrize("require", ["not a range", "=>1.0"])
    def test_a_require_that_is_not_pep_440_fails_the_load(
        self, harness: FunctionalHarness, require: str
    ) -> None:
        _write_require(harness, require)

        run = harness.invoke_entry_shim(
            "start-session", sessionId="root-session", agent="orchestrator"
        )

        assert run.exit_code == 3 and "PEP 440" in run.stderr

    def test_a_pre_release_is_read_as_pep_440(self, harness: FunctionalHarness) -> None:
        """Spec: `1.0.0-alpha.1` reads as `1.0.0a1`, so a range admits pre-releases."""
        _write_require(harness, ">=1.0.0a1,<2")

        run = harness.invoke("start-session", sessionId="root-session", agent="orchestrator")

        assert run.status == "started"


class TestVersionStamp:
    """Function 0 stamps the session; another version is refused, unjournaled."""

    def test_function_0_stamps_the_installed_version(
        self, harness: FunctionalHarness
    ) -> None:
        run = harness.invoke("start-session", sessionId="root-session", agent="orchestrator")

        report = assert_contract_round_trip(harness, run)
        assert report["session"]["harnessVersion"] == HARNESS_VERSION
        assert harness.read_log("root-session")[0]["report"]["session"][
            "harnessVersion"
        ] == HARNESS_VERSION

    @pytest.mark.parametrize("stamp", [OTHER_VERSION, None], ids=["other", "unstamped"])
    @pytest.mark.parametrize(
        ("function", "inquiry"),
        VERSION_REFUSED_INVOCATIONS,
        ids=[function for function, _ in VERSION_REFUSED_INVOCATIONS],
    )
    def test_another_versions_session_is_refused_unjournaled(
        self,
        harness: FunctionalHarness,
        orchestrator_session: str,
        function: str,
        inquiry: dict[str, str],
        stamp: str | None,
    ) -> None:
        _restamp(harness, orchestrator_session, stamp)
        before = harness.read_log_lines(orchestrator_session)

        run = harness.invoke(function, sessionId=orchestrator_session, **inquiry)

        assert run.status == "state-error"
        assert run.error_code == "harness-version-changed"
        assert "start a new session" in run.outcome["error"]["message"]
        assert harness.read_log_lines(orchestrator_session) == before

    def test_function_11_ends_another_versions_session(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec: function 11 is exempt, so an admitted version can end the session."""
        _restamp(harness, orchestrator_session, OTHER_VERSION)

        run = harness.invoke("end-session", sessionId=orchestrator_session)

        assert run.status == "ended"
        assert harness.list_journaled_functions(orchestrator_session)[-1] == "end-session"
