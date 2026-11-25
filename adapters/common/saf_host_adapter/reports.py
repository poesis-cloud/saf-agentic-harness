"""Host-agnostic reading and wording of harness reports, and the session-context renderer.

Every host renders its own decision SHAPES (its stdout contract) from these texts: a write
rejected or left unvalidated, a failed step, the context injected at a session's opening.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

_INSTRUCTIONS_HEADER = "# Instructions"
_SKILLS_HEADER = "# Skills"
_LOAD_DIRECTIVE = "Read `{path}` before acting."
_INSTRUCTION_FILE_SUFFIX = ".instructions.md"
_SKILL_FILE_SUFFIX = ".skill.md"
WRITE_REJECTED = "WRITE REJECTED by the SAFe agentic harness: "
_WRITE_RETRY_TAIL = "Rewrite the artifact to satisfy its schema and retry."
WRITE_UNVALIDATED = "WRITE OUTCOME UNKNOWN — the SAFe agentic harness answered an error: "
# An error can follow the commit (`log-append-failed`: the harness commits, then journals),
# so neither the commit nor a restore can be claimed or denied.
WRITE_ERROR_DIRECTIVE = (
    "The harness answered an error for this write, so its state is UNKNOWN: it may already "
    "be committed, may have been restored, or may still be uncommitted in the working tree. "
    "Do not assume it was saved or discarded. Inspect the artifact (view it) before retrying, "
    "fix what the error names, then redo the write only if it does not hold what you intended."
)
STEP_RETRY_DIRECTIVE = (
    "Per reports-handling: re-resolve (resolve-step) — the cursor returns the failed "
    "step; do not surface step details to the user."
)


def write_rejected_text(report: Mapping[str, Any]) -> tuple[str, str]:
    """H5 `reverted`: the tool-result text the model sees, and the retry directive."""
    description = describe_report(report)
    return (
        f"{WRITE_REJECTED}{description}",
        f"{description}\n\n{write_retry_directive(report)}",
    )


def write_unvalidated_text(report: Mapping[str, Any]) -> tuple[str, str]:
    """H5 error outcome: the write was not validated; never claim it was restored."""
    description = describe_report(report)
    return f"{WRITE_UNVALIDATED}{description}", f"{description}\n\n{WRITE_ERROR_DIRECTIVE}"


def step_failed_text(report: Mapping[str, Any]) -> str:
    """H6: the failed postconditions with the re-resolve directive."""
    return f"{describe_report(report)}\n\n{STEP_RETRY_DIRECTIVE}"


class ContextRenderer:
    """Render resolved instruction/skill refs into the context injected into a session."""

    def __init__(self, instructions_dir: Path, skills_dir: Path) -> None:
        self._instructions_dir = instructions_dir
        self._skills_dir = skills_dir

    def render(self, reports: Sequence[Mapping[str, Any]], *, inline: bool = True) -> str:
        """Render instructions (inlined, or as load directives) and skill load directives.

        `inline=False` is used where the runtime caps the injected text (postToolUse's
        `additionalContext`): every ref becomes a short load directive instead.
        """
        sections: list[str] = []
        instructions = _read_refs(reports, "instructions")
        skills = _read_refs(reports, "skills")
        if instructions:
            sections.append(_INSTRUCTIONS_HEADER)
            sections.extend(self._render_instruction(ref, inline) for ref in instructions)
        if skills:
            sections.append(_SKILLS_HEADER)
            sections.extend(self._render_skill(skill) for skill in skills)
        return "\n\n".join(sections)

    def _render_instruction(self, ref: str, inline: bool) -> str:
        path = self._find_instruction(ref)
        if not inline:
            return f"## {ref}\n\n{_LOAD_DIRECTIVE.format(path=path)}"
        return f"## {ref}\n\n{path.read_text(encoding='utf-8').strip()}"

    def _find_instruction(self, ref: str) -> Path:
        filename = f"{ref}{_INSTRUCTION_FILE_SUFFIX}"
        matches = sorted(
            path
            for path in self._instructions_dir.rglob(filename)
            if path.is_file() and path.name == filename
        )
        if not matches:
            raise FileNotFoundError(
                f"instruction '{ref}' resolves to no file '{filename}'"
            )
        if len(matches) > 1:
            raise ValueError(f"instruction '{ref}' resolves to more than one file")
        return matches[0]

    def _render_skill(self, skill: str) -> str:
        path = self._skills_dir / f"{skill}{_SKILL_FILE_SUFFIX}"
        # One path form for the agent on every OS; Windows tools accept `C:/...` too.
        return f"## {skill}\n\n{_LOAD_DIRECTIVE.format(path=path.as_posix())}"


def read_status(report: Mapping[str, Any]) -> str:
    return str((report.get("outcome") or {}).get("status", ""))


def read_error(report: Mapping[str, Any]) -> Mapping[str, Any] | None:
    error = (report.get("outcome") or {}).get("error")
    return error if isinstance(error, Mapping) else None


def describe_report(report: Mapping[str, Any]) -> str:
    """Serialize one report into the reason the host relays to the model."""
    function = str((report.get("context") or {}).get("function", "harness function"))
    status = read_status(report)
    error = read_error(report)
    if error is not None:
        return f"{function} {status}: {error.get('code')} — {error.get('message')}"
    if "conditionChecks" in report:
        return f"{function} {status}: {_describe_condition_checks(report)}"
    if "authorization" in report:
        authorization = report.get("authorization") or {}
        return (
            f"{function} {status}: {authorization.get('failureMessage')} "
            f"({authorization.get('artifactPath')})"
        )
    if "artifactChecks" in report:
        return f"{function} {status} {_describe_artifact_checks(report)}"
    return f"{function} {status}"


def _describe_condition_checks(report: Mapping[str, Any]) -> str:
    parts = [
        f"[{(check.get('condition') or {}).get('slug')}] {check.get('outcome')}"
        + (
            f" — {check['failureMessage']}"
            if check.get("failureMessage") is not None
            else ""
        )
        for check in report.get("conditionChecks") or ()
    ]
    delivery = report.get("artifactDelivery") or {}
    if delivery.get("outcome") == "fail":
        parts.append(f"[artifact delivery] fail — {delivery.get('failureMessage')}")
    return "; ".join(parts)


def _describe_revert(revert: Mapping[str, Any] | None) -> str:
    """Render one revert record as what happened: `restored from <from>`, `deleted`, ….

    Spec (core function 9, invariant 2): a failing path is restored from the step ref
    tip or from `HEAD` — the record's `from` names which — or deleted. A record that
    names no source renders without one.
    """
    record = revert or {}
    action = record.get("action")
    source = record.get("from")
    if action == "restored" and source:
        return f"restored from {source}"
    return str(action) if action else "discarded"


def write_retry_directive(report: Mapping[str, Any]) -> str:
    """Direct the retry, naming each failing path's revert as the report records it."""
    reverts = "; ".join(
        f"{check.get('artifactPath')} {_describe_revert(check.get('revert'))}"
        for check in report.get("artifactChecks") or ()
    )
    discarded = f"The write was discarded ({reverts})." if reverts else "The write was discarded."
    return f"{discarded} {_WRITE_RETRY_TAIL}"


def _describe_artifact_checks(report: Mapping[str, Any]) -> str:
    return "; ".join(
        f"{check.get('artifactPath')}: {check.get('failureMessage')} "
        f"({_describe_revert(check.get('revert'))})"
        for check in report.get("artifactChecks") or ()
    )


def _read_refs(reports: Sequence[Mapping[str, Any]], key: str) -> tuple[str, ...]:
    for report in reports:
        refs = report.get(key)
        if isinstance(refs, Sequence) and not isinstance(refs, str):
            return tuple(str(ref) for ref in refs)
    return ()


__all__ = [
    "STEP_RETRY_DIRECTIVE",
    "WRITE_ERROR_DIRECTIVE",
    "WRITE_REJECTED",
    "WRITE_UNVALIDATED",
    "ContextRenderer",
    "describe_report",
    "read_error",
    "read_status",
    "step_failed_text",
    "write_rejected_text",
    "write_retry_directive",
    "write_unvalidated_text",
]
