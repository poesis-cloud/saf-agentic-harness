"""A harness tree, a framework, and a Git workspace — all real, all disposable."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from functional_fixtures import (
    DEFAULT_ACCESS_CONTROL_LIST,
    VERIFICATION_WORKFLOW,
    build_capabilities,
    build_framework,
)
from model_catalogue import bound_slugs

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = "verification"
STEP = "verify"
ACTOR = "reviewer"
ORCHESTRATOR = "orchestrator"
REPORT_REF = "report/verify.json"
REPORT_BYTES = '{\n  "slug": "verify",\n  "state": "written-by-the-worker"\n}\n'


@dataclass(frozen=True)
class RealHarness:
    """The harness tree the adapter runs, and the framework and workspace it governs."""

    harness_root: Path
    framework_dir: Path
    workspace_dir: Path
    environment: Mapping[str, str]

    def adapter_dir(self, adapter: str) -> Path:
        return self.harness_root / "adapters" / adapter

    def git(self, *args: str) -> str:
        return subprocess.run(
            ("git", *args),
            cwd=self.workspace_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def step_refs(self) -> list[str]:
        return self.git("for-each-ref", "--format=%(refname)", "refs/harness/steps/").split()

    def lock_refs(self) -> list[str]:
        return self.git("for-each-ref", "--format=%(refname)", "refs/harness/locks/").split()

    def write_report(self) -> Path:
        """Land the worker's write in the working tree, as the host tool does."""
        path = self.workspace_dir / REPORT_REF
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(REPORT_BYTES.encode("utf-8"))
        return path


def build_real_harness(tmp_path: Path, adapter: str) -> RealHarness:
    """Copy the harness tree, write a framework whose catalogue the adapter binds."""
    harness_root = tmp_path / "harness"
    for name in ("adapters", "src", "contracts"):
        shutil.copytree(
            REPO_ROOT / name,
            harness_root / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy2(REPO_ROOT / "harness.py", harness_root / "harness.py")
    shutil.copy2(REPO_ROOT / "VERSION", harness_root / "VERSION")
    models = harness_root / "adapters" / adapter / "models.yaml"
    profiles = [
        {"slug": slug, "costRank": min(rank, 10), "capabilities": build_capabilities(coding=5)}
        for rank, slug in enumerate(bound_slugs(models), 1)
    ]
    rig = tmp_path / "rig"
    rig.mkdir()
    built = build_framework(
        rig,
        workflows={WORKFLOW: VERIFICATION_WORKFLOW},
        model_profiles=profiles,
        access_control_list=DEFAULT_ACCESS_CONTROL_LIST,
    )
    (built.framework_dir / "conf" / "model-profiles.conf.yaml").write_text(
        yaml.safe_dump({"modelProfiles": profiles}, sort_keys=False), encoding="utf-8"
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "SAF_HARNESS_STATE_DIR": str(tmp_path / "state"),
        "FRAMEWORK_DIR": str(built.framework_dir),
        # Absolute: an adapter relativizes host paths against the declared workspace
        # without resolving `..` segments, which the rig's `.env` would carry.
        "FRAMEWORK_WORKSPACE_DIR": str(built.workspace_dir.resolve()),
        # The real harness imports its requirements: run it with this interpreter.
        "SAF_HARNESS_PYTHON": sys.executable,
    }
    return RealHarness(
        harness_root=harness_root,
        framework_dir=built.framework_dir,
        workspace_dir=built.workspace_dir,
        environment=environment,
    )


__all__ = [
    "ACTOR",
    "ORCHESTRATOR",
    "REPORT_BYTES",
    "REPORT_REF",
    "STEP",
    "WORKFLOW",
    "RealHarness",
    "build_real_harness",
]
