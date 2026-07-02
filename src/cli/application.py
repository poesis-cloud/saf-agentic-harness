"""Application — the CLI composition root: wire the workspace, repositories, and services.

`build_parser` renders the command registry into argparse; `Application` instantiates one
Workspace, the repositories, and the services (constructor injection, in dependency order),
and `dispatch` routes a parsed namespace to the owning service. Command logic lives in the
services — never here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from models import Report
from persistence import (
    ArtifactRepository,
    LogRepository,
    SchemaRepository,
    Workspace,
    WorkflowRepository,
)
from utils import ArtifactValidator
from services import (
    ArtifactChecker,
    AuthorizationPolicy,
    CalculationService,
    CelEvaluator,
    HookService,
    ModelRouter,
    OrchestrationService,
    SchemaChecker,
    StepChecker,
    TransitionPolicy,
)
from .command import Command


# --- per-command argument configurators -------------------------------------
def _configure_check_artifact(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--unit-id", help="validate only one Epic/Feature/Story by its globally-unique id")
    parser.add_argument("--path", type=Path, help="validate one native JSON artifact (*.artifact.json) directly against its schema")
    parser.add_argument("--to", help="with --unit-id: target status of a status edge to evaluate against the transition guard")
    parser.add_argument("--gate", choices=["accept", "reject"], help="with --to: explicit decision for an edge that crosses a ★ gate")
    parser.add_argument("--orchestrator", help="with --to: committing orchestrator (owner check: vmo->epic, rte->feature, sm->story)")


def _configure_check_step(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--orchestration", required=True, help="workflow id (root or sub-workflow id) — the workflow.yaml is resolved from it")
    parser.add_argument("--step", required=True, help="structurant step id within that workflow")
    parser.add_argument("--unit-id", required=True, help="Epic / Feature / Story id the step acts on (product is derived from it)")
    parser.add_argument("--session", help="host session id selecting the per-session run ledger (logs/hooks/<session>.jsonl) — read for predecessor checks and always appended to; omit to use the shared 'session' ledger")


def _configure_hook(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--event", required=True, help="host lifecycle event (sessionStart/userPromptSubmit/preToolUse/postToolUse/stop/sessionEnd)")
    parser.add_argument("--env", default="github-copilot", help="host environment binding under adapters/<env>/tools/map.yaml (default: github-copilot)")


def _configure_orchestrate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workflow", required=True, help="workflow id (root or sub-workflow id) to drive")
    parser.add_argument("--unit", help="Epic / Feature / Story id the workflow acts on (its artifacts drive the step cursor)")
    parser.add_argument("--run", help="run id selecting the per-run journal (portfolio/logs/<run>.jsonl); the resolved action is appended there as one enveloped entry")


# --- the registry (metadata; runners are bound in Application) ---------------
COMMANDS: list[Command] = [
    Command("check-artifact", "validate Epic/Feature/Story state (FSM, linkage, schema, gates, derived fields)",
            "The STATE plane. With no args: sweep every Epic/Feature/Story — status FSM, product/path coherence, parent linkage, blocking open_items across gates, JSON Schema conformance, staged gate-packet evidence, and wsjf/cost drift.\n  --unit-id <id>   scope every check to one unit (ids are globally unique across the portfolio).\n  --path <file>    validate one native JSON artifact (*.artifact.json) directly against its schema.\n  --to <status>    with --unit-id: evaluate that status edge against the transition guard (legal edge, structurant precondition, no blocking open_items, ★-gate accept/reject) and report OK-to-commit or blocked.\nThe harness never writes — it reports the value/edge; the orchestrator commits.\nExample: harness.py --portfolio-root portfolio check-artifact --unit-id sie-observability-foundation",
            _configure_check_artifact),
    Command("check-step", "evaluate one step's preconditions and postconditions and append the step line to the session ledger",
            "The CONDITIONS plane. Evaluate a step's `conditions` (each is `kind: precondition|postcondition`, `type: after|state`, `id`): `type: after` checks a predecessor step (resolved from the run ledger), `type: state` asserts on the persisted portfolio via CEL (artifact selection + predicate). Authorization is also checked at precondition. The workflow.yaml is resolved from --orchestration; product is derived from the resolved unit. The step's canonical, schema-valid line is ALWAYS appended to the per-session run ledger (logs/hooks/<session>.jsonl, selected by --session) — that same ledger feeds predecessor `after` checks and the session-close review.\nExample: harness.py check-step --orchestration value-management-officier --step capture-epic --unit-id sie-observability-foundation --session abc123",
            _configure_check_step),
    Command("hook", "environment-hook adapter: funnel a lifecycle event through the harness",
            "Read a host lifecycle event (JSON on stdin) and route it to the deterministic checks: preToolUse authorizes the write (deny ungranted), postToolUse validates the written native-JSON artifact, session-close reviews the recorded steps' postconditions, sessionStart injects deterministic context. Emits the host's decision JSON on stdout; exit 2 = deny/fail. The shared host adapter (adapters/dispatch.sh <event> <env>) calls this; the CLI stays the single source of truth.\nExample: cat event.json | harness.py hook --event preToolUse",
            _configure_hook),
    Command("orchestrate", "resolve the next orchestration action (dispatch | halt | done) for a workflow + unit",
            "The DRIVE plane. Recompute the step cursor from the unit's ARTIFACTS (never a prior log line) and return exactly one action as JSON on stdout: `dispatch` (the next eligible step with its resolved {actor, model, skills, output, instructions, prompts}), `halt` (a ★ gate is next, or no step is eligible while work remains), or `done` (every step's output artifact exists). The model resolves deterministically from the actor's role via harness/llm/map.yaml. The harness never writes — it returns the action; the host commits it.\nExample: harness.py orchestrate --workflow value-management-officier --unit sie-observability-foundation",
            _configure_orchestrate),
]

CLI_DESCRIPTION = "Deterministic check-only harness for Poesis SAFe orchestration: validate artifacts (state + derived fields) and step conditions, and adapt host lifecycle hooks. The framework constitution (workflow contracts + artifact catalog) is verified separately by the pytest suite (make verify)."
CLI_EPILOG = "Run '<command> --help' for command-specific arguments. Global options (--portfolio-root, --strict, --json) come before the command."


class Application:
    """The composition root: one Workspace, the repositories, and the services, wired in
    dependency order, plus the CLI dispatch."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

        # persistence (data-mappers)
        workflows = WorkflowRepository(workspace)
        schemas = SchemaRepository(workspace)
        self.validator = ArtifactValidator(workspace, schemas)
        artifacts = ArtifactRepository(workspace, self.validator)
        self.artifacts = artifacts
        logs = LogRepository(workspace)
        self.logs = logs

        # services (in dependency order — no cycles)
        policy = TransitionPolicy()
        self.schema_checker = SchemaChecker(workspace, schemas, self.validator)
        self.calculation = CalculationService(workspace, artifacts, policy)
        self.artifact_checker = ArtifactChecker(workspace, artifacts, self.schema_checker, policy)
        cel = CelEvaluator(workspace, artifacts, schemas)
        self.step_checker = StepChecker(workspace, workflows, artifacts, logs, cel, self.schema_checker)
        self.schemas = schemas
        self.router = ModelRouter(workspace)
        self.orchestration = OrchestrationService(workspace, workflows, artifacts, self.router)

        self._runners: dict[str, Callable[[argparse.Namespace], Report]] = {
            "check-artifact": self._run_check_artifact,
            "check-step": self._run_check_step,
            "hook": self._run_hook,
            "orchestrate": self._run_orchestrate,
        }

    # --- runners ------------------------------------------------------------
    def _run_check_artifact(self, args: argparse.Namespace) -> Report:
        if args.path is not None:
            return self.schema_checker.check_json(args.path.resolve())
        report = Report()
        if args.to is not None:
            if not args.unit_id:
                report.error("check-artifact", "--to requires --unit-id (the unit whose status edge to check)")
                return report
            return self.calculation.transition(args.unit_id, args.to, args.gate, args.orchestrator)
        if args.unit_id is not None:
            sub, targets = self.artifact_checker.check_target(args.unit_id, None)
            report.extend(sub)
            if len(targets) != 1:
                return report
            report.extend(self.artifact_checker.check_gate_packet(args.unit_id))
            report.extend(self.calculation.wsjf(args.unit_id))
            report.extend(self.calculation.roll_cost(args.unit_id))
            return report
        report.extend(self.artifact_checker.check_all())
        report.extend(self.artifact_checker.check_gate_packet(None))
        report.extend(self.calculation.wsjf(None))
        report.extend(self.calculation.roll_cost(None))
        return report

    def _run_check_step(self, args: argparse.Namespace) -> Report:
        ledger = self.workspace.session_ledger(args.session)
        return self.step_checker.check_step(args.orchestration, args.step, args.unit_id, ledger, record=True)

    def _run_hook(self, args: argparse.Namespace) -> Report:
        """Adapter entry: read the host event (JSON on stdin), route it through the HookService,
        record the observation to the session ledger, and emit the host decision JSON on stdout.
        At session-close it reviews the recorded steps' postconditions against final state
        (the redundant full backlog re-sweep is dropped — run it explicitly via check-artifact).
        Errors here = exit 2 (deny)."""
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        hooks = HookService(self.workspace, self.schemas, self.logs, AuthorizationPolicy(), env=args.env, artifacts=self.artifacts)
        decision = hooks.handle(args.event, payload)
        report = decision.report
        for command in hooks.commands_for(decision.phase):   # map-driven, write-scope only
            if command == "check-artifact":
                for ref in decision.outputs:
                    if ref.endswith(".json"):
                        report.extend(self.schema_checker.check_json((self.workspace.portfolio_base / ref).resolve()))
        if decision.phase == "session-close":
            report.extend(self.step_checker.review_session(hooks.ledger_path(payload)))
        hooks.record(args.event, payload, decision)
        deny = decision.permission == "deny" or any(f.severity == "error" for f in report.findings)
        if deny:
            report.error("hook", decision.reason or "blocked by harness")
        out: dict[str, object] = {"permission": "deny" if deny else "allow", "reason": decision.reason}
        if decision.context:
            out["additionalContext"] = decision.context   # deterministic context injection (session-open)
        print(json.dumps(out))
        return report

    def _run_orchestrate(self, args: argparse.Namespace) -> Report:
        """Resolve the next orchestration action for a (workflow, unit) and emit it as JSON on stdout.
        The harness never writes artifacts — the host commits the returned dispatch/halt/done. When
        --run is supplied the action is also appended (as one enveloped entry) to the run journal,
        so the ordered `dispatch` entries reconstruct the run's step sequence."""
        report = Report()
        action = self.orchestration.orchestrate(args.workflow, run=args.run, unit=args.unit)
        print(json.dumps(action))
        if args.run:
            actor = str(action.get("actor") or "").lstrip("@") or None
            self.logs.append_entry(
                self.workspace.run_journal(args.run),
                command="orchestrate",
                payload=action,
                trigger="agent",
                run=args.run,
                orchestration=action.get("workflow"),
                step=action.get("step"),
                unit=action.get("unit"),
                actor=actor,
                status=action.get("action"),
            )
        if action.get("action") == "error":
            report.error("orchestrate", str(action.get("reason") or "orchestration error"))
        return report

    def dispatch(self, args: argparse.Namespace) -> Report:
        return self._runners[args.command](args)

    # --- parser -------------------------------------------------------------
    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=CLI_DESCRIPTION,
            epilog=CLI_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("--portfolio-root", type=Path, help="portfolio root (default: <framework-root>/portfolio)")
        parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
        parser.add_argument("--json", action="store_true", help="emit findings as structured JSON (machine-actionable)")
        subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
        for command in COMMANDS:
            sub = subparsers.add_parser(
                command.name,
                help=command.summary,
                description=command.description,
                formatter_class=argparse.RawDescriptionHelpFormatter,
            )
            command.configure(sub)
        return parser


def main(argv: list[str] | None = None) -> int:
    parser = Application.build_parser()
    args = parser.parse_args(argv)
    workspace = Workspace.detect(None, args.portfolio_root)
    report = Application(workspace).dispatch(args)
    if args.command in ("hook", "orchestrate"):
        # the action/decision JSON is already on stdout; exit non-zero = deny/error.
        return 2 if any(f.severity == "error" for f in report.findings) else 0
    return report.print_json(args.strict) if args.json else report.print(args.strict)


__all__ = ["main", "Application", "Command", "COMMANDS"]
