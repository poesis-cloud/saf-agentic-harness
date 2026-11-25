#!/usr/bin/env python3
"""Render the harness PLUGIN for Claude Code into a folder.

    python adapters/claude-code/render_plugin.py --dest <folder> [--lock <requirements.lock>]

Adapter spec (claude-code), Plugin mode — The harness plugin. The output holds no machine
path:

- `.claude-plugin/plugin.json`, the Claude Code plugin manifest;
- `hooks/hooks.json`, the global hooks as shell strings reaching the plugin through
  `${CLAUDE_PLUGIN_ROOT}`, rendered from `hooks.yaml`;
- `VERSION`, `requirements.lock`, `launcher.py`, `bin/saf-hook.sh`, and the runtime subset:
  `harness.py`, `src/`, `contracts/`, `adapters/claude-code/`, `adapters/common/` (the shared
  adapter base), `adapters/binding_contract.py`, `LICENSE`, `NOTICE`, `README.md`.

Without `--lock`, the lock is generated with
`uv pip compile --universal --generate-hashes requirements.txt` (uv must be on PATH).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ADAPTER_DIR = Path(__file__).resolve().parent
REPO_ROOT = ADAPTER_DIR.parents[1]
PLUGIN_SOURCES = ADAPTER_DIR / "plugin"

MANIFEST: Mapping[str, Any] = {
    "name": "poesis-saf-agentic-harness",
    "description": (
        "Deterministic governance - regulation - supervision - coordination of agentic "
        "organization: workflow orchestration; step, model, skill, instruction resolution; "
        "artifact transaction and versioning; logging; agent authorization."
    ),
    "author": {"name": "Poesis SAS", "email": "clement.cazaud@poesis.cloud"},
    "homepage": "https://docs.poesis.cloud/saf/",
    "repository": "https://github.com/poesis-cloud/saf-agentic-harness",
    "license": "Apache-2.0",
    "keywords": [
        "harness",
        "organization",
        "governance",
        "regulation",
        "supervision",
        "coordination",
        "orchestration",
        "workflow",
        "agent",
        "skill",
        "instruction",
        "artifact",
        "template",
    ],
}
MANIFEST_FILE = Path(".claude-plugin") / "plugin.json"
HOOKS_FILE = Path("hooks") / "hooks.json"
SESSION_START = "SessionStart"
# Tool events take a matcher; `*` matches every tool (the adapter classifies them itself).
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})
RUNTIME_FILES = ("harness.py", "LICENSE", "NOTICE")
RUNTIME_TREES = ("src", "contracts")
ADAPTER_FILES = ("adapter.py", "hooks.yaml", "models.yaml", "tools.yaml", "README.md")
ADAPTER_TREES = ("claude_code_adapter", "contracts")
_IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


class RenderError(RuntimeError):
    """The plugin cannot be rendered."""


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def render_manifest(version: str) -> dict[str, Any]:
    return {**MANIFEST, "version": version}


def shim_entry(event: str, timeout: int) -> dict[str, Any]:
    """One hook entry: the shim, reached through `${CLAUDE_PLUGIN_ROOT}` (quoted: a plugin
    cache path may hold spaces)."""
    return {
        "type": "command",
        "command": f'sh "${{CLAUDE_PLUGIN_ROOT}}/bin/saf-hook.sh" {event}',
        "timeout": timeout,
    }


def render_hooks() -> dict[str, Any]:
    """Every event of `hooks.yaml` through the shim, with its registered timeout."""
    source = yaml.safe_load((ADAPTER_DIR / "hooks.yaml").read_text(encoding="utf-8"))
    hooks: dict[str, list[dict[str, Any]]] = {}
    for event, settings in (source or {}).get("hooks", {}).items():
        group: dict[str, Any] = {"hooks": [shim_entry(event, int(settings["timeoutSec"]))]}
        if event in TOOL_EVENTS:
            group = {"matcher": "*", **group}
        hooks[event] = [group]
    if SESSION_START not in hooks:
        raise RenderError("hooks.yaml registers no SessionStart")
    return {"hooks": hooks}


def generate_lock(destination: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RenderError("uv is not on PATH: pass --lock, or install uv to generate the lock")
    completed = subprocess.run(
        [uv, "pip", "compile", "--quiet", "--no-header", "--universal", "--generate-hashes",
         "--python-version", "3.10", str(REPO_ROOT / "requirements.txt"),
         "-o", str(destination)],
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RenderError(f"uv pip compile failed: {completed.stderr.strip()}")


def render(destination: Path, lock: Path | None = None) -> Path:
    """Render the plugin into an empty or absent `destination`; answer the manifest path."""
    if destination.exists() and any(destination.iterdir()):
        raise RenderError(f"{destination} is not empty")
    version = read_version()
    with tempfile.TemporaryDirectory(prefix="saf-claude-plugin-") as staging_name:
        staging = Path(staging_name) / "plugin"
        staging.mkdir()
        _write_json(staging / MANIFEST_FILE, render_manifest(version))
        _write_json(staging / HOOKS_FILE, render_hooks())
        (staging / "VERSION").write_text(version + "\n", encoding="utf-8")
        if lock is not None:
            shutil.copy2(lock, staging / "requirements.lock")
        else:
            generate_lock(staging / "requirements.lock")
        shutil.copy2(PLUGIN_SOURCES / "launcher.py", staging / "launcher.py")
        shutil.copy2(PLUGIN_SOURCES / "README.md", staging / "README.md")
        shutil.copytree(PLUGIN_SOURCES / "bin", staging / "bin", ignore=_IGNORED)
        for name in RUNTIME_FILES:
            shutil.copy2(REPO_ROOT / name, staging / name)
        for name in RUNTIME_TREES:
            shutil.copytree(REPO_ROOT / name, staging / name, ignore=_IGNORED)
        adapters = staging / "adapters"
        adapter = adapters / ADAPTER_DIR.name
        adapter.mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "adapters" / "binding_contract.py", adapters)
        shutil.copytree(REPO_ROOT / "adapters" / "common", adapters / "common", ignore=_IGNORED)
        for name in ADAPTER_FILES:
            shutil.copy2(ADAPTER_DIR / name, adapter / name)
        for name in ADAPTER_TREES:
            shutil.copytree(ADAPTER_DIR / name, adapter / name, ignore=_IGNORED)
        destination.mkdir(parents=True, exist_ok=True)
        for entry in staging.iterdir():
            shutil.move(str(entry), destination / entry.name)
    return destination / MANIFEST_FILE


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="render_plugin.py", description=__doc__.splitlines()[0])
    parser.add_argument("--dest", required=True, type=Path, help="the folder to render into")
    parser.add_argument("--lock", type=Path, help="a hash-locked requirements.lock to ship")
    arguments = parser.parse_args(argv)
    try:
        manifest = render(arguments.dest, arguments.lock)
    except (RenderError, OSError) as failure:
        print(f"render_plugin.py: {failure}", file=sys.stderr)
        return 1
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
