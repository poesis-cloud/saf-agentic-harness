"""Functional tests for step transactions: step refs, artifact locks, and promotion.

Core spec (Terminology; Workspace Git plane; Isolation level; functions 0, 3, 8, 9, 10):
the step instance is the unit of acceptance. Each test drives the real command entry
point over a real framework and Git workspace and asserts what the workspace repository
holds afterwards — workspace state (`HEAD`), the step refs, the artifact locks, and the
working tree.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Mapping

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    build_capabilities,
    run_git,
)

PLANNING = "planning"
VERIFICATION = "verification"
DRAFT_REF = "report/draft.json"
VERIFY_REF = "report/verify.json"

# One step whose postcondition holds only once its delivery is approved: a first step
# instance writing a draft fails, and its retry can pass.
APPROVAL_WORKFLOW: Mapping[str, Any] = {
    "slug": "approval",
    "orchestrator": "orchestrator",
    "skills": ["workflow-selection"],
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "draft",
            "actor": "builder",
            "artifact": "report",
            "instructions": ["draft-guidance"],
            "capabilities": build_capabilities(coding=8),
            "conditions": [
                {
                    "kind": "postcondition",
                    "slug": "draft-approved",
                    "setSelector": {
                        "setQuery": "workspace.filter(a, a.path == step.artifact.path"
                        " && a.data.state == 'approved')"
                    },
                    "setPredicate": "selected.size() == 1",
                }
            ],
        }
    ],
}


def _git(harness: FunctionalHarness, *args: str) -> str:
    return run_git(harness.workspace_dir, *args).strip()


def _head(harness: FunctionalHarness) -> str:
    return _git(harness, "rev-parse", "HEAD")


def _step_ref(harness: FunctionalHarness, worker: str) -> str:
    for name in _git(harness, "for-each-ref", "--format=%(refname)", "refs/harness/steps/").split():
        if name.rsplit("/", 1)[-1] == worker:
            return name
    raise AssertionError(f"no step ref for {worker}")


def _lock_holder(harness: FunctionalHarness, ref: str) -> str | None:
    listing = _git(
        harness, "for-each-ref", "--format=%(objectname)", f"refs/harness/locks/{ref}"
    )
    return _git(harness, "cat-file", "blob", listing) if listing else None


def _drive(harness: FunctionalHarness, orchestrator: str, workflow: str) -> Mapping[str, Any]:
    """Open an orchestrator session and resolve its workflow's next step."""
    harness.invoke("start-session", sessionId=orchestrator, agent="orchestrator")
    run = harness.invoke("resolve-step", sessionId=orchestrator, workflowSlug=workflow)
    assert run.status == "step-resolution", run.stdout
    return run.report


def _start_worker(harness: FunctionalHarness, orchestrator: str, worker: str, actor: str) -> None:
    run = harness.invoke(
        "start-session", sessionId=worker, parentSessionId=orchestrator, agent=actor
    )
    assert run.status == "started", run.stdout


def _write(
    harness: FunctionalHarness, worker: str, ref: str, data: Mapping[str, Any]
) -> tuple[str, str | None]:
    """One governed write: authorize (function 8), land it, then commit-gate it (function 9)."""
    authorized = harness.invoke(
        "check-step-authorization", sessionId=worker, artifactPath=ref, action="create"
    )
    if authorized.status != "allowed":
        return authorized.status, authorized.report["authorization"]["failureMessage"]
    path = harness.workspace_dir / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    checked = harness.invoke("check-step-artifact", sessionId=worker, artifactPaths=[ref])
    return checked.status, None


def _conclude(harness: FunctionalHarness, orchestrator: str) -> Mapping[str, Any]:
    run = harness.invoke("check-step-postconditions", sessionId=orchestrator)
    return assert_contract_round_trip(harness, run)


