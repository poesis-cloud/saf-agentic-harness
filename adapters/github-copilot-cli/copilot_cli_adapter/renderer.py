"""Seam 4: the Copilot CLI hook stdout shapes, built from harness reports.

Every rendered object is ONE JSON line, ASCII-escaped so a Windows console code page can
never corrupt it. The adapter never renders `permissionDecision: "allow"` — an allow
would bypass the user's own permission flow; an authorized call simply passes through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

_INSTRUCTIONS_HEADER = "# Instructions"
_SKILLS_HEADER = "# Skills"
_LOAD_DIRECTIVE = "Read `{path}` before acting."
_INSTRUCTION_FILE_SUFFIX = ".instructions.md"
_SKILL_FILE_SUFFIX = ".skill.md"
_CONTEXT_SEPARATOR = "\n\n---\n\n"
_WRITE_REJECTED = "WRITE REJECTED by the SAFe agentic harness: "
_WRITE_RETRY_DIRECTIVE = (
    "The write was discarded (restored from HEAD). Rewrite the artifact to satisfy its "
    "schema and retry."
)
_STEP_RETRY_DIRECTIVE = (
    "Per reports-handling: re-resolve (resolve-step) — the cursor returns the failed "
    "step; do not surface step details to the user."
)
# postToolUse `additionalContext` is capped by the runtime at 10 KB.
ADDITIONAL_CONTEXT_LIMIT = 10_000


def render(decision: Mapping[str, Any] | None) -> str:
    """Serialize a decision as the single stdout JSON object, or nothing (pass-through)."""
    if not decision:
        return ""
    return json.dumps(decision, ensure_ascii=True, sort_keys=True)


def deny(reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": "; ".join(reasons) or "denied by the SAFe agentic harness",
    }


def modified_args(args: Mapping[str, Any]) -> dict[str, Any]:
    return {"modifiedArgs": dict(args)}


def additional_context(text: str) -> dict[str, Any]:
    return {"additionalContext": _truncate(text)}


def transformed_prompt(context: str, transformed: str) -> dict[str, Any]:
    return {"modifiedTransformedPrompt": f"{context}{_CONTEXT_SEPARATOR}{transformed}"}


def write_rejected(report: Mapping[str, Any]) -> dict[str, Any]:
    """H5 — overwrite the tool result the model sees, and direct the retry."""
    description = describe_report(report)
    return {
        "modifiedResult": {
            "resultType": "success",
            "textResultForLlm": f"{_WRITE_REJECTED}{description}",
        },
        "additionalContext": _truncate(f"{description}\n\n{_WRITE_RETRY_DIRECTIVE}"),
    }


def step_failed(report: Mapping[str, Any]) -> dict[str, Any]:
    """H6 — relay the failed postconditions with the re-resolve directive."""
    return additional_context(f"{describe_report(report)}\n\n{_STEP_RETRY_DIRECTIVE}")


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
    return "; ".join(
        f"[{(check.get('condition') or {}).get('slug')}] {check.get('outcome')}"
        + (
            f" — {check['failureMessage']}"
            if check.get("failureMessage") is not None
            else ""
        )
        for check in report.get("conditionChecks") or ()
    )


def _describe_artifact_checks(report: Mapping[str, Any]) -> str:
    return "; ".join(
        f"{check.get('artifactPath')}: {check.get('failureMessage')} "
        f"({(check.get('revert') or {}).get('action')})"
        for check in report.get("artifactChecks") or ()
    )


def _read_refs(reports: Sequence[Mapping[str, Any]], key: str) -> tuple[str, ...]:
    for report in reports:
        refs = report.get(key)
        if isinstance(refs, Sequence) and not isinstance(refs, str):
            return tuple(str(ref) for ref in refs)
    return ()


def _truncate(text: str) -> str:
    if len(text.encode("utf-8")) <= ADDITIONAL_CONTEXT_LIMIT:
        return text
    marker = "\n\n[truncated by the SAFe agentic harness adapter]"
    budget = ADDITIONAL_CONTEXT_LIMIT - len(marker.encode("utf-8"))
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + marker


__all__ = [
    "ADDITIONAL_CONTEXT_LIMIT",
    "ContextRenderer",
    "additional_context",
    "deny",
    "describe_report",
    "modified_args",
    "read_error",
    "read_status",
    "render",
    "step_failed",
    "transformed_prompt",
    "write_rejected",
]
