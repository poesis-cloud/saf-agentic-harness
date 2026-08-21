"""The disposable framework instance every hook firing in this suite runs against.

Hermeticity is the whole point of this module: a run must not read or write the
developer's real framework, and two runs must not see each other's state. So the framework
is a tmp tree holding a VERBATIM COPY of the repository's `adapters/` directory — the
adapter keeps its own private `SessionTracker` record next to `adapter.py`, so a copy is
what keeps that record out of the source tree — plus the scripted stub standing in for
`harness.py` (adapter spec I15: the command API is the whole contract between the adapter
and the harness core, so it is the only seam that needs standing in for).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from harness_stub import HarnessStub

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_ADAPTERS_DIR = REPO_ROOT / "adapters"
STUB_ENTRYPOINT = Path(__file__).resolve().parent / "stub_harness_entrypoint.py"

INSTRUCTION_REF = "reports-handling"
INSTRUCTION_BODY = "Relay every report to the orchestrator, never to the user."
SKILL_ID = "code-review"


@dataclass(frozen=True)
class Framework:
    """One disposable framework instance, with the environment its hooks run under."""

    root: Path
    dispatch_path: Path
    workspace_dir: Path
    instructions_dir: Path
    environment: Mapping[str, str]

    def workspace_path(self, relative_path: str) -> str:
        """Answer a workspace artifact's ABSOLUTE host path, as a tool call would name it."""
        return str(self.workspace_dir / relative_path)


def build_framework(root: Path, script_path: Path, journal_path: Path) -> Framework:
    """Lay out one framework instance and answer it, ready to be fired at."""
    shutil.copytree(
        REPO_ADAPTERS_DIR,
        root / "adapters",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(STUB_ENTRYPOINT, root / "harness.py")
    instructions_dir = root / "instructions"
    instructions_dir.mkdir()
    (instructions_dir / f"{INSTRUCTION_REF}.instructions.md").write_text(
        f"{INSTRUCTION_BODY}\n", encoding="utf-8"
    )
    skills_dir = root / "skills"
    skills_dir.mkdir()
    (skills_dir / f"{SKILL_ID}.skill.md").write_text("# Code review\n", encoding="utf-8")
    workspace_dir = root / "workspace"
    (workspace_dir / "portfolio").mkdir(parents=True)
    HarnessStub.create(script_path=script_path, journal_path=journal_path)
    return Framework(
        root=root,
        dispatch_path=root / "adapters" / "dispatch.sh",
        workspace_dir=workspace_dir,
        instructions_dir=instructions_dir,
        environment={
            # Built from scratch — an exported FRAMEWORK_* of the developer's own must not
            # reach the hook process.
            "PATH": os.environ.get("PATH", ""),
            "FRAMEWORK_DIR": str(root),
            "FRAMEWORK_WORKSPACE_DIR": "workspace",
            "FRAMEWORK_INSTRUCTIONS_DIR": "instructions",
            "FRAMEWORK_SKILLS_DIR": "skills",
            "HARNESS_STUB_SCRIPT": str(script_path),
            "HARNESS_STUB_JOURNAL": str(journal_path),
        },
    )


__all__ = [
    "INSTRUCTION_BODY",
    "INSTRUCTION_REF",
    "REPO_ADAPTERS_DIR",
    "REPO_ROOT",
    "SKILL_ID",
    "Framework",
    "build_framework",
]
