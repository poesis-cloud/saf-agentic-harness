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
from config import ConfigError, FrameworkConfig
from mappers import (
    ArtifactMapper,
    LogMapper,
    Workspace,
)
from utils import ArtifactValidator
from services import (
    ArtifactChecker,
    CelEvaluator,
    HookService,
    ModelRouter,
    OrchestrationService,
    SchemaChecker,
    StepChecker,
)
from .command import Command


# --- per-command argument configurators -------------------------------------
def _configure_check_artifact(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--unit-id", help="validate only one artifact by its globally-unique id")
    parser.add_argument("--path", type=Path, help="validate one native JSON artifact (*.artifact.json) directly against its schema")


def _configure_check_step(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--orchestration", required=True, help="workflow id (root or sub-workflow id) — the workflow config is resolved from it")
    parser.add_argument("--step", required=True, help="structurant step id within that workflow")
    parser.add_argument("--unit-id", required=True, help="artifact id the step acts on (scope is derived from its workspace path)")
    parser.add_argument("--session", help="host session id selecting the per-session run ledger (logs/hooks/<session>.jsonl) — read for predecessor checks and always appended to; omit to use the shared 'session' ledger")


def _configure_hook(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--event", required=True, help="host lifecycle event (sessionStart/userPromptSubmit/preToolUse/postToolUse/stop/sessionEnd)")
    parser.add_argument("--env", default="github-copilot", help="host environment binding under adapters/<env>/tools.yaml (default: github-copilot)")


def _configure_orchestrate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workflow", help="workflow id to drive; omit to get the advisory `propose` (the next natural workflow(s) from the catalog's workflow-level after graph)")
    parser.add_argument("--unit", help="artifact slug the workflow acts on")
    parser.add_argument("--run", help="run id selecting the per-run journal (workspace/logs/<run>.jsonl); the resolved action is appended there as one enveloped entry")


# --- the registry (metadata; runners are bound in Application) ---------------
COMMANDS: list[Command] = [
    Command("check-artifact", "validate artifact state (linkage, schema, open-items)",
            "The STATE plane. With no args: sweep every artifact — scope/frontmatter coherence, parent linkage, blocking open_items, and JSON Schema conformance.\n  --unit-id <id>   scope every check to one unit (ids are globally unique across the workspace).\n  --path <file>    validate one native JSON artifact (*.artifact.json) directly against its schema.\nThe harness never writes — it reports findings; the orchestrator commits.\nExample: harness.py --workspace-root workspace check-artifact --unit-id my-unit",
            _configure_check_artifact),
    Command("check-step", "evaluate one step's preconditions and postconditions and append the step line to the session ledger",
            "The CONDITIONS plane. Evaluate a step's `conditions` (each is `kind: precondition|postcondition`, `type: after|state`, `id`): `type: after` checks a predecessor step (resolved from the run ledger), `type: state` asserts on the persisted workspace via CEL (artifact selection + predicate). Authorization is also checked at precondition. The workflow config is resolved from --orchestration; scope is derived from the resolved unit's workspace path. The step's canonical, schema-valid line is ALWAYS appended to the per-session run ledger (logs/hooks/<session>.jsonl, selected by --session) — that same ledger feeds predecessor `after` checks and the session-close review.\nExample: harness.py check-step --orchestration my-workflow --step my-step --unit-id my-unit --session abc123",
            _configure_check_step),
    Command("hook", "environment-hook adapter: funnel a lifecycle event through the harness",
            "Read a host lifecycle event (JSON on stdin) and route it to the deterministic checks: preToolUse authorizes the write (deny ungranted), postToolUse validates the written native-JSON artifact, session-close reviews the recorded steps' postconditions, sessionStart injects deterministic context. Emits the host's decision JSON on stdout; exit 2 = deny/fail. The shared host adapter (adapters/dispatch.sh <event> <env>) calls this; the CLI stays the single source of truth.\nExample: cat event.json | harness.py hook --event preToolUse",
            _configure_hook),
    Command("orchestrate", "resolve the next orchestration action (dispatch | propose | halt | done) for a workflow + unit",
            "The DRIVE plane. With --workflow: recompute the step cursor from the RUN JOURNAL (a step counts as completed when its latest journaled check-step line for that workflow says so) and return exactly one action as JSON on stdout: `dispatch` (the next eligible step with its resolved {actor, model, skills, artifacts, instructions, prompts} — the model routed from the step's weighted capabilities against conf/model-profiles.conf.yaml; see harness/def/spec.md 'Model Routing'), `halt` (no step is eligible while work remains, or the step is unroutable), or `done` (every step journaled complete; carries the advisory `propose` successors). Without --workflow: return `propose` — the eligible next natural workflow(s) from the catalog's advisory workflow-level `after` graph; the sequence never constrains — the user may (re)run any workflow, and assent starts it. The harness never writes artifacts — it returns the action; the host commits it.\nExample: harness.py orchestrate --workflow my-workflow --unit my-unit --run r-1",
            _configure_orchestrate),
]

CLI_DESCRIPTION = "Deterministic check-only orchestration harness: validate artifacts (state + derived fields) and step conditions, and adapt host lifecycle hooks. The framework constitution (workflow contracts + artifact catalog) is verified separately by the pytest suite (make verify)."
CLI_EPILOG = "Run '<command> --help' for command-specific arguments. Global options (--workspace-root, --strict, --json) come before the command."


class Application:
    """The composition root: one Workspace, the repositories, and the services, wired in
    dependency order, plus the CLI dispatch."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

        # configuration plane — parsed AND contract-validated at initialization; every CLI
        # interaction fails fast (ConfigError) on an invalid conf/*.conf.yaml before any
        # command logic runs. The framework (the application embedding the harness) and the
        # workspace (the data plane — this framework's portfolio) are distinct planes.
        self.config = FrameworkConfig.detect(workspace)
        schemas = self.config.schemas
        workflows = self.config.workflows
        self.schemas = schemas

        # mappers (workspace data access)
        self.validator = ArtifactValidator(workspace, schemas)
        artifacts = ArtifactMapper(workspace, self.validator)
        self.artifacts = artifacts
        logs = LogMapper(workspace)
        self.logs = logs

        # services (in dependency order — no cycles)
        self.schema_checker = SchemaChecker(workspace, schemas, self.validator)
        self.artifact_checker = ArtifactChecker(workspace, artifacts, self.schema_checker)
        cel = CelEvaluator(workspace, artifacts, schemas)
        self.step_checker = StepChecker(workspace, workflows, artifacts, logs, cel, self.schema_checker)
        self.router = ModelRouter(self.config.model_profiles)
        self.orchestration = OrchestrationService(workspace, workflows, artifacts, self.router, logs)

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
        if args.unit_id is not None:
            sub, _ = self.artifact_checker.check_target(args.unit_id, None)
            return sub
        return self.artifact_checker.check_all()

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
        hooks = HookService(self.workspace, self.schemas, self.logs, self.config.access_control_list, self.config.workspace_layout, self.config.workflows, self.router, binding=self.config.adapter_binding(args.env), artifacts=self.artifacts)
        decision = hooks.handle(args.event, payload)
        report = decision.report
        for command in hooks.commands_for(decision.phase):   # map-driven, write-scope only
            if command == "check-artifact":
                for ref in decision.outputs:
                    if ref.endswith(".json"):
                        report.extend(self.schema_checker.check_json((self.workspace.workspace_base / ref).resolve()))
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
        parser.add_argument("--workspace-root", type=Path, help="workspace root (default: <framework-root>/workspace)")
        parser.add_argument("--portfolio-root", type=Path, help=argparse.SUPPRESS)
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
    workspace_root = args.workspace_root or args.portfolio_root
    workspace = Workspace.detect(None, workspace_root)
    try:
        application = Application(workspace)
    except ConfigError as exc:
        # invalid framework configuration — render every finding and fail before any command runs.
        return exc.report.print_json(strict=True) if args.json else exc.report.print(strict=True)
    report = application.dispatch(args)
    if args.command in ("hook", "orchestrate"):
        # the action/decision JSON is already on stdout; exit non-zero = deny/error.
        return 2 if any(f.severity == "error" for f in report.findings) else 0
    return report.print_json(args.strict) if args.json else report.print(args.strict)


__all__ = ["main", "Application", "Command", "COMMANDS"]
