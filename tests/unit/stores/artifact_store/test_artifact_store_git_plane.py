"""Unit tests for the artifact store's step transactions: step refs, locks, promotion.

Spec (Workspace Git plane; functions 0, 8, 9, 10): every operation runs on a real
tmp-dir Git repository — `HEAD` and the working tree never leave the workspace
branch, and every `refs/harness/*` update is a compare-and-swap.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from config.workspace_layout import WorkspaceLayout
from errors import StateError
from stores.artifact_store import ArtifactLock, ArtifactStore, StepRef
from stores.artifact_store.artifact_lock import build_lock_ref_name, parse_lock_ref_name

INSTANCE = "flow-01AB"


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=workspace, text=True, capture_output=True, check=True
    ).stdout.strip()


def _write(workspace: Path, ref: str, slug: str) -> None:
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"slug": slug}) + "\n", encoding="utf-8")


def _foreign_commit(workspace: Path, ref: str, slug: str) -> str:
    """Commit straight onto the workspace branch — what a human or a script does."""
    _write(workspace, ref, slug)
    _git(workspace, "add", "--", ref)
    _git(workspace, "commit", "-q", "-m", f"foreign: {ref}")
    return _git(workspace, "rev-parse", "HEAD")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    repo = tmp_path / "workspace"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "baseline")
    return repo


@pytest.fixture()
def store(workspace: Path, tmp_path: Path) -> ArtifactStore:
    schema = tmp_path / "sample.artifact.schema.json"
    schema.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gsmarc://tests/contracts/step-transactions-sample/v1",
                "$ref": "gsmarc://saf/contracts/artifact/v1",
            }
        ),
        encoding="utf-8",
    )
    layout = WorkspaceLayout(
        nodes=(
            FolderNode(
                slug="sample",
                description="Samples.",
                children=(
                    ArtifactNode(
                        slug="<name>.json",
                        description="One sample.",
                        cardinality="0..*",
                        artifact="sample",
                        template="sample",
                    ),
                ),
            ),
        )
    )
    return ArtifactStore(workspace, {"sample": schema}, layout)


def _commit_on(store: ArtifactStore, workspace: Path, step_ref: StepRef, ref: str, slug: str) -> str:
    _write(workspace, ref, slug)
    commit = store.commit_artifacts((Path(ref),), session_id=step_ref.session_id, step_ref=step_ref)
    assert commit is not None
    return commit.commit_id


class TestStepRefCreation:
    def test_a_step_ref_is_created_from_the_branch_tip_without_any_checkout(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 0, invariant 6): the step ref is created without any checkout —
        `HEAD` stays on the workspace branch and the working tree is untouched."""
        head = _git(workspace, "rev-parse", "HEAD")
        branch = _git(workspace, "symbolic-ref", "HEAD")

        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")

        assert step_ref.name == f"refs/harness/steps/{INSTANCE}/draft/worker-1"
        assert _git(workspace, "rev-parse", step_ref.name) == head
        assert _git(workspace, "symbolic-ref", "HEAD") == branch
        assert store.find_step_ref("worker-1") == step_ref

    def test_creation_is_create_only(self, store: ArtifactStore) -> None:
        """Spec (function 0, invariant 6): nothing is renamed, reused, or deleted — a
        second creation of the same step ref fails instead of overwriting it."""
        store.create_step_ref(INSTANCE, "draft", "worker-1")

        with pytest.raises(Exception):
            store.create_step_ref(INSTANCE, "draft", "worker-1")

    def test_a_retry_on_an_unchanged_base_continues_the_previous_step_ref(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 0, invariant 6): a retry's step ref is created from the
        previous step instance's ref tip when the workspace branch has not moved since
        that step instance's base — the retry continues the failed work."""
        previous = store.create_step_ref(INSTANCE, "draft", "worker-1")
        tip = _commit_on(store, workspace, previous, "sample/a.json", "a")

        retry = store.create_step_ref(INSTANCE, "draft", "worker-2", previous)

        assert _git(workspace, "rev-parse", retry.name) == tip

    def test_a_retry_after_the_branch_moved_starts_from_the_branch_tip(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 0, invariant 6): otherwise — the branch moved — the retry's
        step ref is created from the workspace branch tip."""
        previous = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, previous, "sample/a.json", "a")
        moved = _foreign_commit(workspace, "sample/other.json", "other")

        retry = store.create_step_ref(INSTANCE, "draft", "worker-2", previous)

        assert _git(workspace, "rev-parse", retry.name) == moved

    def test_the_logs_path_is_excluded_from_git(self, workspace: Path, store: ArtifactStore) -> None:
        """Spec (Logging): the harness adds the logs path to `.git/info/exclude`, once."""
        store.create_step_ref(INSTANCE, "draft", "worker-1")
        store.create_step_ref(INSTANCE, "draft", "worker-2")
        (workspace / "logs").mkdir()
        (workspace / "logs" / "worker-1.log.jsonl").write_text("{}\n", encoding="utf-8")

        exclude = (workspace / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert exclude.splitlines().count("/logs/") == 1
        assert _git(workspace, "status", "--porcelain", "--untracked-files=all") == ""


class TestCommitAndRevert:
    def test_a_commit_moves_only_the_step_ref(self, workspace: Path, store: ArtifactStore) -> None:
        """Spec (function 9, invariant 3): built with a temporary index plus
        `commit-tree` — `HEAD`, the working tree, and the workspace's index never move."""
        head = _git(workspace, "rev-parse", "HEAD")
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")

        commit_id = _commit_on(store, workspace, step_ref, "sample/a.json", "a")

        assert _git(workspace, "rev-parse", step_ref.name) == commit_id
        assert _git(workspace, "rev-parse", "HEAD") == head
        assert _git(workspace, "diff", "--cached", "--name-only") == ""
        assert (workspace / "sample" / "a.json").is_file()
        assert list((workspace / ".git").glob("harness-index-*")) == []

    def test_revert_restores_from_the_step_ref_when_it_changed_the_path(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 9, invariant 2): restored from the step ref tip when the step
        ref changed the path since its merge base with the workspace branch."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, step_ref, "sample/a.json", "valid")
        _write(workspace, "sample/a.json", "INVALID")

        finding = store.revert_artifact(Path("sample/a.json"), step_ref)

        assert (finding.rule, finding.origin) == ("restored", step_ref.name)
        assert json.loads((workspace / "sample" / "a.json").read_text())["slug"] == "valid"
        assert _git(workspace, "diff", "--cached", "--name-only") == ""

    def test_revert_restores_from_head_when_the_step_ref_left_the_path_alone(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 9, invariant 2): otherwise from `HEAD` when tracked there."""
        _foreign_commit(workspace, "sample/a.json", "committed")
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _write(workspace, "sample/a.json", "INVALID")

        finding = store.revert_artifact(Path("sample/a.json"), step_ref)

        assert (finding.rule, finding.origin) == ("restored", "HEAD")
        assert json.loads((workspace / "sample" / "a.json").read_text())["slug"] == "committed"

    def test_revert_deletes_a_new_path(self, workspace: Path, store: ArtifactStore) -> None:
        """Spec (function 9, invariant 2): otherwise deleted — a new path."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _write(workspace, "sample/new.json", "new")

        finding = store.revert_artifact(Path("sample/new.json"), step_ref)

        assert (finding.rule, finding.origin) == ("deleted", None)
        assert not (workspace / "sample" / "new.json").exists()


