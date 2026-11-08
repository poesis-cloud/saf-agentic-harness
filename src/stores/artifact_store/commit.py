"""One commit function 9 made: the record its `valid` report carries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Commit:
    """Name the commit that promoted a validated set and the paths it changed.

    Spec (function 9, invariant 3): rendered as the `valid` report's `commitId`
    and `committedPaths`, so the session log records what each session committed.
    """

    commit_id: str
    paths: tuple[str, ...]


__all__ = ["Commit"]
