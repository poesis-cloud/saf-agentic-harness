"""The composition root: build the object graph, then dispatch argv to one command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, NoReturn, Sequence

from commands.check_step_artifact_command import CheckStepArtifactCommand
from commands.check_step_authorization_command import CheckStepAuthorizationCommand
from commands.check_step_postconditions_command import CheckStepPostconditionsCommand
from commands.check_step_preconditions_command import CheckStepPreconditionsCommand
from commands.command import Command
from commands.end_session_command import EndSessionCommand
from commands.resolve_step_command import ResolveStepCommand
from commands.resolve_step_instructions_command import ResolveStepInstructionsCommand
from commands.resolve_step_model_command import ResolveStepModelCommand
from commands.resolve_step_skills_command import ResolveStepSkillsCommand
from commands.resolve_workflow_instructions_command import (
    ResolveWorkflowInstructionsCommand,
)
from commands.resolve_workflow_skills_command import ResolveWorkflowSkillsCommand
from commands.start_session_command import StartSessionCommand
from config.access_control_list import AccessControlList
from config.artifact_node import ArtifactNode
from config.config_loader import ConfigLoader
from config.folder_node import FolderNode
from config.framework_layout import FrameworkLayout
from config.workflow_catalog import WorkflowCatalog
from config.workspace_layout import WorkspaceLayout
from errors import ConfigurationError, InquiryError
from services.checking.condition_evaluator import ConditionEvaluator
from services.checking.step_postcondition_checker import StepPostconditionChecker
from services.checking.step_precondition_checker import StepPreconditionChecker
from services.context_resolution.step_instruction_resolver import (
    StepInstructionResolver,
)
from services.context_resolution.step_skill_resolver import StepSkillResolver
from services.context_resolution.workflow_instruction_resolver import (
    WorkflowInstructionResolver,
)
from services.context_resolution.workflow_skill_resolver import WorkflowSkillResolver
from services.model_resolution.step_model_resolver import StepModelResolver
from services.session_lifecycle.session_lifecycle import SessionLifecycle
from services.step_resolution.step_resolver import StepResolver
from stores.artifact_store.artifact_store import ArtifactStore
from stores.session_log_store.session_log_store import SessionLogStore
from utils.env_loader import EnvLoader
from utils.schema_validator import SchemaValidator
from utils.yaml_loader import YamlLoader

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"
_FRAMEWORK_ANCHOR = "FRAMEWORK_DIR"
_ARTIFACT_SCHEMA_SUFFIX = ".artifact.schema.json"
_SKILL_SUFFIX = ".skill.md"
_INSTRUCTION_SUFFIX = ".instructions.md"
_EXIT_OK = 0
_EXIT_INVALID_INQUIRY = 1
_EXIT_USAGE = 2

# The function-specific flags each command's `in` object carries, beside the shared
# session attribution pair: (flag spellings, contract property, set-valued).
_FUNCTION_ARGUMENTS: Mapping[str, tuple[tuple[tuple[str, ...], str, bool], ...]] = (
    MappingProxyType(
        {
            "start-session": ((("--agent",), "agent", False),),
            "resolve-step": ((("--workflow-slug", "--workflow"), "workflowSlug", False),),
            "check-step-authorization": (
                (("--artifact-path",), "artifactPath", False),
                (("--action",), "action", False),
            ),
            "check-step-artifact": ((("--artifact-path",), "artifactPaths", True),),
        }
    )
)


class _InquiryArgumentParser(argparse.ArgumentParser):
    """Turn an unparsable invocation into the exit plane's `invalid-inquiry`.

    Spec (Outcomes rule 4): arguments that never reach an inquiry can produce no
    contract-valid report, so they surface exactly like a contract-validation failure
    rather than as argparse's own process exit.
    """

    def error(self, message: str) -> NoReturn:
        """Raise instead of exiting the process."""
        raise InquiryError("invalid-inquiry", f"{self.prog}: {message}", False)


def _parse_inquiry_arguments(function: str, flags: Sequence[str]) -> dict[str, Any]:
    """Read one invocation's flags into the function's JSON `in` object."""
    parser = _InquiryArgumentParser(prog=f"harness.py {function}", add_help=False)
    parser.add_argument("--session-id", dest="sessionId")
    parser.add_argument("--parent-session-id", dest="parentSessionId")
    for spellings, property_name, set_valued in _FUNCTION_ARGUMENTS.get(function, ()):
        parser.add_argument(
            *spellings,
            dest=property_name,
            action="append" if set_valued else "store",
        )
    parsed = vars(parser.parse_args(list(flags)))
    return {name: value for name, value in parsed.items() if value is not None}


def _iter_artifact_slugs(nodes: Iterable[ArtifactNode | FolderNode]) -> Iterable[str]:
    """Walk the workspace blueprint, answering every artifact slug it binds."""
    for node in nodes:
        if isinstance(node, FolderNode):
            yield from _iter_artifact_slugs(node.children)
        else:
            yield node.artifact


def _require_framework_agent(acl: AccessControlList, actor: str, workflow: str) -> None:
    """Enforce that a workflow's declared agent exists in the access control list."""
    if not acl.is_framework_agent(actor):
        raise ConfigurationError(
            "unknown-actor",
            f"Workflow '{workflow}' names agent '{actor}', which the access control "
            "list never declares.",
            False,
        )


