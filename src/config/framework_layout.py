"""The typed view over the framework's layout environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrameworkLayout:
    """Expose the resolved framework directories.

    Spec (Configuration plane): the framework's layout is environment, not file
    configuration; `FRAMEWORK_DIR` is the one ABSOLUTE path and every other layout
    variable resolves relative to it. `schemas_dir` is the artifact schema directory
    declared by `FRAMEWORK_ARTIFACTS_DIR`: artifacts and their schemas share one
    declaration, so both fields carry the same resolved path.
    """

    framework_dir: Path
    agents_dir: Path
    artifacts_dir: Path
    schemas_dir: Path
    skills_dir: Path
    templates_dir: Path
    workflows_dir: Path
    instructions_dir: Path
    workspace_dir: Path


__all__ = ["FrameworkLayout"]