class TestConcurrentStepInstances:
    def test_two_step_instances_on_different_artifact_instances_share_one_clone(
        self, harness: FunctionalHarness
    ) -> None:
        """Isolation level: step instances on different artifact instances coexist on
        one clone — their writes never share a path, and their promotions serialize on
        the compare-and-swap; the second rebases over the first in memory (function
        10, step 1). The working tree never leaves the workspace branch."""
        branch = _git(harness, "symbolic-ref", "HEAD")
        base = _head(harness)
        _drive(harness, "planner-o", PLANNING)
        _drive(harness, "verifier-o", VERIFICATION)
        _start_worker(harness, "planner-o", "builder-w", "builder")
        _start_worker(harness, "verifier-o", "reviewer-w", "reviewer")

        assert _write(harness, "builder-w", DRAFT_REF, {"slug": "draft", "state": "a"})[0] == "valid"
        assert _write(harness, "reviewer-w", VERIFY_REF, {"slug": "verify", "state": "b"})[0] == "valid"
        assert _head(harness) == base

        first = _conclude(harness, "planner-o")
        second = _conclude(harness, "verifier-o")

        assert (first["promotion"]["outcome"], second["promotion"]["outcome"]) == (
            "promoted",
            "promoted",
        )
        assert _head(harness) == second["promotion"]["workspaceCommit"]
        assert _git(harness, "rev-list", "--count", f"{base}..HEAD") == "2"
        assert first["promotion"]["workspaceCommit"] == _git(harness, "rev-parse", "HEAD~1")
        assert set(harness.list_committed_paths()) >= {DRAFT_REF, VERIFY_REF}
        assert _git(harness, "symbolic-ref", "HEAD") == branch
        assert _git(harness, "status", "--porcelain", "--", "report") == ""

    def test_a_lock_conflict_denies_the_second_writer_naming_the_holder(
        self, harness: FunctionalHarness
    ) -> None:
        """Function 8, invariant 8: writes are exclusive per artifact instance until
        promotion — a path another step instance holds is denied, naming the holder's
        workflow instance and step; the holder's lock and bytes are untouched."""
        planning = _drive(harness, "planner-o", PLANNING)
        _drive(harness, "verifier-o", VERIFICATION)
        _start_worker(harness, "planner-o", "builder-w", "builder")
        _start_worker(harness, "verifier-o", "reviewer-w", "reviewer")
        _write(harness, "builder-w", DRAFT_REF, {"slug": "draft", "state": "mine"})

        status, message = _write(harness, "reviewer-w", DRAFT_REF, {"slug": "draft", "state": "theirs"})

        assert status == "denied"
        assert "draft" in message
        assert planning["context"]["workflowInstanceId"] in message
        assert _lock_holder(harness, DRAFT_REF) == _step_ref(harness, "builder-w")
        assert "mine" in (harness.workspace_dir / DRAFT_REF).read_text(encoding="utf-8")

    def test_a_foreign_commit_on_a_locked_path_is_a_rebase_conflict(
        self, harness: FunctionalHarness
    ) -> None:
        """Isolation level, residuals: a foreign commit is outside the locks; if it
        changes a locked path, the promotion fails as `step-rebase-conflict` (function
        10, step 1) — journaled, workspace state untouched, the step ref kept."""
        _drive(harness, "planner-o", PLANNING)
        _start_worker(harness, "planner-o", "builder-w", "builder")
        _write(harness, "builder-w", DRAFT_REF, {"slug": "draft", "state": "mine"})
        tip = _git(harness, "rev-parse", _step_ref(harness, "builder-w"))
        harness.commit_artifact(DRAFT_REF, {"slug": "draft", "state": "foreign"})
        moved = _head(harness)

        report = _conclude(harness, "planner-o")

        assert report["outcome"]["status"] == "state-error"
        assert report["outcome"]["error"]["code"] == "step-rebase-conflict"
        assert _head(harness) == moved
        assert _git(harness, "rev-parse", _step_ref(harness, "builder-w")) == tip


