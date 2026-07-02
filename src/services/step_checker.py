"""StepChecker — the check-step router: evaluate one step's conditions, optionally log it."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from models import Finding, Report
from persistence import ArtifactRepository, LogRepository, WorkflowRepository, Workspace
from .cel_evaluator import CelEvaluator
from .schema_checker import SchemaChecker


class StepChecker:
    """Evaluates one step's flat `conditions` list in authored order. Each condition is either
    `type: after` (a predecessor `step_id` that must be logged complete, resolved here) or
    `type: state` (a state assertion delegated to the `CelEvaluator`). It then records the
    canonical run-log line via the `LogRepository`."""

    def __init__(
        self,
        workspace: Workspace,
        workflows: WorkflowRepository,
        artifacts: ArtifactRepository,
        logs: LogRepository,
        cel: CelEvaluator,
        schema_checker: SchemaChecker,
    ) -> None:
        self.workspace = workspace
        self.workflows = workflows
        self.artifacts = artifacts
        self.logs = logs
        self.cel = cel
        self.schema_checker = schema_checker

    # --- the check-step router ---------------------------------------------
    def check_step(
        self,
        orchestration_id: str,
        step_id: str,
        unit_id: str,
        log_path: Path | None = None,
        record: bool = False,
    ) -> Report:
        report = Report()
        workflow = self.workflows.find(orchestration_id)
        if workflow is None:
            report.error("check-step", f"no contract found for orchestration {orchestration_id!r}")
            return report
        label = self.workspace.label(workflow.path, self.workspace.framework_root)
        steps = workflow.steps
        if not steps:
            report.error(label, "workflow has no steps[]")
            return report
        step = workflow.step(step_id)
        if step is None:
            report.error(label, f"step {step_id!r} is not declared in the contract")
            return report

        artifact = self.artifacts.resolve_unit(unit_id)
        if artifact is None:
            report.warn("check-step", f"unit {unit_id!r} not found; state conditions evaluate against an empty unit")

        log = self.logs.read(log_path)
        executed_steps = log.executed_steps() if log is not None else None

        results: list[dict[str, Any]] = []
        counts = {"pass": 0, "fail": 0, "skipped": 0}

        def record_check(ckind: str, ctype: str, result: str, *,
                         cid: str | None = None, reason: str | None = None, detail: str | None = None,
                         descr: str | None = None, selector_summary: str | None = None) -> None:
            entry: dict[str, Any] = {"kind": ckind, "type": ctype, "result": result}
            if cid:
                entry["id"] = cid
            if reason:
                entry["reason"] = reason
            if detail:
                entry["detail"] = detail
            if selector_summary:
                entry["selector"] = selector_summary
            results.append(entry)
            counts[result] += 1
            if result == "fail":
                finding = Finding("error", "check-step", f"[{ctype}/{ckind}] {cid} FAILED — {descr or cid}")
                finding.condition_id = cid
                finding.expected = "pass"
                finding.actual = "fail"
                report.add(finding)

        for condition in step.conditions:
            ckind = condition.kind
            ctype = condition.type
            cond_id = condition.id

            if ctype == "after":
                # type: after → check predecessor step completion via condition.step_id
                predecessor_id = condition.step_id
                if not predecessor_id:
                    report.error(label, f"step '{step_id}' condition '{cond_id}': type=after requires step_id")
                    continue
                if executed_steps is None:
                    record_check(ckind, ctype, "skipped", cid=cond_id, reason="unavailable", descr=f"predecessor '{predecessor_id}' (no run log supplied)")
                elif predecessor_id in executed_steps:
                    record_check(ckind, ctype, "pass", cid=cond_id)
                else:
                    record_check(ckind, ctype, "fail", cid=cond_id, descr=f"predecessor '{predecessor_id}' is not logged complete")

            elif ctype == "state":
                # type: state → CelEvaluator asserts on the acting unit (set_type: unit) or over a
                # selected artifact set (set_type: artifact).
                if not cond_id:
                    report.error(label, f"step '{step_id}': type=state condition is missing its required id")
                    continue
                selector = condition.set_selector
                predicate_src = condition.set_predicate
                if not selector or not predicate_src:
                    report.error(label, f"step '{step_id}' condition '{cond_id}': type=state requires set_selector and set_predicate")
                    continue
                outcome, detail = self.cel.evaluate_state(selector, predicate_src, artifact)
                if outcome == "error":
                    report.error(label, f"step '{step_id}' condition '{cond_id}': {detail}")
                    continue
                record_check(ckind, ctype, outcome, cid=cond_id, descr=detail, selector_summary=selector.get("set_predicate"))

            else:
                report.error(label, f"step '{step_id}' condition '{cond_id}': unknown condition type {ctype!r} (expected after|state)")

        print(f"check-step {orchestration_id}/{step_id} unit={unit_id}: pass={counts['pass']}, fail={counts['fail']}, skipped={counts['skipped']}", file=sys.stderr)
        for entry in results:
            if entry["result"] == "skipped":
                why = entry.get("reason") or ""
                note = entry.get("detail") or ""
                print(f"  skipped ({entry.get('type')}{', ' + why if why else ''}): {entry.get('id')}{' — ' + note if note else ''}", file=sys.stderr)

        if record and log_path is not None:
            payload: dict[str, Any] = {"conditions": results}
            payload["counts"] = counts
            status = "failed" if counts["fail"] else "completed"
            self.logs.append_entry(
                log_path,
                command="check-step",
                payload=payload,
                trigger="agent",
                orchestration=orchestration_id,
                step=step_id,
                unit=unit_id,
                actor=str(step.actor).lstrip("@") or None,
                status=status,
            )
            print(f"recorded step '{step_id}' -> {self.workspace.label(log_path, self.workspace.framework_root)} (status={status})", file=sys.stderr)
        return report

    # --- session-close review ----------------------------------------------
    def review_session(self, ledger_path: Path) -> Report:
        """Session-close review: read the per-session ledger and re-evaluate every step it recorded
        against the FINAL artifact state (so postconditions that only hold once later artifacts exist
        are caught at the boundary), then print a coverage summary. Read-only — never appends to the
        ledger (record=False), so a review leaves no trace of itself."""
        report = Report()
        log = self.logs.read(ledger_path)
        if log is None:
            return report
        steps: list[tuple[str, str, str]] = []
        for entry in log.entries():
            orchestration, step, unit = entry.orchestration, entry.step, entry.unit
            if orchestration and step and unit:
                key = (str(orchestration), str(step), str(unit))
                if key not in steps:
                    steps.append(key)
        for orchestration, step, unit in steps:
            report.extend(self.check_step(orchestration, step, unit, ledger_path, record=False))
        writes = sum(1 for entry in log.entries() if entry.outputs)
        denials = sum(1 for entry in log.entries() if entry.permission == "deny")
        units = len({unit for _, _, unit in steps})
        label = self.workspace.label(ledger_path, self.workspace.framework_root)
        print(f"session-review {label}: steps={len(steps)} units={units} writes={writes} denied={denials}", file=sys.stderr)
        return report
