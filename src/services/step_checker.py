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
    """Evaluates one step's flat `conditions` list in authored order, routing each by
    `expression`: structural `ref` (after/input/output) resolved here, `cel` delegated to
    the `CelEvaluator`, `instruction` (an invariant's obligation file) resolved here. It
    then records the canonical run-log line via the `LogRepository`."""

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

    # --- structural ref resolution -----------------------------------------
    def _resolve_ref(self, contract_dir: Path | None, product: str | None, unit_id: str, ref: str) -> Path | None:
        """Resolve a reads/writes ref to a concrete repo path, or None for a logical ref."""
        if "{product}" in ref and not product:
            return None  # product-scoped ref with no product in scope — assert via a `cel` predicate
        ref = ref.replace("{unit_id}", unit_id).replace("{product}", product or "")
        if "[" in ref or "/" not in ref:
            return None  # logical ref (epic.artifact.json, raw-idea, open_items[kind=...])
        if ref.startswith("artifacts/") and contract_dir is not None:
            return contract_dir / ref  # suborchestration-local template
        return self.workspace.portfolio_base / ref  # repo-root-relative (portfolio/...)

    def _ref_status(self, contract_dir: Path | None, product: str | None, unit_id: str, ref: str) -> tuple[str, str | None]:
        """Structural existence check for a `reads`/`writes` ref. Returns (result, reason)."""
        resolved = self._resolve_ref(contract_dir, product, unit_id, ref)
        if resolved is None:
            return ("skipped", "logical")
        is_dir_ref = ref.endswith("/")
        exists = resolved.is_dir() if is_dir_ref else resolved.is_file()
        if not exists:
            return ("fail", None)
        if not is_dir_ref and resolved.name.endswith(".artifact.json"):
            if self.schema_checker.check_json(resolved).has_errors():
                return ("fail", None)
        return ("pass", None)

    @staticmethod
    def _suggest(ctype: str, value: str | None) -> str:
        if ctype == "after":
            return f"run and log step '{value}' to completion before this step"
        if ctype == "input":
            return f"ensure input {value} exists before this step"
        if ctype == "output":
            return f"this step must produce {value}"
        return ""

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
        product = artifact.product_slug if artifact is not None else None

        log = self.logs.read(log_path)
        executed_steps = log.executed_steps() if log is not None else None
        activation, functions = self.cel.build_activation(unit_id, product, artifact)
        contract_dir = workflow.path.parent

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
                # type: state → two-CEL pipeline: set_query selects a bounded artifact set,
                # set_predicate asserts a boolean over that selected set (see CelEvaluator).
                if not cond_id:
                    report.error(label, f"step '{step_id}': type=state condition is missing its required id")
                    continue
                selector = condition.set_selector
                predicate_src = condition.set_predicate
                if not selector or not predicate_src:
                    report.error(label, f"step '{step_id}' condition '{cond_id}': type=state requires set_selector and set_predicate")
                    continue
                outcome, detail = self.cel.evaluate_state(selector, predicate_src)
                if outcome == "error":
                    report.error(label, f"step '{step_id}' condition '{cond_id}': {detail}")
                    continue
                record_check(ckind, ctype, outcome, cid=cond_id, descr=detail, selector_summary=selector.get("set_query"))

            else:
                # Unknown type (old model still uses expression-based routing for backward compatibility)
                expression = condition.expression
                value = condition.value
                if expression == "ref":
                    # Old model: structural ref checks (legacy, not in new model)
                    if ctype == "after":
                        if executed_steps is None:
                            record_check(ckind, ctype, "skipped", reason="unavailable", descr=f"predecessor '{value}' (legacy ref, no run log supplied)")
                        elif value in executed_steps:
                            record_check(ckind, ctype, "pass")
                        else:
                            record_check(ckind, ctype, "fail", descr=f"predecessor '{value}' is not logged complete")
                    else:
                        result, reason = self._ref_status(contract_dir, product, unit_id, value)
                        record_check(ckind, ctype, result, reason=reason, descr=f"{ctype} {value}")
                elif expression == "cel":
                    # Old model: CEL on unit facts (legacy)
                    if not cond_id:
                        report.error(label, f"step '{step_id}': {ctype or 'untyped'} cel condition is missing its required id")
                        continue
                    ekind, ok = self.cel.evaluate_expr(value, activation, functions)
                    if ekind == "error":
                        report.error(label, f"step '{step_id}' condition '{cond_id}': {ok}")
                        continue
                    record_check(ckind, ctype, "pass" if ok else "fail", cid=cond_id, descr=value)
                elif expression == "instruction":
                    # Old model: obligation files (legacy)
                    if not cond_id:
                        report.error(label, f"step '{step_id}': instruction condition is missing its required id")
                        continue
                    resolved = contract_dir / value
                    if resolved.is_file():
                        record_check(ckind, ctype, "pass", cid=cond_id, descr=value)
                    else:
                        record_check(ckind, ctype, "fail", cid=cond_id, descr=f"obligation file not found: {value}")
                else:
                    report.error(label, f"step '{step_id}': condition has unknown type/expression {ctype!r}/{expression!r}")

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