def _require_refs_resolve(
    layout: FrameworkLayout,
    workflow: str,
    skills: Sequence[str],
    instructions: Sequence[str],
) -> None:
    """Enforce that every instruction and skill ref resolves to a framework file."""
    candidates = [
        (layout.skills_dir / f"{slug}{_SKILL_SUFFIX}", slug) for slug in skills
    ]
    candidates += [
        (layout.instructions_dir / f"{slug}{_INSTRUCTION_SUFFIX}", slug)
        for slug in instructions
    ]
    for path, slug in candidates:
        if not path.is_file():
            raise ConfigurationError(
                "unresolved-context-ref",
                f"Workflow '{workflow}' references '{slug}', which resolves to no file "
                f"at '{path}'.",
                False,
            )


def _resolve_artifact_schemas(
    layout: FrameworkLayout,
    workspace_layout: WorkspaceLayout,
    catalog: WorkflowCatalog,
) -> Mapping[str, Path]:
    """Resolve every declared artifact slug to its schema file, fail-fast."""
    slugs = set(_iter_artifact_slugs(workspace_layout.nodes))
    slugs.update(
        step.artifact for workflow in catalog.workflows.values() for step in workflow.steps
    )
    schemas: dict[str, Path] = {}
    for slug in sorted(slugs):
        path = layout.schemas_dir / f"{slug}{_ARTIFACT_SCHEMA_SUFFIX}"
        if not path.is_file():
            raise ConfigurationError(
                "unresolved-artifact-schema",
                f"Artifact slug '{slug}' resolves to no artifact schema at '{path}'.",
                False,
            )
        schemas[slug] = path
    return MappingProxyType(schemas)


def _require_coherent_configuration(
    layout: FrameworkLayout,
    acl: AccessControlList,
    catalog: WorkflowCatalog,
) -> None:
    """Enforce the coherence rules that span several configuration sources.

    Spec (Internal validation): the cross-configuration coherence rules — workflow
    actors exist in the ACL, instruction/skill refs resolve to files in the framework
    layout — are enforced at the fail-fast load, before any function runs. The rule
    that capability tags belong to the model catalog's vocabulary needs no check here:
    steps and profiles reference ONE shared `capabilities` definition, so the config
    boundary's own contract validation already enforces it.
    """
    for workflow in catalog.workflows.values():
        _require_framework_agent(acl, workflow.facilitator, workflow.slug)
        _require_refs_resolve(
            layout, workflow.slug, workflow.skills, workflow.instructions
        )
        for step in workflow.steps:
            _require_framework_agent(acl, step.actor, workflow.slug)
            _require_refs_resolve(
                layout, workflow.slug, step.skills, step.instructions
            )


