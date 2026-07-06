"""Services layer — the domain logic (the checkers + the CEL evaluator + routing).

Each service operates on the workspace entities via the mappers and on the validated
configuration via the config views: `SchemaChecker`, `ArtifactChecker` (state plane),
`StepChecker` (check-step), `AuthorizationChecker` (authority plane), `CelEvaluator` (the only
check language), `ModelRouter` (dispatch routing), `OrchestrationService` (the drive),
`HookService` (the host funnel). Services depend on models + mappers + config — never on the
CLI; the CLI wires them together.
"""

from __future__ import annotations

from .artifact_checker import ArtifactChecker
from .authorization_checker import AuthorizationChecker
from .cel_evaluator import CelEvaluator
from .hook_service import HookDecision, HookService
from .model_router import ModelRouter
from .orchestration_service import OrchestrationService
from .schema_checker import SchemaChecker
from .step_checker import StepChecker

__all__ = [
    "ArtifactChecker",
    "AuthorizationChecker",
    "CelEvaluator",
    "HookDecision",
    "HookService",
    "ModelRouter",
    "OrchestrationService",
    "SchemaChecker",
    "StepChecker",
]
