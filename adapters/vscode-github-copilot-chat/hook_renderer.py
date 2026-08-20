"""Report-to-host-decision rendering: the seam-4 stdout shapes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from event_class import EventClass
from hook_event import HookDecision

_ALLOWING_STATUSES = frozenset({"pass", "allowed"})
_VALID_STATUS = "valid"
_PASS_STATUS = "pass"
_SESSION_ID_FLAG = "--session-id"
_INSTRUCTIONS_HEADER = "# Instructions"
_SKILLS_HEADER = "# Skills"
_LOAD_DIRECTIVE = "Read `{path}` before acting."
_INSTRUCTION_FILE_SUFFIX = ".instructions.md"
_SKILL_FILE_SUFFIX = ".skill.md"
_WRITE_RETRY_DIRECTIVE = (
    "The write was discarded (restored from HEAD). Rewrite the artifact to satisfy its "
    "schema and retry."
)
_STEP_RETRY_DIRECTIVE = (
    "Per reports-handling: re-resolve (resolve-step) — the cursor returns the failed "
    "step; do not surface step details to the user."
)
_INJECTION_EVENT_NAMES: Mapping[EventClass, str] = {
    EventClass.SESSION_STARTED: "UserPromptSubmit",
    EventClass.STEP_STARTED: "SubagentStart",
}


class HookRenderer:
    """Build the host decision the harness reports imply.

    Spec (adapter, I14): output construction — report `outcome` to `permissionDecision`,
    `conditionChecks[]` serialization into reasons, instruction/skill refs rendered to
    inlined content plus load directives, `updatedInput` stamping — is this adapter's
    own behavior, governed by its own seam-4 stdout contract.
    """

    def __init__(self, instructions_dir: Path, skills_dir: Path) -> None:
        """Create the renderer over the framework's instruction and skill directories."""
        self._instructions_dir = instructions_dir
        self._skills_dir = skills_dir

    def render_pass_through(self) -> HookDecision:
        """Render the pass-through: exit 0, empty stdout, nothing journaled (C7)."""
        return HookDecision(exit_code=0, stdout="")

    def render_system_message(self, message: str) -> HookDecision:
        """Render an inject-only boundary's failure — exit 0, never a veto (H0/H1)."""
        return _decide({"systemMessage": message})

    def render_context_injection(
        self, event_class: EventClass, reports: Sequence[Mapping[str, Any]]
    ) -> HookDecision:
        """Render the injected context of a session- or step-started boundary.

        Spec (adapter, H0/H1 Output construction): instruction refs are INLINED in report
        order under a header naming each ref; skill ids become LOAD DIRECTIVES — never an
        inline dump, which would defeat the skills' own lazy loading.
        """
        failures = tuple(report for report in reports if _read_error(report))
        if failures:
            return self.render_system_message(
                "; ".join(_describe_report(report) for report in failures)
            )
        return _decide(
            {
                "hookSpecificOutput": {
                    "hookEventName": _INJECTION_EVENT_NAMES[event_class],
                    "additionalContext": self._render_context(reports),
                }
            }
        )

    def render_permission_decision(
        self, reports: Sequence[Mapping[str, Any]]
    ) -> HookDecision:
        """Collapse gating reports into ONE host permission decision.

        Spec (adapter, H2 invariant 1 / H3 invariant 2): a single `permissionDecision`
        guards the whole tool call, and any non-allowing outcome — harness errors included
        — denies. Erring open would unmake the enforcement.
        """
        denials = tuple(
            report for report in reports if not _is_allowing(_read_status(report))
        )
        if not denials:
            return self.render_allowance()
        return self.render_denial(
            "; ".join(_describe_report(report) for report in denials)
        )

    def render_allowance(self) -> HookDecision:
        """Render an allowed pre-tool boundary."""
        return _decide(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        )

    def render_denial(self, reason: str) -> HookDecision:
        """Render a denied pre-tool boundary, relaying the reason to the model."""
        return _decide(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )

    def render_stamped_input(
        self, tool_input: Mapping[str, Any], command_key: str, session_id: str
    ) -> HookDecision:
        """Render H4's attribution stamp as a FULL rewritten `updatedInput`.

        Spec (adapter, H4 rule 4): the host validates `updatedInput` against the tool's
        input schema and the object must carry the whole `tool_input`, never a patch.
        """
        updated_input = dict(tool_input)
        updated_input[command_key] = (
            f"{tool_input[command_key]} {_SESSION_ID_FLAG} {session_id}"
        )
        return _decide(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated_input,
                }
            }
        )

    def render_write_outcome(self, report: Mapping[str, Any]) -> HookDecision:
        """Render the commit gate's outcome (H5).

        Spec (adapter, H5 Output construction): `valid` renders plain success — the
        commit already happened harness-side; anything else blocks and feeds the failure
        back to the writing agent.
        """
        if _read_status(report) == _VALID_STATUS:
            return _decide({"continue": True})
        return self.render_block(_describe_report(report), _WRITE_RETRY_DIRECTIVE)

    def render_step_outcome(self, report: Mapping[str, Any]) -> HookDecision:
        """Render the step-ended evaluation's outcome (H6).

        Spec (adapter, H6 invariant 2): a failure never becomes a user-facing verdict —
        the block reason addresses the orchestrator, which re-resolves.
        """
        if _read_status(report) == _PASS_STATUS:
            return _decide({"continue": True})
        return self.render_block(_describe_report(report), _STEP_RETRY_DIRECTIVE)

    def render_block(self, reason: str, additional_context: str) -> HookDecision:
        """Render a post-tool block carrying its reason and retry directive."""
        return _decide(
            {
                "decision": "block",
                "reason": reason,
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": additional_context,
                },
            }
        )

    def _render_context(self, reports: Sequence[Mapping[str, Any]]) -> str:
        sections: list[str] = []
        instructions = _read_refs(reports, "instructions")
        skills = _read_refs(reports, "skills")
        if instructions:
            sections.append(_INSTRUCTIONS_HEADER)
            sections.extend(self._render_instruction(ref) for ref in instructions)
        if skills:
            sections.append(_SKILLS_HEADER)
            sections.extend(self._render_skill(skill) for skill in skills)
        return "\n\n".join(sections)

    def _render_instruction(self, ref: str) -> str:
        path = self._instructions_dir / f"{ref}{_INSTRUCTION_FILE_SUFFIX}"
        return f"## {ref}\n\n{path.read_text(encoding='utf-8').strip()}"

    def _render_skill(self, skill: str) -> str:
        path = self._skills_dir / f"{skill}{_SKILL_FILE_SUFFIX}"
        return f"## {skill}\n\n{_LOAD_DIRECTIVE.format(path=path)}"