class TestRecovery:
    def test_a_crash_leaves_stale_locks_that_the_resumed_step_instance_inherits(
        self, harness: FunctionalHarness
    ) -> None:
        """Function 10, invariant 7 + Abandonment: a worker session ending without a
        function-10 outcome leaves its step ref, its locks, and its bytes in place. The
        workflow instance resumed by a new orchestrator session once the old one ended
        re-resolves the step; the new step instance continues the orphaned one — its
        step ref based on the orphaned tip, its locks re-pointed — and delivers."""
        planning = _drive(harness, "planner-o", PLANNING)
        _start_worker(harness, "planner-o", "builder-w1", "builder")
        _write(harness, "builder-w1", DRAFT_REF, {"slug": "draft", "state": "first"})
        orphan = _step_ref(harness, "builder-w1")
        orphan_tip = _git(harness, "rev-parse", orphan)
        harness.invoke("end-session", sessionId="planner-o")
        assert _lock_holder(harness, DRAFT_REF) == orphan

        resumed = _drive(harness, "planner-o2", PLANNING)
        assert resumed["context"]["workflowInstanceId"] == planning["context"]["workflowInstanceId"]
        _start_worker(harness, "planner-o2", "builder-w2", "builder")
        retry = _step_ref(harness, "builder-w2")
        assert _git(harness, "rev-parse", retry) == orphan_tip

        assert _write(harness, "builder-w2", DRAFT_REF, {"slug": "draft", "state": "second"})[0] == "valid"
        assert _lock_holder(harness, DRAFT_REF) == retry
        report = _conclude(harness, "planner-o2")

        assert report["promotion"]["outcome"] == "promoted"
        assert _head(harness) == _git(harness, "rev-parse", retry)
        assert _git(harness, "for-each-ref", "refs/harness/locks/") == ""
        assert _git(harness, "rev-parse", orphan) == orphan_tip

    def test_a_live_driver_makes_the_workflow_instance_busy(
        self, harness: FunctionalHarness
    ) -> None:
        """Function 3, invariant 10: while another orchestrator session that has not
        ended holds an unfinished step resolution, a resolve-step call is refused —
        `workflow-instance-busy`, journaled without a `workflowInstanceId`."""
        _drive(harness, "planner-o", PLANNING)
        harness.invoke("start-session", sessionId="planner-o2", agent="orchestrator")

        run = harness.invoke("resolve-step", sessionId="planner-o2", workflowSlug=PLANNING)

        report = assert_contract_round_trip(harness, run)
        assert run.error_code == "workflow-instance-busy"
        assert report["context"]["workflowInstanceId"] is None

    def test_the_busy_message_gives_a_recovery_command_that_unsticks_the_instance(
        self, harness: FunctionalHarness
    ) -> None:
        """The `workflow-instance-busy` message names the holder and the installed
        recovery command; once that `end-session` runs, the next orchestrator session
        continues the workflow instance, and the ended session's later calls are refused
        (C8, fail closed)."""
        planning = _drive(harness, "planner-o", PLANNING)
        harness.invoke("start-session", sessionId="planner-o2", agent="orchestrator")
        busy = harness.invoke("resolve-step", sessionId="planner-o2", workflowSlug=PLANNING)
        message = busy.report["outcome"]["error"]["message"]

        assert "'planner-o'" in message
        assert "end-session --session-id planner-o" in message
        assert "harness.py" in message and sys.executable in message
        assert f"FRAMEWORK_DIR={harness.framework_dir}" in message

        assert harness.invoke("end-session", sessionId="planner-o").status == "ended"
        resumed = harness.invoke("resolve-step", sessionId="planner-o2", workflowSlug=PLANNING)
        assert resumed.status == "step-resolution"
        assert resumed.report["context"]["workflowInstanceId"] == planning["context"]["workflowInstanceId"]
        refused = harness.invoke("resolve-step", sessionId="planner-o", workflowSlug=PLANNING)
        assert refused.error_code == "session-ended"

    def test_a_failed_step_instance_is_withheld_and_its_retry_continues_its_work(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Function 10, step 5 + function 0, invariant 6: a failing step instance is
        withheld — workspace state and its locks untouched, its output kept on its ref.
        The retry's step ref starts from that tip (the base is unchanged) and inherits
        its lock; once its own write passes, the promotion carries both step instances'
        commits, in order."""
        harness = build_harness(workflows={"approval": APPROVAL_WORKFLOW})
        _drive(harness, "approver-o", "approval")
        _start_worker(harness, "approver-o", "builder-w1", "builder")
        _write(harness, "builder-w1", DRAFT_REF, {"slug": "draft", "state": "draft"})
        head = _head(harness)
        first = _step_ref(harness, "builder-w1")

        failed = _conclude(harness, "approver-o")

        assert failed["outcome"]["status"] == "fail"
        assert failed["promotion"] == {"outcome": "withheld", "workspaceCommit": head}
        assert _head(harness) == head
        assert _lock_holder(harness, DRAFT_REF) == first
        first_tip = _git(harness, "rev-parse", first)

        retried = harness.invoke("resolve-step", sessionId="approver-o", workflowSlug="approval")
        assert retried.report["step"]["slug"] == "draft"
        _start_worker(harness, "approver-o", "builder-w2", "builder")
        retry = _step_ref(harness, "builder-w2")
        assert _git(harness, "rev-parse", retry) == first_tip
        assert _write(harness, "builder-w2", DRAFT_REF, {"slug": "draft", "state": "approved"})[0] == "valid"
        assert _lock_holder(harness, DRAFT_REF) == retry

        passed = _conclude(harness, "approver-o")

        assert passed["promotion"]["outcome"] == "promoted"
        assert _git(harness, "rev-list", "--count", f"{head}..HEAD") == "2"
        assert _git(harness, "rev-parse", "HEAD~1") == first_tip
        assert _git(harness, "for-each-ref", "refs/harness/locks/") == ""

    def test_an_inherited_but_unrewritten_artifact_does_not_deliver(
        self, harness: FunctionalHarness
    ) -> None:
        """Function 10, invariant 5 (b): a retry built on its predecessor's ref whose
        own commits do not change the artifact instance does not deliver."""
        _drive(harness, "planner-o", PLANNING)
        _start_worker(harness, "planner-o", "builder-w1", "builder")
        _write(harness, "builder-w1", DRAFT_REF, {"slug": "draft", "state": "first"})
        harness.invoke("end-session", sessionId="planner-o")
        _drive(harness, "planner-o2", PLANNING)
        _start_worker(harness, "planner-o2", "builder-w2", "builder")

        report = _conclude(harness, "planner-o2")

        assert report["outcome"]["status"] == "fail"
        assert "own commits" in report["artifactDelivery"]["failureMessage"]
        assert report["promotion"]["outcome"] == "withheld"


class TestWriteBoundary:
    def test_a_write_from_outside_a_step_instance_is_denied(
        self, harness: FunctionalHarness
    ) -> None:
        """Function 8, invariant 7: a write from an orchestrator (root) session is
        denied — every artifact write happens inside a governed step instance."""
        harness.invoke("start-session", sessionId="planner-o", agent="orchestrator")

        status, message = _write(harness, "planner-o", DRAFT_REF, {"slug": "draft", "state": "x"})

        assert status == "denied"
        assert "no step in flight" in message
        assert _git(harness, "for-each-ref", "refs/harness/locks/") == ""