class Application:
    """Build the harness object graph once, then dispatch one invocation.

    Spec (`application`): the single composition root, above every package — it builds
    every configuration dataclass through `ConfigLoader` (fail-fast) and wires the
    object graph, then dispatches `argv` to one command. Only `ConfigurationError`
    escapes: no function exists yet to report through.
    """

    def __init__(
        self,
        framework_root: str | Path,
        config_loader: ConfigLoader | None = None,
        schema_validator: SchemaValidator | None = None,
    ) -> None:
        """Load every configuration view fail-fast and wire the twelve commands."""
        validator = schema_validator or SchemaValidator.compile_contracts(
            sorted(_CONTRACTS_DIR.rglob("*.schema.json"))
        )
        loader = config_loader or ConfigLoader(EnvLoader(), YamlLoader(), validator)

        layout = loader.load_framework_layout(framework_root)
        acl = loader.load_access_control_list(framework_root)
        profiles = loader.load_model_profiles(framework_root)
        workspace_layout = loader.load_workspace_layout(framework_root)
        catalog = loader.load_workflow_catalog(framework_root)
        _require_coherent_configuration(layout, acl, catalog)
        artifact_schemas = _resolve_artifact_schemas(layout, workspace_layout, catalog)

        session_log_store = SessionLogStore(
            layout.workspace_dir, schema_validator=validator
        )
        artifact_store = ArtifactStore(layout.workspace_dir, artifact_schemas)
        lifecycle = SessionLifecycle(session_log_store, acl)
        evaluator = ConditionEvaluator(artifact_store)

        commands: tuple[Command, ...] = (
            StartSessionCommand(lifecycle, validator),
            EndSessionCommand(lifecycle, validator),
            ResolveWorkflowInstructionsCommand(
                WorkflowInstructionResolver(catalog, session_log_store), validator
            ),
            ResolveWorkflowSkillsCommand(
                WorkflowSkillResolver(catalog, session_log_store), validator
            ),
            ResolveStepCommand(StepResolver(session_log_store, catalog), validator),
            ResolveStepModelCommand(
                StepModelResolver(session_log_store, catalog, profiles), validator
            ),
            CheckStepPreconditionsCommand(
                StepPreconditionChecker(evaluator, session_log_store, catalog), validator
            ),
            ResolveStepInstructionsCommand(
                StepInstructionResolver(catalog, session_log_store), validator
            ),
            ResolveStepSkillsCommand(
                StepSkillResolver(catalog, session_log_store), validator
            ),
            CheckStepPostconditionsCommand(
                StepPostconditionChecker(evaluator, session_log_store, catalog), validator
            ),
        )
        self._commands: Mapping[str, Command] = MappingProxyType(
            {command.FUNCTION: command for command in commands}
        )

    def list_functions(self) -> tuple[str, ...]:
        """Answer the harness functions this application exposes as commands."""
        return tuple(sorted(self._commands))

    def dispatch_command(self, argv: Sequence[str]) -> int:
        """Dispatch one invocation to its command, rendering the report it answers.

        Spec (Outcomes rule 4): an inquiry its own contract rejects produces no report
        — it surfaces here, at the command exit plane, with stderr and a nonzero exit.
        """
        arguments = list(argv)
        if not arguments:
            return _refuse("harness: no function named; expected one of "
                           f"{', '.join(self.list_functions())}", _EXIT_USAGE)
        function, flags = arguments[0], arguments[1:]
        command = self._commands.get(function)
        if command is None:
            return _refuse(
                f"harness: '{function}' is no harness function; expected one of "
                f"{', '.join(self.list_functions())}",
                _EXIT_USAGE,
            )
        try:
            inquiry = command.parse_inquiry(_parse_inquiry_arguments(function, flags))
        except InquiryError as failure:
            return _refuse(f"harness: {failure.code}: {failure.message}", _EXIT_INVALID_INQUIRY)
        report = command.execute_function(inquiry)
        sys.stdout.write(_render_report(report.to_dict()) + "\n")
        return _EXIT_OK


def _render_report(report: Mapping[str, Any]) -> str:
    """Render one report exactly as the journal persists it, byte for byte."""
    return json.dumps(report, ensure_ascii=False, separators=(",", ":"))


def _refuse(message: str, exit_code: int) -> int:
    """Surface a refusal at the command exit plane: stderr, nonzero, no report."""
    print(message, file=sys.stderr)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run one harness command against the framework the environment anchors."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    framework_root = os.environ.get(_FRAMEWORK_ANCHOR)
    if not framework_root:
        return _refuse(
            f"harness: {_FRAMEWORK_ANCHOR} is unset; it anchors the framework layout.",
            _EXIT_USAGE,
        )
    return Application(framework_root).dispatch_command(arguments)


__all__ = ["Application", "main"]
