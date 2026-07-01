"""A collection of findings — the accumulating result of any check, with rendering."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from .finding import Finding


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def error(self, path: Path | str, message: str) -> None:
        self.findings.append(Finding("error", str(path), message))

    def warn(self, path: Path | str, message: str) -> None:
        self.findings.append(Finding("warning", str(path), message))

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, other: "Report") -> None:
        self.findings.extend(other.findings)

    def has_errors(self) -> bool:
        return any(finding.severity == "error" for finding in self.findings)

    def _counts(self) -> tuple[int, int]:
        errors = sum(1 for finding in self.findings if finding.severity == "error")
        warnings = sum(1 for finding in self.findings if finding.severity == "warning")
        return errors, warnings

    def print(self, strict: bool) -> int:
        for finding in self.findings:
            print(f"{finding.severity}: {finding.path}: {finding.message}", file=sys.stderr)
        error_count, warning_count = self._counts()
        if strict and warning_count:
            error_count += warning_count
        if error_count:
            return 1
        print(f"pass: errors=0 warnings={warning_count}")
        return 0

    def print_json(self, strict: bool) -> int:
        import json as _json

        payload = [
            {
                key: value
                for key, value in {
                    "severity": finding.severity,
                    "path": finding.path,
                    "message": finding.message,
                    "condition_id": finding.condition_id,
                    "expected": finding.expected,
                    "actual": finding.actual,
                    "suggestion": finding.suggestion,
                }.items()
                if value is not None
            }
            for finding in self.findings
        ]
        print(_json.dumps(payload, indent=2))
        error_count, warning_count = self._counts()
        if strict and warning_count:
            error_count += warning_count
        return 1 if error_count else 0
