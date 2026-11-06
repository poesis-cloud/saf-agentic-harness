"""The store owning the artifact side: committed state is workspace state (C6)."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import replace
from itertools import chain
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

from config.workspace_layout import WorkspaceLayout
from errors import ConfigurationError, StateError, SystemFailureError
from stores.artifact_store.artifact import Artifact
from stores.artifact_store.artifact_lock import (
    LOCK_REF_NAMESPACE,
    ArtifactLock,
    build_lock_ref_name,
    parse_lock_ref_name,
)
from stores.artifact_store.commit import Commit
from stores.artifact_store.finding import Finding
from stores.artifact_store.step_ref import STEP_REF_NAMESPACE, StepRef, build_step_ref_name
from stores.artifact_store.workspace_sweep import WorkspaceSweep
from utils.exclusive_file_lock import ExclusiveFileLock
from utils.json_loader import JsonLoader
from utils.markdown_loader import MarkdownLoader
from utils.schema_validator import SchemaValidator, ValidationErrorRecord
from utils.yaml_loader import YamlLoader

_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
_MARKDOWN_SUFFIX = ".md"
_YAML_SUFFIXES = (".yaml", ".yml")
_UNRESOLVABLE_RESOURCE = "unresolvable-resource"
_HEAD = "HEAD"
_LOGS_EXCLUDE_PATTERN = "/logs/"
_STEP_REBASE_CONFLICT = "step-rebase-conflict"
_SESSION_ID_TRAILER = "session-id: "
_REGISTRATION_LOCK_NAME = "harness-registration.lock"


class ArtifactStore:
    """Read committed artifacts, validate staged ones, and transact on the Git plane.

    Git is the transaction mechanism (Workspace Git plane): `HEAD` — the
    workspace branch, which stays checked out — is workspace state, and the
    working tree is the write staging area. The harness's only deliberate Git
    actions live here: step-ref creation (function 0); claiming and re-pointing
    artifact locks (function 8); committing a validated set onto the step ref
    and reverting an invalid one (function 9); the in-memory rebase, the
    compare-and-swap promotion, and the index sync (function 10). None of them
    moves `HEAD` other than by promotion, or switches the working tree.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        artifact_schemas: Mapping[str, Path],
        workspace_layout: WorkspaceLayout,
        schema_validator: SchemaValidator | None = None,
        json_loader: JsonLoader | None = None,
        markdown_loader: MarkdownLoader | None = None,
        yaml_loader: YamlLoader | None = None,
    ) -> None:
        """Create the store over a workspace repository, compiling contracts once.

        `artifact_schemas` maps an artifact slug to its schema file; which slug a
        given workspace path is bound to is the `workspace_layout`'s answer.
        """
        self._workspace_dir = Path(workspace_dir)
        self._workspace_layout = workspace_layout
        self._json_loader = json_loader or JsonLoader()
        self._markdown_loader = markdown_loader or MarkdownLoader()
        self._yaml_loader = yaml_loader or YamlLoader()
        schema_paths = tuple(Path(path) for path in artifact_schemas.values())
        self._schema_ids: Mapping[str, str] = MappingProxyType(
            {
                kind: self._read_schema_id(Path(path))
                for kind, path in artifact_schemas.items()
            }
        )
        self._validator = schema_validator or SchemaValidator.compile_contracts(
            chain(sorted(_CONTRACTS_DIR.rglob("*.schema.json")), schema_paths)
        )

    # -- committed state ---------------------------------------------------------

    def discover_artifacts(
        self, selector: str, revision: str = _HEAD
    ) -> tuple[Artifact, ...]:
        """Read committed artifacts under a path selector, raising on any invalid one.

        C6: `discover()` raises rather than yield a schema-invalid artifact, so
        every reader of committed state fails loudly on foreign corruption.
        `revision` is workspace state (`HEAD`) or, for function 10, the step state.
        """
        return self._read_committed(
            (
                (path.as_posix(), self._resolve_kind(path.as_posix()))
                for path in self.scan_raw_paths(Path(selector), revision)
            ),
            revision,
        )

    def discover_artifacts_by_kind(
        self, kind: str, revision: str = _HEAD
    ) -> tuple[Artifact, ...]:
        """Read every committed instance of one artifact kind, raising on any invalid one.

        Membership is the workspace layout's answer for each committed path —
        never a path prefix. Paths the layout binds to no artifact (the logs
        plane, foreign files) are not instances of any kind.
        """
        if kind not in self._schema_ids:
            raise StateError(
                "artifact-schema-unresolved",
                f"No artifact schema is declared for the artifact slug '{kind}'.",
                False,
            )
        return self.discover_workspace_artifacts(frozenset({kind}), revision)

    def discover_workspace_artifacts(
        self, kinds: frozenset[str] | None, revision: str = _HEAD
    ) -> tuple[Artifact, ...]:
        """Read the committed artifact instances of some kinds — every kind when `None`.

        Function 5, invariant 2: `workspace` holds the artifact instances of the
        evaluated state; the condition's guards may narrow which kinds it reads.
        """
        refs: list[tuple[str, str]] = []
        for path in self.scan_raw_paths(Path("."), revision):
            ref = path.as_posix()
            resolved = self._find_kind(ref)
            if resolved is not None and (kinds is None or resolved in kinds):
                refs.append((ref, resolved))
        return self._read_committed(refs, revision)

    def scan_raw_paths(self, scope: Path, revision: str = _HEAD) -> Iterator[Path]:
        """Enumerate the raw universe of committed paths under a scope.

        The sweep's reader: unlike `discover_artifacts` it never validates, so
        whoever must enumerate invalids can (internal validation, C6).
        """
        listing = self._run_git(
            "ls-tree", "-r", "--name-only", revision, "--", Path(scope).as_posix()
        )
        paths = tuple(Path(line) for line in sorted(listing.splitlines()) if line)
        return iter(paths)

    def read_workspace_commit(self) -> str:
        """Read the workspace branch tip — the commit workspace state is."""
        return self._run_git("rev-parse", "--verify", f"{_HEAD}^{{commit}}")

    def resolve_artifact_kind(self, artifact_path: str) -> str | None:
        """Resolve a workspace path to its artifact kind, or none if the layout binds none."""
        return self._find_kind(artifact_path)

    # -- staging (functions 8 and 9) ---------------------------------------------

    def validate_artifact(self, artifact_path: Path) -> tuple[Finding, ...]:
        """Validate the staged bytes of one path against its matched schema.

        Function 9 validates the working tree — the staging area — before the
        commit gate commits it; the path exists because the write just landed.
        """
        ref = Path(artifact_path).as_posix()
        kind = self._resolve_kind(ref)
        staged = (self._workspace_dir / artifact_path).read_text(encoding="utf-8")
        return self._validate_document(ref, kind, staged)[0]

    def is_staging_clean(self, artifact_path: Path) -> bool:
        """Answer whether a target path is absent, or tracked and clean against `HEAD`.

        Function 8's invariant 5 staging baseline: a dirty tracked target and a
        pre-existing untracked target are both unclean.
        """
        status = self._run_git(
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            Path(artifact_path).as_posix(),
        )
        return status == ""

    def commit_artifacts(
        self, artifact_paths: Sequence[Path], *, session_id: str, step_ref: StepRef
    ) -> Commit | None:
        """Commit one validated write set onto the step ref as ONE commit.

        Function 9 invariant 3: 1 validated set = 1 commit, attributed to the
        acting session, built from the validated working-tree bytes over the
        step ref tip with a temporary index plus `commit-tree`, then a
        compare-and-swap update of the step ref — `HEAD`, the working tree, and
        the workspace's own index never move. Invariant 4: a set byte-identical
        to the step ref tip creates no commit and returns `None`.
        """
        refs = tuple(Path(artifact_path).as_posix() for artifact_path in artifact_paths)
        if not refs:
            return None
        tip = self._read_step_ref_tip(step_ref)
        with self._temporary_index() as index_env:
            self._run_git("read-tree", tip, env=index_env)
            present = tuple(ref for ref in refs if (self._workspace_dir / ref).is_file())
            absent = tuple(ref for ref in refs if ref not in present)
            if present:
                self._run_git("update-index", "--add", "--", *present, env=index_env)
            if absent:
                self._run_git(
                    "update-index", "--force-remove", "--", *absent, env=index_env
                )
            tree = self._run_git("write-tree", env=index_env)
        changed = self._list_tree_changes(f"{tip}^{{tree}}", tree)
        if not changed:
            return None
        commit_id = self._run_git(
            "commit-tree",
            tree,
            "-p",
            tip,
            "-m",
            f"artifact: commit validated write set\n\n{_SESSION_ID_TRAILER}{session_id}",
        )
        self._run_git("update-ref", step_ref.name, commit_id, tip)
        return Commit(commit_id=commit_id, paths=changed)

    def revert_artifact(
        self, artifact_path: Path, step_ref: StepRef | None = None
    ) -> Finding:
        """Discard one staged path from the working tree.

        Function 9 invariant 2: restored from the step ref tip when the step ref
        changed it since its merge base with the workspace branch, otherwise from
        `HEAD` when tracked there, otherwise deleted. The discard never touches a
        commit, and restoring from the step ref leaves the workspace's index alone.
        """
        ref = Path(artifact_path).as_posix()
        if step_ref is not None and self._is_changed_by_step_ref(ref, step_ref):
            tip = self._read_step_ref_tip(step_ref)
            if self._probe_git("cat-file", "-e", f"{tip}:{ref}"):
                self._run_git("restore", f"--source={tip}", "--worktree", "--", ref)
                return Finding(
                    source=ref,
                    rule="restored",
                    message=f"Restored '{ref}' from {step_ref.name}.",
                    origin=step_ref.name,
                )
        elif self._probe_git("cat-file", "-e", f"{_HEAD}:{ref}"):
            self._run_git(
                "restore", f"--source={_HEAD}", "--staged", "--worktree", "--", ref
            )
            return Finding(
                source=ref,
                rule="restored",
                message=f"Restored '{ref}' from HEAD.",
                origin=_HEAD,
            )
        self._run_git("rm", "--cached", "--force", "--ignore-unmatch", "--quiet", "--", ref)
        (self._workspace_dir / artifact_path).unlink(missing_ok=True)
        return Finding(
            source=ref, rule="deleted", message=f"Deleted newly staged '{ref}'."
        )

    # -- step refs (functions 0 and 10) ------------------------------------------

    def create_step_ref(
        self,
        workflow_instance_id: str,
        step_slug: str,
        session_id: str,
        previous: StepRef | None = None,
    ) -> StepRef:
        """Create a step instance's step ref, create-only, without any checkout.

        Function 0, invariant 6: from the previous, unpromoted step instance's
        ref tip — a retry, which continues it — when the workspace branch has
        not moved since that step instance's base; otherwise from the workspace
        branch tip. The logs path is excluded from Git first (Logging).
        """
        self.exclude_logs_path()
        head = self.read_workspace_commit()
        base = head
        if previous is not None:
            previous_tip = self._read_step_ref_tip(previous)
            if self._is_ancestor(head, previous_tip):
                base = previous_tip
        name = build_step_ref_name(workflow_instance_id, step_slug, session_id)
        self._run_git("update-ref", "-m", "harness: create step ref", name, base, "")
        return StepRef(
            workflow_instance_id=workflow_instance_id,
            step_slug=step_slug,
            session_id=session_id,
            tip=base,
        )

    def find_step_ref(self, session_id: str) -> StepRef | None:
        """Find the step ref of the step instance a worker session IS, if it has one."""
        for step_ref in self.list_step_refs():
            if step_ref.session_id == session_id:
                return step_ref
        return None

    def list_step_refs(
        self, workflow_instance_id: str | None = None, step_slug: str | None = None
    ) -> tuple[StepRef, ...]:
        """List the step refs, optionally of one workflow instance's step."""
        prefix = STEP_REF_NAMESPACE
        if workflow_instance_id is not None:
            prefix += f"{workflow_instance_id}/"
            if step_slug is not None:
                prefix += f"{step_slug}/"
        found: list[StepRef] = []
        for name, oid in self._list_refs(prefix):
            parsed = StepRef.parse_step_ref(name, oid)
            if parsed is not None:
                found.append(parsed)
        return tuple(found)

    def is_step_ref_promoted(self, step_ref_name: str) -> bool:
        """Tell whether a step instance was promoted, from Git alone.

        Function 8, invariant 8: its step ref's tip is a commit of that step
        instance (its worker session's `sessionId` in the commit message)
        reachable from the workspace branch — which cannot hold before its
        promotion, since its own commits reach the branch only by promotion.
        """
        step_ref = StepRef.parse_step_ref(step_ref_name, "")
        if step_ref is None:
            return False
        tip = self._invoke_git(("rev-parse", "--verify", "--quiet", step_ref_name), check=False)
        if tip.returncode != 0:
            return False
        commit = tip.stdout.strip()
        message = self._run_git("log", "-1", "--format=%B", commit)
        if f"{_SESSION_ID_TRAILER}{step_ref.session_id}" not in message.splitlines():
            return False
        return self._is_ancestor(commit, self.read_workspace_commit())

    def list_commit_changes(self, old: str, new: str) -> tuple[str, ...]:
        """List the paths that differ between two commits, sorted."""
        return self._list_tree_changes(f"{old}^{{tree}}", f"{new}^{{tree}}")

    def rebase_step_ref(self, step_ref: StepRef, onto: str) -> StepRef:
        """Rebase the step instance's commits onto the moved workspace branch, in memory.

        Function 10, step 1: replayed with `git merge-tree --write-tree` (Git ≥
        2.38), no working tree, history kept linear, and the step ref moved to
        the rebased commits, onto `onto` — the workspace branch tip read by the
        caller, which the promotion's compare-and-swap then expects. A path
        changed both by the step ref and by the
        commits it is rebased over — or any merge conflict — is `state-error`
        (`step-rebase-conflict`): the rebase is abandoned, the step ref unmoved.
        """
        tip = self._read_step_ref_tip(step_ref)
        head = onto
        if self._is_ancestor(head, tip):
            return replace(step_ref, tip=tip)
        base = self._run_git("merge-base", head, tip)
        step_paths = set(self._list_tree_changes(f"{base}^{{tree}}", f"{tip}^{{tree}}"))
        branch_paths = set(self._list_tree_changes(f"{base}^{{tree}}", f"{head}^{{tree}}"))
        both = sorted(step_paths & branch_paths)
        if both:
            raise StateError(
                _STEP_REBASE_CONFLICT,
                f"Step ref '{step_ref.name}' cannot be rebased onto the moved workspace "
                f"branch: {', '.join(both)} changed on both sides.",
                False,
            )
        listing = self._run_git("rev-list", "--reverse", f"{base}..{tip}")
        parent = head
        for commit in (line for line in listing.splitlines() if line):
            tree = self._merge_tree(head, commit, step_ref)
            parent = self._replay_commit(commit, tree, parent)
        self._run_git(
            "update-ref", "-m", "harness: rebase step ref", step_ref.name, parent, tip
        )
        return replace(step_ref, tip=parent)

    def promote_step_ref(self, step_ref: StepRef, expected_head: str) -> str:
        """Fast-forward the workspace branch to the evaluated step state by compare-and-swap.

        Function 10, step 3 — THE acceptance point: ONE atomic ref transaction
        moving the branch to `step_ref.tip` — exactly the evaluated state
        (invariant 6) — conditional on the branch still holding `expected_head`
        and on the step ref still pointing at that tip. A branch that moved
        under the step instance, or a step ref that moved after its evaluation,
        is `state-error` (`step-rebase-conflict`); nothing is moved.
        """
        tip = step_ref.tip
        # One `--stdin` transaction is atomic; NUL-terminated (`-z`) so that no
        # newline translation of a text-mode pipe can reach Git (Windows).
        transaction = (
            f"verify {step_ref.name}\0{tip}\0"
            f"update {_HEAD}\0{tip}\0{expected_head}\0"
        )
        updated = self._invoke_git(
            ("update-ref", "-m", f"harness: promote {step_ref.name}", "-z", "--stdin"),
            check=False,
            stdin=transaction,
        )
        if updated.returncode == 0:
            return tip
        if self.read_workspace_commit() != expected_head:
            raise StateError(
                _STEP_REBASE_CONFLICT,
                f"The workspace branch moved under step ref '{step_ref.name}' before "
                f"its promotion's compare-and-swap.",
                False,
            )
        if self._read_step_ref_tip(step_ref) != tip:
            raise StateError(
                _STEP_REBASE_CONFLICT,
                f"Step ref '{step_ref.name}' moved after its step state was evaluated; "
                f"the unevaluated commits are not promoted.",
                False,
            )
        raise SystemFailureError(
            "git-failed",
            f"git update-ref HEAD failed: {(updated.stderr or updated.stdout).strip()}",
            True,
        )

    def sync_index(self, artifact_paths: Sequence[str], commit: str) -> None:
        """Sync the workspace index entries of the promoted paths to the new `HEAD`.

        Function 10, step 4: the working tree already holds the promoted bytes, so
        only these index entries move; the working tree is never rewritten.
        """
        if artifact_paths:
            self._run_git("reset", "-q", commit, "--", *artifact_paths)

    # -- artifact locks (functions 8 and 10) -------------------------------------

    def find_artifact_lock(self, artifact_path: str) -> ArtifactLock | None:
        """Read the artifact lock of one path, if any step ref holds it."""
        name = build_lock_ref_name(artifact_path)
        probed = self._invoke_git(("rev-parse", "--verify", "--quiet", name), check=False)
        if probed.returncode != 0:
            return None
        value = probed.stdout.strip()
        return ArtifactLock(
            artifact_path=artifact_path,
            holder=self._run_git("cat-file", "blob", value),
            value=value,
        )

    def list_artifact_locks(self) -> tuple[ArtifactLock, ...]:
        """List every held artifact lock with the step ref holding it."""
        locks: list[ArtifactLock] = []
        for name, value in self._list_refs(LOCK_REF_NAMESPACE):
            path = parse_lock_ref_name(name)
            if path is None:
                continue
            locks.append(
                ArtifactLock(
                    artifact_path=path,
                    holder=self._run_git("cat-file", "blob", value),
                    value=value,
                )
            )
        return tuple(locks)

    def claim_artifact_lock(
        self, artifact_path: str, step_ref: StepRef, expected: ArtifactLock | None
    ) -> bool:
        """Point a path's lock at a step ref by compare-and-swap, answering success.

        Function 8, invariant 8: create-only when `expected` is none (expected old
        value: none); otherwise a re-pointing from that exact previous holder. A
        lost race answers `False` — never a silent overwrite. A failure that is no
        race (the lock still holds its expected value) is the environment failing:
        `system-error`.
        """
        value = self._run_git_input(step_ref.name, "hash-object", "-w", "--stdin")
        old = "" if expected is None else expected.value
        claimed = self._invoke_git(
            (
                "update-ref",
                "-m",
                "harness: claim artifact lock",
                build_lock_ref_name(artifact_path),
                value,
                old,
            ),
            check=False,
        )
        if claimed.returncode == 0:
            return True
        current = self.find_artifact_lock(artifact_path)
        unchanged = (
            current is None
            if expected is None
            else current is not None and current.value == expected.value
        )
        if unchanged:
            raise SystemFailureError(
                "git-failed",
                f"git update-ref {build_lock_ref_name(artifact_path)} failed: "
                f"{(claimed.stderr or claimed.stdout).strip()}",
                True,
            )
        return False

    def release_artifact_locks(self, step_ref: StepRef) -> None:
        """Delete the artifact locks a promoted step instance holds, by compare-and-swap.

        Function 10, step 4: best-effort and harmless — a promoted step instance's
        locks already count as released. Its locks include the ones it inherited
        (Terminology, Step ref): those still naming an earlier step instance of
        the same step in the same workflow instance, which it continues.
        """
        for lock in self.list_artifact_locks():
            holder = lock.holder_step_ref
            if lock.holder == step_ref.name or (
                holder is not None
                and holder.workflow_instance_id == step_ref.workflow_instance_id
                and holder.step_slug == step_ref.step_slug
            ):
                self._invoke_git(
                    ("update-ref", "-d", lock.name, lock.value), check=False
                )

    # -- repository metadata and the sweep ---------------------------------------

    @contextmanager
    def hold_registration_lock(self) -> Iterator[None]:
        """Serialize session registrations on this clone with an exclusive OS file lock.

        Function 0, invariant 5: a step resolution admits exactly ONE worker session,
        so the check that no worker session is registered yet and the registration
        itself are one critical section across processes. The lock file sits in the
        repository's Git directory — repository metadata, like `.git/info/exclude`:
        never workspace state, never a ref, never a commit — and only the OS lock on
        it carries meaning, released when the section ends or its process dies.
        """
        path = Path(self._run_git("rev-parse", "--absolute-git-dir")) / _REGISTRATION_LOCK_NAME
        try:
            lock = ExclusiveFileLock(path).__enter__()
        except (OSError, TimeoutError) as failure:
            raise SystemFailureError(
                "registration-lock-failed",
                f"The session registration lock '{path}' could not be acquired: {failure}",
                True,
            ) from failure
        try:
            yield
        finally:
            lock.__exit__(None, None, None)


    def exclude_logs_path(self) -> None:
        """Exclude the logs path from Git in `.git/info/exclude`, idempotently.

        Logging: logs are local-only — no commit, the harness's or a human's,
        ever picks one up, and a log never makes an artifact path unclean.
        """
        exclude = Path(self._run_git("rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = self._workspace_dir / exclude
        try:
            existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
            if _LOGS_EXCLUDE_PATTERN in existing.splitlines():
                return
            exclude.parent.mkdir(parents=True, exist_ok=True)
            separator = "" if existing == "" or existing.endswith("\n") else "\n"
            with exclude.open("a", encoding="utf-8") as handle:
                handle.write(f"{separator}{_LOGS_EXCLUDE_PATTERN}\n")
        except OSError as failure:
            raise SystemFailureError(
                "logs-exclude-failed",
                f"The logs path cannot be excluded from Git in '{exclude}': "
                f"{failure.strerror or failure}.",
                True,
            ) from failure

    def sweep_workspace(self) -> WorkspaceSweep:
        """Run the internal workspace sweep: it reports, never journals or mutates.

        Internal validation: every committed artifact against its matched schema
        (the raw universe of the workspace branch), every step ref as a step
        instance, every held artifact lock — stale ones included — with its
        holder, and every working-tree path that differs from `HEAD` without a lock.
        """
        findings: list[Finding] = []
        for path in self.scan_raw_paths(Path(".")):
            ref = path.as_posix()
            kind = self._find_kind(ref)
            if kind is None:
                continue
            committed = self._run_git("show", f"{_HEAD}:{ref}")
            findings.extend(self._validate_document(ref, kind, committed)[0])
        step_refs = self.list_step_refs()
        promoted = frozenset(
            step_ref.name
            for step_ref in step_refs
            if self.is_step_ref_promoted(step_ref.name)
        )
        locks = self.list_artifact_locks()
        released = frozenset(
            lock.artifact_path
            for lock in locks
            if self.is_step_ref_promoted(lock.holder)
        )
        held = {lock.artifact_path for lock in locks} - released
        status = self._run_git(
            "status", "--porcelain", "--untracked-files=all", "--no-renames"
        )
        dirty = sorted(
            {
                line[3:].strip().strip('"')
                for line in status.splitlines()
                if len(line) > 3
            }
            - held
        )
        return WorkspaceSweep(
            findings=tuple(findings),
            step_refs=step_refs,
            promoted_step_refs=promoted,
            artifact_locks=locks,
            released_locks=released,
            unlocked_dirty_paths=tuple(dirty),
        )

    # -- internals ---------------------------------------------------------------

    def _read_step_ref_tip(self, step_ref: StepRef) -> str:
        """Read the step ref's current tip; its recorded tip may be stale."""
        return self._run_git("rev-parse", "--verify", f"{step_ref.name}^{{commit}}")

    def _is_changed_by_step_ref(self, ref: str, step_ref: StepRef) -> bool:
        """Tell whether the step ref changed a path since its merge base with `HEAD`."""
        tip = self._invoke_git(
            ("rev-parse", "--verify", "--quiet", f"{step_ref.name}^{{commit}}"),
            check=False,
        )
        if tip.returncode != 0:
            return False
        commit = tip.stdout.strip()
        base = self._run_git("merge-base", _HEAD, commit)
        return not self._probe_git("diff", "--quiet", base, commit, "--", ref)

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Tell whether one commit is an ancestor of (or equal to) another."""
        return self._probe_git("merge-base", "--is-ancestor", ancestor, descendant)

    def _list_tree_changes(self, old: str, new: str) -> tuple[str, ...]:
        """List the paths that differ between two trees, sorted."""
        listing = self._run_git("diff-tree", "-r", "--name-only", "--no-renames", old, new)
        return tuple(sorted(line for line in listing.splitlines() if line))

    def _list_refs(self, prefix: str) -> tuple[tuple[str, str], ...]:
        """List `(refname, objectname)` pairs under a ref prefix."""
        listing = self._run_git(
            "for-each-ref", "--format=%(refname) %(objectname)", prefix
        )
        pairs: list[tuple[str, str]] = []
        for line in listing.splitlines():
            name, _, oid = line.rpartition(" ")
            if name:
                pairs.append((name, oid))
        return tuple(pairs)

    def _merge_tree(self, head: str, commit: str, step_ref: StepRef) -> str:
        """Merge one step commit over the branch tip in memory, answering the tree."""
        merged = self._invoke_git(
            ("merge-tree", "--write-tree", "--no-messages", head, commit), check=False
        )
        if merged.returncode == 1:
            raise StateError(
                _STEP_REBASE_CONFLICT,
                f"Step ref '{step_ref.name}' conflicts with the moved workspace branch.",
                False,
            )
        if merged.returncode != 0:
            raise SystemFailureError(
                "git-failed",
                f"git merge-tree failed: {(merged.stderr or merged.stdout).strip()}",
                True,
            )
        return merged.stdout.splitlines()[0].strip()

    def _replay_commit(self, commit: str, tree: str, parent: str) -> str:
        """Re-create one commit over a new parent, keeping its message and author."""
        fields = self._invoke_git(
            ("log", "-1", "--format=%an%x00%ae%x00%aI%x00%B", commit), check=True
        ).stdout
        name, email, date, message = fields.split("\x00", 3)
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": date,
        }
        return self._run_git(
            "commit-tree", tree, "-p", parent, "-m", message.rstrip("\n"), env=env
        )

    def _temporary_index(self) -> "_TemporaryIndex":
        """Open a throwaway index file inside the repository's Git directory."""
        git_dir = Path(self._run_git("rev-parse", "--absolute-git-dir"))
        return _TemporaryIndex(git_dir / f"harness-index-{uuid.uuid4().hex}")

    def _find_kind(self, ref: str) -> str | None:
        """Resolve a path to its artifact kind, or none if the layout binds none."""
        try:
            return self._workspace_layout.resolve_resource(ref)
        except ConfigurationError as failure:
            if failure.code != _UNRESOLVABLE_RESOURCE:
                raise
            return None

    def _read_committed(
        self, refs: Iterable[tuple[str, str]], revision: str = _HEAD
    ) -> tuple[Artifact, ...]:
        """Validate and read committed `(ref, kind)` pairs, raising on any invalid one."""
        artifacts: list[Artifact] = []
        findings: list[Finding] = []
        for ref, kind in refs:
            committed = self._run_git("show", f"{revision}:{ref}")
            path_findings, data = self._validate_document(ref, kind, committed)
            if path_findings:
                findings.extend(path_findings)
                continue
            artifacts.append(Artifact(ref=ref, kind=kind, data=data))
        if findings:
            first = findings[0]
            raise StateError(
                "artifact-invalid",
                f"Committed artifact '{first.source}' is not valid: {first.message}",
                False,
            )
        return tuple(sorted(artifacts, key=lambda artifact: artifact.ref))

    def _read_schema_id(self, schema_path: Path) -> str:
        """Read one artifact schema's `$id` URI, failing fast when absent."""
        document = self._json_loader.load_json(schema_path)
        schema_id = document.get("$id") if isinstance(document, Mapping) else None
        if not isinstance(schema_id, str):
            raise ConfigurationError(
                "artifact-schema-invalid",
                f"Artifact schema '{schema_path}' declares no string $id.",
                False,
            )
        return schema_id

    def _resolve_kind(self, ref: str) -> str:
        """Match one workspace path to the artifact kind whose path pattern binds it.

        Function 8 invariant 2 / function 9 invariant 1: the artifact's schema
        identity is resolved from the write path through the workspace layout's
        path patterns. Function 9 precondition (E): a path resolving to no
        artifact schema is `state-error` (`artifact-schema-unresolved`).
        """
        try:
            return self._workspace_layout.resolve_resource(ref)
        except ConfigurationError as failure:
            raise StateError(
                "artifact-schema-unresolved",
                f"No artifact schema resolves the path '{ref}': {failure.message}",
                False,
            ) from failure

    def _validate_document(
        self, ref: str, kind: str, document: str
    ) -> tuple[tuple[Finding, ...], Any]:
        """Validate one artifact's raw bytes, returning its findings and its data."""
        try:
            data = self._read_document(ref, document)
        except (ValueError, yaml.YAMLError) as error:
            return (
                (Finding(source=ref, rule="parse", message=f"{error}"),),
                None,
            )
        records = self._validator.validate_instance(self._schema_ids[kind], data)
        findings = tuple(
            Finding(source=ref, rule="schema", message=self._render_message(record))
            for record in records
        )
        return findings, data

    def _read_document(self, ref: str, document: str) -> Any:
        """Read one artifact's bytes in the format its own file extension declares.

        A markdown artifact's schema binds its FRONTMATTER (function 9's worked
        example reports `frontmatter.status: ...`); the body is prose no contract
        describes. A `.yaml`/`.yml` artifact is a YAML document, and anything else is JSON.
        YAML is read in the JSON data model the schemas validate: timestamps stay strings.
        """
        suffix = PurePosixPath(ref).suffix
        if suffix == _MARKDOWN_SUFFIX:
            return self._markdown_loader.parse_markdown(document).frontmatter
        if suffix in _YAML_SUFFIXES:
            return self._yaml_loader.parse_json_data(document)
        return json.loads(document)

    def _render_message(self, record: ValidationErrorRecord) -> str:
        """Render one validation record as a path-prefixed failure message."""
        return f"{record.path}: {record.message}" if record.path else record.message

    def _invoke_git(
        self,
        args: Sequence[str],
        *,
        check: bool,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one Git command in the workspace, mapping failures to `system-error`."""
        try:
            return subprocess.run(
                ("git", *args),
                cwd=self._workspace_dir,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=check,
                input=stdin,
                env=None if env is None else {**os.environ, **env},
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise SystemFailureError(
                "git-failed",
                f"git {' '.join(args)} failed: {detail}",
                True,
            ) from error
        except OSError as error:
            raise SystemFailureError(
                "git-failed",
                f"git {' '.join(args)} could not run: {error}",
                True,
            ) from error

    def _run_git(self, *args: str, env: Mapping[str, str] | None = None) -> str:
        """Run one Git command that must succeed, returning its stripped stdout."""
        return self._invoke_git(args, check=True, env=env).stdout.strip()

    def _run_git_input(
        self, stdin: str, *args: str, env: Mapping[str, str] | None = None
    ) -> str:
        """Run one Git command fed on stdin that must succeed, returning its stdout."""
        return self._invoke_git(args, check=True, env=env, stdin=stdin).stdout.strip()

    def _probe_git(self, *args: str) -> bool:
        """Run one Git command whose non-zero exit is an answer, not a failure."""
        return self._invoke_git(args, check=False).returncode == 0


class _TemporaryIndex:
    """A throwaway Git index file, removed on exit — the workspace's own index is untouched."""

    def __init__(self, path: Path) -> None:
        """Name the index file; Git creates it on first write."""
        self._path = path

    def __enter__(self) -> Mapping[str, str]:
        """Answer the environment pointing Git at this index."""
        return {"GIT_INDEX_FILE": str(self._path)}

    def __exit__(self, *exc_info: object) -> None:
        """Remove the index file, whatever happened."""
        self._path.unlink(missing_ok=True)


__all__ = ["ArtifactStore"]