class TestArtifactLocks:
    def test_a_lock_name_encodes_the_path_where_git_requires(self) -> None:
        """Spec (Terminology, Artifact lock): `refs/harness/locks/<artifactPath>`,
        encoded where Git's ref-name rules require — reversibly."""
        for path in ("sample/a.json", ".hidden/x..y.lock", "a b/c:d?e*f[g/é.", "x/y."):
            name = build_lock_ref_name(path)
            assert subprocess.run(("git", "check-ref-format", name)).returncode == 0
            assert parse_lock_ref_name(name) == path

    def test_a_claim_is_create_only(self, workspace: Path, store: ArtifactStore) -> None:
        """Spec (function 8, invariant 8): a create-only ref update (expected old value:
        none) to a blob naming the acting step ref — a second create-only claim fails."""
        first = store.create_step_ref(INSTANCE, "draft", "worker-1")
        second = store.create_step_ref("other-01CD", "review", "worker-2")

        assert store.claim_artifact_lock("sample/a.json", first, None) is True
        assert store.claim_artifact_lock("sample/a.json", second, None) is False

        lock = store.find_artifact_lock("sample/a.json")
        assert lock is not None
        assert lock.holder == first.name
        assert _git(workspace, "cat-file", "-t", lock.name) == "blob"

    def test_re_pointing_is_a_compare_and_swap_from_the_previous_holder(
        self, store: ArtifactStore
    ) -> None:
        """Spec (function 8, invariant 8): a retry re-points an inherited lock by a
        compare-and-swap from the previous holder; a stale expectation fails."""
        previous = store.create_step_ref(INSTANCE, "draft", "worker-1")
        retry = store.create_step_ref(INSTANCE, "draft", "worker-2")
        store.claim_artifact_lock("sample/a.json", previous, None)
        held = store.find_artifact_lock("sample/a.json")
        stale = ArtifactLock(artifact_path="sample/a.json", holder="x", value="0" * 40)

        assert store.claim_artifact_lock("sample/a.json", retry, stale) is False
        assert store.claim_artifact_lock("sample/a.json", retry, held) is True
        assert store.find_artifact_lock("sample/a.json").holder == retry.name

    def test_a_lock_counts_as_released_once_its_holder_is_promoted(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 8, invariant 8): released when the holder's tip is a commit of
        that step instance reachable from the workspace branch — never before promotion."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, step_ref, "sample/a.json", "a")
        assert store.is_step_ref_promoted(step_ref.name) is False

        store.promote_step_ref(store.find_step_ref(step_ref.session_id), _git(workspace, "rev-parse", "HEAD"))

        assert store.is_step_ref_promoted(step_ref.name) is True

    def test_a_step_ref_without_its_own_commit_is_never_promoted(
        self, store: ArtifactStore
    ) -> None:
        """Spec (function 8, invariant 8): the tip must be a commit OF that step
        instance — an empty step ref sitting on `HEAD` is not promoted."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")

        assert store.is_step_ref_promoted(step_ref.name) is False


class TestRebaseAndPromotion:
    def test_a_moved_branch_is_rebased_over_in_memory_keeping_history_linear(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 10, step 1): `git merge-tree`, no working tree; the step ref
        moves to the rebased commits, their messages (the `sessionId`) kept."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, step_ref, "sample/a.json", "a")
        _commit_on(store, workspace, step_ref, "sample/a.json", "a2")
        head = _foreign_commit(workspace, "sample/other.json", "other")

        rebased = store.rebase_step_ref(step_ref, head)

        assert _git(workspace, "rev-parse", rebased.name) == rebased.tip
        assert _git(workspace, "rev-list", "--count", f"{head}..{rebased.tip}") == "2"
        assert _git(workspace, "merge-base", "--is-ancestor", head, rebased.tip) == ""
        assert "session-id: worker-1" in _git(workspace, "log", "-1", "--format=%B", rebased.tip)
        assert set(store.list_commit_changes(head, rebased.tip)) == {"sample/a.json"}
        assert _git(workspace, "show", f"{rebased.tip}:sample/a.json").find('"a2"') >= 0

    def test_a_path_changed_on_both_sides_is_a_rebase_conflict(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 10, step 1): a path changed both by the step ref and by the
        commits it is rebased over is `step-rebase-conflict`; the step ref is unmoved."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        tip = _commit_on(store, workspace, step_ref, "sample/a.json", "mine")
        head = _foreign_commit(workspace, "sample/a.json", "theirs")

        with pytest.raises(StateError) as conflict:
            store.rebase_step_ref(step_ref, head)

        assert conflict.value.code == "step-rebase-conflict"
        assert _git(workspace, "rev-parse", step_ref.name) == tip

    def test_promotion_is_one_compare_and_swap_fast_forward(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 10, step 3): ONE atomic fast-forward of the workspace branch to
        the step ref tip — the working tree is not rewritten — then the promoted paths'
        index entries are synced."""
        head = _git(workspace, "rev-parse", "HEAD")
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        tip = _commit_on(store, workspace, step_ref, "sample/a.json", "a")

        promoted = store.promote_step_ref(store.find_step_ref(step_ref.session_id), head)
        store.sync_index(store.list_commit_changes(head, promoted), promoted)

        assert promoted == tip
        assert _git(workspace, "rev-parse", "HEAD") == tip
        assert _git(workspace, "status", "--porcelain", "--untracked-files=all") == ""

    def test_a_branch_moved_before_the_compare_and_swap_is_a_rebase_conflict(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (function 10, step 3): if the compare-and-swap fails because the branch
        moved, `step-rebase-conflict` — the branch is untouched."""
        head = _git(workspace, "rev-parse", "HEAD")
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, step_ref, "sample/a.json", "a")
        moved = _foreign_commit(workspace, "sample/other.json", "other")

        with pytest.raises(StateError) as conflict:
            store.promote_step_ref(store.find_step_ref(step_ref.session_id), head)

        assert conflict.value.code == "step-rebase-conflict"
        assert _git(workspace, "rev-parse", "HEAD") == moved

    def test_releasing_deletes_only_the_promoted_step_instances_locks(
        self, store: ArtifactStore
    ) -> None:
        """Spec (function 10, step 4): the step instance's artifact locks are deleted."""
        mine = store.create_step_ref(INSTANCE, "draft", "worker-1")
        other = store.create_step_ref("other-01CD", "review", "worker-2")
        store.claim_artifact_lock("sample/a.json", mine, None)
        store.claim_artifact_lock("sample/b.json", other, None)

        store.release_artifact_locks(mine)

        assert store.find_artifact_lock("sample/a.json") is None
        assert store.find_artifact_lock("sample/b.json").holder == other.name


class TestStepState:
    def test_discovery_reads_the_step_state_at_a_revision(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (C1; function 10, invariant 1): postconditions read the step state —
        committed, never the working tree — while workspace state is unchanged."""
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        tip = _commit_on(store, workspace, step_ref, "sample/a.json", "a")

        assert store.discover_workspace_artifacts(None) == ()
        assert [artifact.ref for artifact in store.discover_workspace_artifacts(None, tip)] == [
            "sample/a.json"
        ]
        assert store.discover_workspace_artifacts(frozenset({"other"}), tip) == ()


class TestSweep:
    def test_the_sweep_reports_step_refs_locks_and_unlocked_dirty_paths(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (Internal validation; Abandonment): step refs as step instances, every
        held lock — stale ones included — with its holder, and working-tree paths that
        differ from `HEAD` without a lock. The sweep never mutates."""
        head = _git(workspace, "rev-parse", "HEAD")
        promoted = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, promoted, "sample/a.json", "a")
        store.claim_artifact_lock("sample/a.json", promoted, None)
        store.promote_step_ref(store.find_step_ref(promoted.session_id), head)
        abandoned = store.create_step_ref(INSTANCE, "review", "worker-2")
        _commit_on(store, workspace, abandoned, "sample/b.json", "b")
        store.claim_artifact_lock("sample/b.json", abandoned, None)
        _write(workspace, "sample/loose.json", "loose")
        refs_before = _git(workspace, "for-each-ref")

        sweep = store.sweep_workspace()

        assert {ref.session_id for ref in sweep.step_refs} == {"worker-1", "worker-2"}
        assert sweep.promoted_step_refs == frozenset({promoted.name})
        assert {(lock.artifact_path, lock.holder) for lock in sweep.artifact_locks} == {
            ("sample/a.json", promoted.name),
            ("sample/b.json", abandoned.name),
        }
        assert sweep.released_locks == frozenset({"sample/a.json"})
        assert "sample/loose.json" in sweep.unlocked_dirty_paths
        assert "sample/b.json" not in sweep.unlocked_dirty_paths
        assert sweep.findings == ()
        assert _git(workspace, "for-each-ref") == refs_before


class TestPromotionOfTheEvaluatedState:
    def test_a_commit_landing_after_the_evaluation_is_never_promoted(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Function 10, invariant 6: workspace state advances to EXACTLY the evaluated
        state — a commit onto the step ref after its evaluation fails the promotion
        (`step-rebase-conflict`) and the branch does not move."""
        head = _git(workspace, "rev-parse", "HEAD")
        step_ref = store.create_step_ref(INSTANCE, "draft", "worker-1")
        _commit_on(store, workspace, step_ref, "sample/a.json", "a")
        evaluated = store.rebase_step_ref(store.find_step_ref("worker-1"), head)
        _commit_on(store, workspace, evaluated, "sample/late.json", "late")

        with pytest.raises(StateError) as conflict:
            store.promote_step_ref(evaluated, head)

        assert conflict.value.code == "step-rebase-conflict"
        assert _git(workspace, "rev-parse", "HEAD") == head

    def test_a_promoted_retry_releases_the_locks_it_inherited(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Terminology (Step ref, Artifact lock) + function 10, step 4: a retry inherits
        its predecessor's locks; once it is promoted, those count as its own and are
        released — even one it never re-pointed."""
        head = _git(workspace, "rev-parse", "HEAD")
        previous = store.create_step_ref(INSTANCE, "draft", "worker-1")
        store.claim_artifact_lock("sample/untouched.json", previous, None)
        retry = store.create_step_ref(INSTANCE, "draft", "worker-2", previous)
        other = store.create_step_ref("other-01CD", "draft", "worker-3")
        store.claim_artifact_lock("sample/other.json", other, None)
        _commit_on(store, workspace, retry, "sample/a.json", "a")
        promoted = store.find_step_ref("worker-2")
        store.promote_step_ref(promoted, head)

        store.release_artifact_locks(promoted)

        assert store.find_artifact_lock("sample/untouched.json") is None
        assert store.find_artifact_lock("sample/other.json").holder == other.name
