"""The internal workspace sweep's report: findings plus the Git plane's residue."""

from __future__ import annotations

from dataclasses import dataclass

from stores.artifact_store.artifact_lock import ArtifactLock
from stores.artifact_store.finding import Finding
from stores.artifact_store.step_ref import StepRef


@dataclass(frozen=True)
class WorkspaceSweep:
    """Carry what the sweep found; it journals nothing and mutates nothing.

    Spec (Internal validation): every committed artifact against its schema;
    step refs reported as step instances, never validated as workspace state;
    every held artifact lock — stale ones included — with its holder; and the
    working-tree paths that differ from `HEAD` without a lock. `promoted_step_refs`
    and `released_locks` name the refs Git alone shows as promoted or released;
    whether an unpromoted step instance is in flight, failed, or abandoned is the
    logs' answer.
    """

    findings: tuple[Finding, ...]
    step_refs: tuple[StepRef, ...]
    promoted_step_refs: frozenset[str]
    artifact_locks: tuple[ArtifactLock, ...]
    released_locks: frozenset[str]
    unlocked_dirty_paths: tuple[str, ...]


__all__ = ["WorkspaceSweep"]
