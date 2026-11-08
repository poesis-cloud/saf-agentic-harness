"""The framework's declaration of the harness versions it supports."""

from __future__ import annotations

from dataclasses import dataclass

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class HarnessRequirement:
    """`conf/harness.conf.yaml`: a PEP 440 version specifier set (`require`).

    Spec (core, Harness compatibility): the fail-fast load compares the installed harness
    version, read as a PEP 440 version (`1.0.0-alpha.1` reads as `1.0.0a1`), against it.
    """

    require: str

    def __post_init__(self) -> None:
        """Refuse a `require` that is not a PEP 440 specifier set."""
        try:
            SpecifierSet(self.require)
        except InvalidSpecifier as failure:
            raise ValueError(f"'{self.require}' is not a PEP 440 version specifier set") from failure

    def admits(self, version: str) -> bool:
        """Tell whether the installed harness `version` satisfies `require`."""
        try:
            parsed = Version(version)
        except InvalidVersion:
            return False
        return SpecifierSet(self.require).contains(parsed, prereleases=True)


__all__ = ["HarnessRequirement"]
