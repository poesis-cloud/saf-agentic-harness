"""HookService — the environment-adapter plane: funnel every agent action through the harness.

A host environment (GitHub Copilot CLI first) fires lifecycle hooks; each hook calls the harness
`hook` command, which routes the event here. This service is the ONE place that turns a generic
environment event into the same deterministic checks the CLI already exposes — it never becomes a
second source of truth. Tool names, payload keys, and write verbs are host-specific, so they are NOT
hardcoded: each `adapters/<env>/tools/map.yaml` declares them and the core stays env-agnostic.

Host events normalize to the WORKFLOW's own vocabulary (a new host maps its events in EVENT_PHASE):
  - session-open   (sessionStart)        — constitution preflight + deterministic context injection
  - observe        (userPromptSubmit)    — no enforcement
  - precondition   (preToolUse)          — authorization (authority) + preconditions on the write
  - postcondition  (postToolUse)         — postconditions on the produced artifact
  - session-close  (stop/sessionEnd)     — return-boundary; can block

So the env hooks bind directly to the workflow concepts precondition / postcondition / authorization.
It returns a `HookDecision` the adapter renders into the host's expected JSON. Deterministic and
check-only: it appends observations, never edits artifacts.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from models import Report
from persistence import ArtifactRepository, LogRepository, SchemaRepository, Workspace, WorkflowRepository
from .authorization_checker import AuthorizationChecker
from .authorization_policy import AuthorizationPolicy
from .model_router import ModelRouter
# Host event name -> normalized workflow concept. Hosts vary, so a new env adds rows; the core
# reasons on the workflow's own vocabulary (precondition / postcondition + the session boundaries).
EVENT_PHASE: dict[str, str] = {
    "sessionStart": "session-open",
    "userPromptSubmit": "observe",
    "preToolUse": "precondition",
    "postToolUse": "postcondition",
    "stop": "session-close",
    "sessionEnd": "session-close",
}


@dataclass
class HookDecision:
    """The verdict the adapter renders for the host. `permission` is allow/deny (precondition +
    session phases can block); observe-only phases return allow. `report` carries findings; `outputs`
    echoes the derived write refs for the ledger; `phase` is the normalized workflow concept;
    `context` is deterministic additionalContext the host injects into the agent (session-open:
    workflow + instructions + step/sub-orchestration skills)."""

    permission: str = "allow"
    reason: str = ""
    phase: str = "observe"
    report: Report = field(default_factory=Report)
    outputs: list[str] = field(default_factory=list)
    context: str = ""


class HookService:
    # Hardcoded phase→command policy (formerly in map/command.map.yaml): postcondition auto-runs
    # check-artifact on writes (write-scope); session-open injects workflow + instructions + skills.
    # All other phases have empty run lists. Unit-scoped commands (check-step) need --orchestration/
    # --step/--unit-id so they are agent-driven at the step boundary, never hook-auto-run.
    POSTCONDITION_AUTO_RUN = ["check-artifact"]
    SESSION_OPEN_INJECT = ["workflow", "instructions", "skills"]

    def __init__(self, workspace: Workspace, schemas: SchemaRepository, logs: LogRepository, policy: AuthorizationPolicy, env: str = "github-copilot", artifacts: ArtifactRepository | None = None) -> None:
        self.workspace = workspace
        self.logs = logs
        self.policy = policy
        self.artifacts = artifacts
        self.authz = AuthorizationChecker(workspace, schemas, logs, policy)
        self.workflows = WorkflowRepository(workspace)
        self.router = ModelRouter(workspace)
        self.binding = self._load_binding(env)

    def _load_binding(self, env: str) -> dict[str, Any]:
        path = self.workspace.harness_dir / "adapters" / env / "tools" / "map.yaml"
        return yaml.safe_load(self.workspace.read_text(path)) or {} if path.is_file() else {}

    def commands_for(self, phase: str) -> list[str]:
        """CLI commands the adapter auto-runs at this phase: write-scope check-artifact at postcondition;
        all others empty (unit-scope checks need unit-id/step and stay agent-driven)."""
        if phase == "postcondition":
            return self.POSTCONDITION_AUTO_RUN
        return []

    # --- event routing ------------------------------------------------------
    def handle(self, event: str, payload: dict[str, Any]) -> HookDecision:
        phase = EVENT_PHASE.get(event, "observe")
        if phase == "precondition":
            if self._is_dispatch(payload):
                return self._dispatch(payload)
            return self._precondition(payload)
        if phase == "postcondition":
            return self._postcondition(payload)
        if phase == "session-open":
            return HookDecision(phase=phase, context=self._inject(phase, payload))
        return HookDecision(phase=phase)

    def _inject(self, phase: str, payload: dict[str, Any]) -> str:
        """Deterministic additionalContext for this phase. Two session shapes, distinguished by
        dispatch correlation (Option B — correlate-by-actor via the run journal's open `dispatch`
        entry): a CHILD STEP session (the actor has an open dispatch addressed to it) inherits ITS
        step's (orchestration, step, unit) and gets that step's skills + invariants — skills are
        injected PER STEP; an ORCHESTRATOR session (no open dispatch) gets the root's workflow map +
        the suborchestration skill map (sub-id → procedure skill + invariants). No agent discretion —
        the workflow + the journal decide; the `inject:` list selects which (workflow/skills/instructions)."""
        if phase != "session-open":
            return ""
        wants = self.SESSION_OPEN_INJECT
        actor = self._actor(payload)
        frame = self._open_dispatch(actor)
        if frame is not None:
            return self._inject_step(frame, wants)
        return self._inject_orchestrator(actor, wants)

    def _inject_orchestrator(self, actor: str, wants: list[str]) -> str:
        """The orchestrator session's context: the root it facilitates + the suborchestration skill
        map (each sub-id → procedure skill + instructions + prompts), so the driver loads the right
        resources the moment it enters a sub. The active actor scopes it to the root it facilitates."""
        all_wf = self.workflows.all()
        blocks: list[str] = []
        for wf in (w for w in all_wf if w.is_root):
            if actor and str(wf.facilitator).lstrip("@") != actor.lstrip("@"):
                continue
            if "workflow" in wants:
                blocks.append(f"orchestration {wf.id}: facilitate {wf.facilitator}")
            if "resources" in wants or "instructions" in wants:  # support both names during transition
                instr = self._aggregate_guidance(wf, "instructions")
                prompts = self._aggregate_guidance(wf, "prompts")
                if instr:
                    blocks.append("follow instructions: " + ", ".join(instr))
                if prompts:
                    blocks.append("reference prompts: " + ", ".join(prompts))
            subs = [s for s in all_wf if s.parent == wf.id]
            for sub in sorted(subs, key=lambda s: str(s.id)):
                parts: list[str] = []
                if "skills" in wants and sub.id:
                    parts.append("load skill " + str(sub.id))
                if "resources" in wants or "instructions" in wants:
                    instr = self._aggregate_guidance(sub, "instructions")
                    prompts = self._aggregate_guidance(sub, "prompts")
                    if instr:
                        parts.append("follow instructions " + ", ".join(instr))
                    if prompts:
                        parts.append("reference prompts " + ", ".join(prompts))
                if parts:
                    blocks.append(f"on suborchestration {sub.id}: " + "; ".join(parts))
        return "\n".join(blocks)

    def _inject_step(self, frame: Any, wants: list[str]) -> str:
        """A dispatched child step session inherits the step it was dispatched for and loads that
        step's skills (per-step injection). The skill resolves in precedence: an explicit step-level
        `skills`, else the target suborchestration id as its procedure skill (a delegate step IS the
        sub). Instruction and prompt refs come from the step's properties (new model) or from scanning
        conditions for `expression: instruction` invariants (old model, during migration)."""
        orchestration = frame.orchestration
        step_id = frame.step
        wf = self._workflow_by_id(orchestration)
        if wf is None or not step_id:
            return ""
        step = wf.step(step_id)
        parts: list[str] = []
        if "skills" in wants:
            skills = self._step_skills(wf, step)
            if skills:
                parts.append("load skill " + ", ".join(skills))
        if "resources" in wants or "instructions" in wants:
            if step is not None:
                instr = list(step.instructions) if hasattr(step, 'instructions') else []
                prompts = list(step.prompts) if hasattr(step, 'prompts') else []
                if instr:
                    parts.append("follow instructions " + ", ".join(instr))
                if prompts:
                    parts.append("reference prompts " + ", ".join(prompts))
        if not parts:
            return ""
        scope = f"step {step_id} (orchestration {orchestration}"
        if frame.unit:
            scope += f", unit {frame.unit}"
        scope += ")"
        return f"{scope}: " + "; ".join(parts)

    def _step_skills(self, wf: Any, step: Any) -> list[str]:
        if step is not None and step.skills:
            return step.skills
        if step is not None and step.delegates_to:
            target = self._workflow_by_id(str(step.delegates_to).rstrip("/").split("/")[-1])
            if target is not None and target.id:
                return [str(target.id)]
        return []

    def _aggregate_guidance(self, wf: Any, guidance_type: str) -> list[str]:
        """Collect guidance refs (instructions or prompts) from all steps in the workflow.
        guidance_type is either 'instructions' or 'prompts'."""
        refs: set[str] = set()
        if hasattr(wf, 'steps'):
            for step in wf.steps:
                if guidance_type == "instructions" and hasattr(step, 'instructions'):
                    refs.update(step.instructions)
                elif guidance_type == "prompts" and hasattr(step, 'prompts'):
                    refs.update(step.prompts)
        return sorted(refs)

    def _workflow_by_id(self, wid: Any) -> Any:
        if not wid:
            return None
        return next((w for w in self.workflows.all() if str(w.id) == str(wid)), None)

    def _open_dispatch(self, actor: str) -> Any:
        """The most-recent open `dispatch` addressed to this actor across the run journals (Option B
        correlate-by-actor). Sequential per-orchestrator WIP makes the latest dispatch to an actor its
        live step; the child step session reads it to inherit (orchestration, step, unit). Returns the
        LogEntry or None — a fresh orchestrator session (no prior dispatch to it) returns None."""
        if not actor:
            return None
        target = actor.lstrip("@")
        best: Any = None
        for path in self.workspace.run_journals():
            log = self.logs.read(path)
            if log is None:
                continue
            for entry in log.entries():
                if (entry.command == "orchestrate" and entry.status == "dispatch"
                        and entry.actor and entry.actor.lstrip("@") == target):
                    best = entry
        return best

    def _precondition(self, payload: dict[str, Any]) -> HookDecision:
        """preToolUse → the workflow's precondition + authorization on the write: the actor must hold
        the privilege for every produced resource, else deny (the funnel)."""
        actor = self._actor(payload)
        action, outputs = self._write(payload)
        if not outputs:
            return HookDecision(phase="precondition")
        if not actor:
            return HookDecision(reason=f"write {outputs} has no actor; authorship cannot be verified", phase="precondition", outputs=outputs)
        for ref in outputs:
            path = ref.split("#", 1)[0]  # any #property suffix is ignored — whole-resource RBAC
            resource = self.policy.singleton_kind(path) or self.authz.resource_for(path)
            if resource is None:
                continue
            if not self.policy.allows(actor, action, resource):
                return HookDecision("deny", f"{actor} lacks privilege {action}_{resource}", "precondition", outputs=outputs)
        return HookDecision(phase="precondition", outputs=outputs)

    def _postcondition(self, payload: dict[str, Any]) -> HookDecision:
        """postToolUse → enforce the Portfolio Validity Invariant on the just-written artifact:
        validate it against its schema; if invalid, REVERT the write from the portfolio (restore the
        last-good version if tracked, else delete the new file) and DENY with the schema findings so
        the host relays them to the agent, which retries. A valid write is recorded and allowed. This
        revert is the harness's single deliberate write — everything else stays check-only."""
        action, outputs = self._write(payload)
        if self.artifacts is not None and action in ("create", "edit", "write", "update") and outputs:
            for ref in outputs:
                abs_path = self._resolve_write_path(ref)
                artifact = self.artifacts.load_one(abs_path) if abs_path is not None else None
                if artifact is None:
                    continue  # not a portfolio artifact — nothing to enforce
                report = self.artifacts.validator.validate(artifact) if self.artifacts.validator else Report()
                if report.has_errors():
                    how = self._revert(abs_path)
                    reason = f"invalid artifact {how} at write boundary: " + "; ".join(f.message for f in report.findings)
                    return HookDecision("deny", reason, "postcondition", report=report, outputs=outputs)
        return HookDecision(phase="postcondition", outputs=outputs)

    def _resolve_write_path(self, ref: str) -> Path | None:
        """Resolve a write ref (host-absolute or framework/portfolio-relative) to a concrete path."""
        candidate = Path(ref)
        if candidate.is_absolute():
            return candidate
        for base in (self.workspace.framework_root, getattr(self.workspace, "portfolio_base", self.workspace.framework_root)):
            resolved = base / ref
            if resolved.exists():
                return resolved.resolve()
        return (self.workspace.framework_root / ref).resolve()

    def _revert(self, path: Path) -> str:
        """Revert an invalid write: restore the last-committed version if the path is git-tracked,
        else delete the new file. Returns 'restored' or 'deleted'."""
        work_dir = str(path.parent)
        try:
            tracked = subprocess.run(
                ["git", "-C", work_dir, "ls-files", "--error-unmatch", str(path)],
                capture_output=True, text=True,
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            tracked = False
        if tracked:
            try:
                subprocess.run(["git", "-C", work_dir, "checkout", "--", str(path)], capture_output=True, text=True)
                return "restored"
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return "deleted"

    def _dispatch(self, payload: dict[str, Any]) -> HookDecision:
        """preToolUse on a subagent dispatch (runSubagent) → govern the (target agent, model) selection
        against the routing map: deny an off-policy model (Auto/omitted, unknown key, or below the
        role-default tier floor) and inject the routing guidance. The host contract is allow/deny +
        additionalContext, so the harness cannot REWRITE the model arg — it validates the orchestrator's
        choice and corrects via denial + injected context (the dispatch starts the child step-session)."""
        agent = self._dispatch_agent(payload)
        model = self._dispatch_model(payload)
        error = self.router.validate_dispatch(agent, model)
        context = self._routing_context(agent)
        if error:
            return HookDecision("deny", f"dispatch {agent or '<agent>'}: {error}", "precondition", context=context)
        return HookDecision(phase="precondition", context=context)

    def _routing_context(self, agent: str) -> str:
        if not agent:
            return ""
        return (f"routing: agent={agent}; role-default tier floor={self.router.role_default(agent)}; "
                "resolve the model from harness/llm/map.yaml (never Auto).")

    # --- session ledger -----------------------------------------------------
    def ledger_path(self, payload: dict[str, Any]) -> Path:
        """The per-session run ledger for this event — the single append-only record the hook funnel
        and `check-step` share, keyed by the host session id, so session-open, every write, each
        recorded step, and session-close all land in one file."""
        return self.workspace.session_ledger(self._session(payload))

    def _active_orchestration(self, actor: str) -> str | None:
        """The root orchestration this actor facilitates (so a session-open marker records which
        workflow the session runs, giving session-close the context to review it), or None."""
        if not actor:
            return None
        for wf in self.workflows.all():
            if wf.is_root and str(wf.facilitator).lstrip("@") == actor.lstrip("@"):
                return str(wf.id)
        return None

    def record(self, event: str, payload: dict[str, Any], decision: HookDecision) -> None:
        """Append the observation to the per-session ledger so the harness captures everything the
        agent/subagents did. Append-only; the CLI's checks read it back as evidence later. A
        session-open line also records the active orchestration so session-close can review the run.
        Written as an enveloped journal entry (command=hook): the envelope carries session/actor/
        status, the payload is the host decision (event/phase/permission/reason/outputs/context)."""
        actor = self._actor(payload)
        hook_payload: dict[str, Any] = {
            "event": event,
            "phase": decision.phase,
            "permission": decision.permission,
            "reason": decision.reason,
            "outputs": decision.outputs,
        }
        if decision.context:
            hook_payload["context"] = decision.context
        orchestration = self._active_orchestration(actor) if decision.phase == "session-open" else None
        self.logs.append_entry(
            self.ledger_path(payload),
            command="hook",
            payload=hook_payload,
            trigger="host",
            session=self._session(payload),
            orchestration=orchestration,
            actor=actor or None,
            status=decision.permission,
        )

    # --- payload extraction (config-driven: hosts vary key names) -----------
    def _first(self, payload: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            value = payload.get(key)
            if value:
                return value
        return None

    def _session(self, payload: dict[str, Any]) -> str:
        value = self._first(payload, self.binding.get("session_keys", ["sessionId"]))
        return str(value).replace("/", "-") if value else "session"

    def _actor(self, payload: dict[str, Any]) -> str:
        value = self._first(payload, self.binding.get("actor_keys", ["actor"]))
        return str(value).strip().lstrip("@") if value else ""

    def _is_dispatch(self, payload: dict[str, Any]) -> bool:
        tool = str(self._first(payload, self.binding.get("tool_keys", ["tool"])) or "")
        return tool in (self.binding.get("dispatch_tools") or [])

    def _dispatch_agent(self, payload: dict[str, Any]) -> str:
        args = self._first(payload, self.binding.get("input_keys", ["tool_input"])) or {}
        if not isinstance(args, dict):
            return ""
        value = self._first(args, self.binding.get("dispatch_agent_keys", ["agentName", "agent"]))
        return self.policy.normalize(str(value)) if value else ""

    def _dispatch_model(self, payload: dict[str, Any]) -> str:
        args = self._first(payload, self.binding.get("input_keys", ["tool_input"])) or {}
        if not isinstance(args, dict):
            return ""
        value = self._first(args, self.binding.get("dispatch_model_keys", ["model"]))
        return str(value).strip() if value else ""

    def _write(self, payload: dict[str, Any]) -> tuple[str, list[str]]:
        tool = str(self._first(payload, self.binding.get("tool_keys", ["tool"])) or "")
        action = self.binding.get("write_tools", {}).get(tool, "delete" if tool in self.binding.get("delete_tools", []) else "")
        if not action:
            return "", []
        args = self._first(payload, self.binding.get("input_keys", ["tool_input"])) or {}
        path = self._first(args, self.binding.get("path_keys", ["path"])) if isinstance(args, dict) else None
        if not path:
            return action, []
        return action, [self._relativize(str(path))]

    def _relativize(self, path: str) -> str:
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                return str(candidate.resolve().relative_to(self.workspace.framework_root))
            except ValueError:
                return path
        return path

