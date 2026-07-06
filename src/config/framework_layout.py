"""FrameworkLayout — the typed view over conf/framework.conf.yaml (the framework's own layout).

The FRAMEWORK is the application embedding the harness (this repo's SAFe methodology); the
WORKSPACE is the data plane the harness checks (this framework's portfolio). This view declares
where the framework keeps the files the harness loads agnostically — skills, agents,
instructions, prompts, templates, and the artifact schema registry. The harness's OWN contracts
(harness/contracts/) are harness-owned and resolved structurally from the harness package, never
configured here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FrameworkLayout:
    """Typed accessors over the validated framework layout document."""

    #: Conventional defaults, used when a key is not declared (and by consumers constructed
    #: without a loaded layout, e.g. standalone SchemaCatalog uses in tests).
    DEFAULTS = {
        "skills": ".",
        "agents": "agents",
        "instructions": "instructions",
        "prompts": "prompts",
        "templates": "templates",
        "schemas": "schemas",
    }

    def __init__(self, data: dict[str, Any]) -> None:
        self._paths: dict[str, str] = {**self.DEFAULTS, **dict(data.get("paths") or {})}

    def dir_of(self, key: str, framework_root: Path) -> Path:
        """The absolute directory for a declared framework path key ('.' = the root itself)."""
        relative = self._paths.get(key, ".")
        return framework_root if relative == "." else (framework_root / relative).resolve()

    def skills_root(self, framework_root: Path) -> Path:
        return self.dir_of("skills", framework_root)

    def schemas_registry(self, framework_root: Path) -> Path:
        """The methodology-specific artifact schema registry."""
        return self.dir_of("schemas", framework_root)

    def agents_dir(self, framework_root: Path) -> Path:
        return self.dir_of("agents", framework_root)

    def instructions_dir(self, framework_root: Path) -> Path:
        return self.dir_of("instructions", framework_root)

    def prompts_dir(self, framework_root: Path) -> Path:
        return self.dir_of("prompts", framework_root)

    def templates_dir(self, framework_root: Path) -> Path:
        return self.dir_of("templates", framework_root)


__all__ = ["FrameworkLayout"]
