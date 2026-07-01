"""Services layer — the domain logic (the checkers + the CEL evaluator + policy).

Each service operates on the model entities via the repositories: `WorkflowChecker`
(workflow constitution, pytest), `SchemaChecker`, `ArtifactChecker` (state plane),
`StepChecker` (check-step), `CalculationService` (wsjf/cost/transition),
`CelEvaluator` (the only check language), `TransitionPolicy` (the kanban policy). Services
depend on models + persistence — never on the CLI; the CLI wires them together.
"""

from __future__ import annotations

from .artifact_checker import ArtifactChecker
from .authorization_checker import AuthorizationChecker
from .authorization_policy import AuthorizationPolicy
from .calculation_service import CalculationService
from .cel_evaluator import CelEvaluator
from .hook_service import HookDecision, HookService
from .model_router import ModelRouter
from .orchestration_service import OrchestrationService
from .schema_checker import SchemaChecker
from .step_checker import StepChecker
from .transition_policy import TransitionPolicy
from .workflow_checker import WorkflowChecker

__all__ = [
    "ArtifactChecker",
    "AuthorizationChecker",
    "AuthorizationPolicy",
    "CalculationService",
    "CelEvaluator",
    "HookDecision",
    "HookService",
    "ModelRouter",
    "OrchestrationService",
    "SchemaChecker",
    "StepChecker",
    "TransitionPolicy",
    "WorkflowChecker",
]