def _decide(rendered: Mapping[str, Any]) -> HookDecision:
    return HookDecision(
        exit_code=0, stdout=json.dumps(rendered, ensure_ascii=False, sort_keys=True)
    )


def _read_status(report: Mapping[str, Any]) -> str:
    outcome = report.get("outcome") or {}
    return str(outcome.get("status", ""))


def _read_error(report: Mapping[str, Any]) -> Mapping[str, Any] | None:
    outcome = report.get("outcome") or {}
    error = outcome.get("error")
    return error if isinstance(error, Mapping) else None


def _read_refs(reports: Sequence[Mapping[str, Any]], key: str) -> tuple[str, ...]:
    for report in reports:
        refs = report.get(key)
        if isinstance(refs, Sequence) and not isinstance(refs, str):
            return tuple(str(ref) for ref in refs)
    return ()


def _is_allowing(status: str) -> bool:
    return status in _ALLOWING_STATUSES


def _describe_report(report: Mapping[str, Any]) -> str:
    """Serialize one report into the reason the host relays to the model."""
    function = str((report.get("context") or {}).get("function", "harness function"))
    status = _read_status(report)
    error = _read_error(report)
    if error is not None:
        return f"{function} {status}: {error.get('code')} — {error.get('message')}"
    if "conditionChecks" in report:
        return f"{function} {status}: {_describe_condition_checks(report)}"
    if "authorization" in report:
        return f"{function} {status}: {_describe_authorization(report)}"
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


def _describe_authorization(report: Mapping[str, Any]) -> str:
    authorization = report.get("authorization") or {}
    return (
        f"{authorization.get('failureMessage')} ({authorization.get('artifactPath')})"
    )


def _describe_artifact_checks(report: Mapping[str, Any]) -> str:
    return "; ".join(
        f"{check.get('artifactPath')}: {check.get('failureMessage')} "
        f"({(check.get('revert') or {}).get('action')})"
        for check in report.get("artifactChecks") or ()
    )


__all__ = ["HookRenderer"]
