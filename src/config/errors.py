"""ConfigError — a framework configuration file failed to parse or validate."""

from __future__ import annotations

from models import Report


class ConfigError(Exception):
    """Raised by the config plane when a conf/*.conf.yaml file is missing, unparsable, or
    violates its contract schema. Carries the full findings Report so the CLI can render every
    violation at once instead of the first."""

    def __init__(self, report: Report) -> None:
        self.report = report
        first = report.findings[0].message if report.findings else "invalid framework configuration"
        count = len(report.findings)
        suffix = f" (+{count - 1} more finding{'s' if count > 2 else ''})" if count > 1 else ""
        super().__init__(f"{first}{suffix}")


__all__ = ["ConfigError"]
