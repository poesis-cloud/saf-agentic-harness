"""Parse a workspace-relative artifact path the same way on every operating system."""

from __future__ import annotations

from pathlib import Path


def parse_artifact_path(raw: str) -> Path:
    """Answer the artifact path with `\\` and `/` both read as separators.

    An inquiry's `artifactPath` is workspace-relative and rendered POSIX in every report,
    but a Windows host names paths with backslashes. Reading both separators makes the
    same inquiry resolve to the same artifact on every OS — layout matching, the logs-path
    guard and the Git refs never depend on where the harness happens to run.
    """
    return Path(raw.replace("\\", "/"))


__all__ = ["parse_artifact_path"]
