"""Functional tests for harness function 0, `start-session`.

Every test drives the assembled system through the real command entry point and
asserts the contract-validated In -> Out round trip, the postconditions on the real
workspace, and the invariants observable from outside.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
    run_git,
)
from utils.exclusive_file_lock import ExclusiveFileLock

FUNCTION = "start-session"


class TestStartSession:
    """Function 0: the session seed and the very file every later entry appends to."""

    def test_root_session_registers_and_creates_its_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, Interface + Postconditions): `started` + the `session`
        object, and `<workspace>/logs/<sessionId>.log.jsonl` exists with the start as
        its first line."""
        run = harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "started"}
        assert report["session"] == {
            "agent": "orchestrator",
            "sessionId": "root-session",
            "parentSessionId": None,
        }
        assert report["context"] == {
            "function": FUNCTION,
            "sessionId": "root-session",
            "parentSessionId": None,
            "workflowInstanceId": None,
        }
        assert harness.log_path("root-session").is_file()
        entries = assert_journal_contract(harness, "root-session")
        assert len(entries) == 1
        assert_report_journaled_byte_identically(harness, run, 0)

    def test_registration_precedes_every_other_entry(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, invariant 1): starting precedes everything — function 0
        creates the very file the others append to, so its entry is line one."""
        harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")
        harness.invoke("resolve-workflow-skills", sessionId="root-session")

        assert harness.list_journaled_functions("root-session") == (
            FUNCTION,
            "resolve-workflow-skills",
        )

    def test_root_session_of_a_non_framework_agent_passes_through_unstarted(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, precondition E + rule 2): a root `agent` naming no
        framework agent is `not-applicable` — returned, never journaled, so no log
        file is created at all."""
        run = harness.invoke(FUNCTION, sessionId="stranger-session", agent="stranger")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert "session" not in report
        assert not harness.is_session_logged("stranger-session")

    def test_redelivery_replays_the_registration_without_a_second_entry(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, invariant 4): re-delivery of the same start for an
        already-registered OPEN session returns the same `started` report and appends
        no second registration."""
        first = harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")
        second = harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")

        replayed = assert_contract_round_trip(harness, second)
        assert replayed == first.report
        assert len(harness.read_log("root-session")) == 1

    def test_reregistration_under_another_agent_conflicts_and_journals(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, invariant 4): a re-registration naming a DIFFERENT agent
        for a registered session is `state-error` (`session-conflict`), journaled —
        identity never silently mutates."""
        harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")
        run = harness.invoke(FUNCTION, sessionId="root-session", agent="builder")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-conflict"
        entries = assert_journal_contract(harness, "root-session")
        assert len(entries) == 2
        assert entries[1]["report"] == report

    def test_step_session_without_a_correlated_resolution_passes_through(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, precondition E + rule 2): for a worker session the
        correlation IS the check — a dispatch no step resolution correlates to finds
        no target and is `not-applicable`, unjournaled."""
        run = harness.invoke(
            FUNCTION,
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert not harness.is_session_logged("step-session")

    def test_step_session_registers_against_its_parents_resolution(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 3): the parent chain is unbounded — each start
        records one parent, and a worker session correlating to its parent's unresolved
        step resolution registers with that parent recorded."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")

        run = harness.invoke(
            FUNCTION,
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "started"}
        assert report["session"] == {
            "agent": "builder",
            "sessionId": "step-session",
            "parentSessionId": orchestrator_session,
        }
        assert len(assert_journal_contract(harness, "step-session")) == 1

    def test_a_second_step_session_for_one_resolution_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 5 + rule 4): 1 step = 1 session — once the
        in-flight resolution registered its worker session, a second start for it is
        `state-error` (`worker-session-bound`), returned and never journaled."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")
        harness.invoke(
            FUNCTION,
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )
        parent_before = harness.read_log_lines(orchestrator_session)

        run = harness.invoke(
            FUNCTION,
            sessionId="second-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "worker-session-bound"
        assert not harness.is_session_logged("second-session")
        assert harness.read_log_lines(orchestrator_session) == parent_before
        assert len(harness.read_log("step-session")) == 1

    def test_concurrent_starts_for_one_resolution_register_exactly_one_worker(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 5): a step resolution admits exactly ONE worker
        session, also when the same resolved step is dispatched several times at once —
        real, concurrent harness processes. Exactly one is `started`; every other is
        `worker-session-bound`, with no log and no step ref of its own."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")
        workers = [f"worker-{index}" for index in range(4)]

        processes = {
            worker: harness.spawn_entry_shim(
                FUNCTION,
                sessionId=worker,
                parentSessionId=orchestrator_session,
                agent="builder",
            )
            for worker in workers
        }
        reports = {}
        for worker, process in processes.items():
            stdout, stderr = process.communicate(timeout=120)
            assert process.returncode == 0, stderr
            reports[worker] = json.loads(stdout)

        statuses = sorted(
            (report["outcome"]["status"], (report["outcome"].get("error") or {}).get("code"))
            for report in reports.values()
        )
        assert statuses == [("started", None)] + [("state-error", "worker-session-bound")] * 3
        (winner,) = [w for w, r in reports.items() if r["outcome"]["status"] == "started"]
        assert [w for w in workers if harness.is_session_logged(w)] == [winner]
        step_refs = run_git(
            harness.workspace_dir, "for-each-ref", "--format=%(refname)", "refs/harness/steps/"
        ).split()
        assert [ref.rsplit("/", 1)[-1] for ref in step_refs] == [winner]

    def test_a_registration_waits_for_another_process_in_the_critical_section(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 5): the check and the registration are ONE
        critical section across processes. While another process holds the
        registration lock, a real harness process cannot register; it registers as
        soon as the lock is released."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")
        git_dir = Path(run_git(harness.workspace_dir, "rev-parse", "--absolute-git-dir").strip())

        with ExclusiveFileLock(git_dir / "harness-registration.lock"):
            process = harness.spawn_entry_shim(
                FUNCTION,
                sessionId="worker-0",
                parentSessionId=orchestrator_session,
                agent="builder",
            )
            time.sleep(2.0)
            assert process.poll() is None
            assert not harness.is_session_logged("worker-0")

        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["outcome"]["status"] == "started"
        assert harness.is_session_logged("worker-0")

    def test_a_re_resolved_step_admits_a_new_step_session(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 5): function 10 concludes the step at dispatch
        return; the orchestrator re-resolves the failed step, and that new resolution
        admits a new worker session."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")
        harness.invoke(
            FUNCTION,
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )
        concluded = harness.invoke("check-step-postconditions", sessionId=orchestrator_session)
        assert concluded.status == "fail"
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")

        run = harness.invoke(
            FUNCTION,
            sessionId="retry-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        assert assert_contract_round_trip(harness, run)["outcome"] == {"status": "started"}

    def test_start_against_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 4 + rule 3): a start against an ENDED id is the
        C8 refusal — `state-error` (`session-ended`) — and no entry ever follows the
        ending entry."""
        harness.invoke("end-session", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session, agent="orchestrator")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a contract-validation failure produces NO report at all — it
        surfaces at the command exit plane with stderr and a nonzero exit, and nothing
        is journaled."""
        run = harness.invoke(FUNCTION, sessionId="root-session")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert "invalid-inquiry" in run.stderr
        assert not harness.is_session_logged("root-session")

    @pytest.mark.parametrize("session_id", ["Root_Session", "root session", "../escape"])
    def test_an_unsafe_session_id_never_becomes_a_log_filename(
        self, harness: FunctionalHarness, session_id: str
    ) -> None:
        """Spec (function 0, precondition E; Logging, sanitization): the id becomes a
        log filename, so a raw host id is a path-traversal vector the contract
        rejects, unjournalably."""
        run = harness.invoke(FUNCTION, sessionId=session_id, agent="orchestrator")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert not (harness.workspace_dir / "logs").exists()

    def test_the_entry_shim_runs_the_same_registration(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (Invocation surfaces): every harness function is exposed as ONE harness
        command — `harness.py <function> [flags]` over `FRAMEWORK_DIR` is that surface."""
        run = harness.invoke_entry_shim(
            FUNCTION, sessionId="shim-session", agent="orchestrator"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "started"}
        assert len(assert_journal_contract(harness, "shim-session")) == 1
