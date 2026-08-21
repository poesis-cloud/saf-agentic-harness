"""Parse, contract-validate, and semantically check every configuration source."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence

import celpy

from config.access_control_list import AccessControlList
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from config.framework_layout import FrameworkLayout
from config.model_profile import ModelProfile
from config.model_profiles import ModelProfiles
from config.privilege import Privilege
from config.state_condition import StateCondition
from config.step import Step
from config.step_condition import StepCondition
from config.workflow import Workflow
from config.workflow_catalog import WorkflowCatalog
from config.workspace_layout import WorkspaceLayout, paths_can_collide
from errors import ConfigurationError
from utils.env_loader import EnvLoader
from utils.schema_validator import SchemaValidator
from utils.yaml_loader import YamlLoader

_ENV_FILENAME = ".env"
_CONF_DIR_NAME = "conf"
_ACCESS_CONTROL_LIST_FILENAME = "access-control-list.conf.yaml"
_MODEL_PROFILES_FILENAME = "model-profiles.conf.yaml"
_WORKSPACE_FILENAME = "workspace.conf.yaml"
_WORKFLOW_FILENAME_SUFFIX = ".workflow.conf.yaml"

_ACCESS_CONTROL_LIST_CONTRACT = "gsmarc://saf/contracts/conf/framework/access-control-list.conf/v1"
_MODEL_PROFILES_CONTRACT = "gsmarc://saf/contracts/conf/framework/model-profiles.conf/v1"
_WORKSPACE_CONTRACT = "gsmarc://saf/contracts/conf/framework/workspace.conf/v1"
_WORKFLOW_CONTRACT = "gsmarc://saf/contracts/conf/framework/workflow.conf/v1"

_FRAMEWORK_DIR_KEY = "FRAMEWORK_DIR"
_REQUIRED_LAYOUT_KEYS = (
    "FRAMEWORK_AGENTS_DIR",
    "FRAMEWORK_ARTIFACTS_DIR",
    "FRAMEWORK_INSTRUCTIONS_DIR",
    "FRAMEWORK_SKILLS_DIR",
    "FRAMEWORK_TEMPLATES_DIR",
    "FRAMEWORK_WORKFLOWS_DIR",
    "FRAMEWORK_WORKSPACE_DIR",
)
_PRECONDITION_KIND = "precondition"
_ARTIFACT_SCHEMA_SUFFIX = ".artifact.schema.json"
_ARTIFACTS_SLUG = re.compile(r"artifacts\[\s*['\"]([a-z0-9-]+)['\"]\s*\]")
_ARTIFACTS_PROPERTY = re.compile(
    r"artifacts\[\s*['\"]([a-z0-9-]+)['\"]\s*\]\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)"
)
# CEL macros and list members read like a property but bind no artifact field.
_CEL_MEMBERS = frozenset(
    {"all", "exists", "exists_one", "filter", "map", "size"}
)


def _to_plain_data(value: Any) -> Any:
    """Thaw loader data into containers jsonschema accepts.

    `YamlLoader` hands out `MappingProxyType`/`tuple`, and jsonschema type-checks with
    `isinstance(value, dict)` / `isinstance(value, list)` — a frozen mapping is neither,
    so every instance is thawed before validation.
    """
    if isinstance(value, Mapping):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_plain_data(item) for item in value]
    return value


def _normalize_slug_refs(value: Any) -> tuple[str, ...]:
    """Normalize a contract's scalar-or-array slug reference into a tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _has_cycle(edges: Mapping[str, tuple[str, ...]]) -> bool:
    """Tell whether a directed graph given as node -> successors holds a cycle."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for successor in edges.get(node, ()):
            if walk(successor):
                return True
        visiting.discard(node)
        visited.add(node)
        return False

    return any(walk(node) for node in edges)


class ConfigLoader:
    """Build every configuration view fail-fast from its source.

    Spec (Internal validation): `ConfigLoader` parses and contract-validates every source as
    ONE act — an unvalidated parse never escapes — and applies the semantic rules JSON Schema
    cannot express, so no view is constructed from a source that has not passed all of them.
    Cross-configuration coherence (workflow actors exist in the ACL, instruction/skill refs
    resolve to framework files) spans several sources and belongs to the composition root.
    """

    def __init__(
        self,
        env_loader: EnvLoader,
        yaml_loader: YamlLoader,
        schema_validator: SchemaValidator,
    ) -> None:
        """Create the loader over its injected format loaders and contract validator."""
        self._env_loader = env_loader
        self._yaml_loader = yaml_loader
        self._schema_validator = schema_validator

    def load_framework_layout(self, framework_root: str | Path) -> FrameworkLayout:
        """Load the layout environment: every variable present, every path existing."""
        env_path = Path(framework_root) / _ENV_FILENAME
        declared_in_file: Mapping[str, str] = (
            self._env_loader.load_environment(env_path) if env_path.is_file() else {}
        )
        declared = {
            key: value
            for key in (_FRAMEWORK_DIR_KEY, *_REQUIRED_LAYOUT_KEYS)
            if (value := os.environ.get(key, declared_in_file.get(key)))
        }

        missing = tuple(sorted(key for key in _REQUIRED_LAYOUT_KEYS if key not in declared))
        if missing:
            raise ConfigurationError(
                "missing-layout-variable",
                f"Framework layout environment declares no {', '.join(missing)}.",
                False,
            )

        framework_dir = Path(declared.get(_FRAMEWORK_DIR_KEY, env_path.parent)).resolve()
        resolved = {
            key: (framework_dir / declared[key]).resolve() for key in _REQUIRED_LAYOUT_KEYS
        }
        absent = tuple(sorted(key for key, path in resolved.items() if not path.is_dir()))
        if absent:
            raise ConfigurationError(
                "missing-layout-path",
                f"Framework layout points at directories that do not exist: {', '.join(absent)}.",
                False,
            )

        return FrameworkLayout(
            framework_dir=framework_dir,
            agents_dir=resolved["FRAMEWORK_AGENTS_DIR"],
            artifacts_dir=resolved["FRAMEWORK_ARTIFACTS_DIR"],
            schemas_dir=resolved["FRAMEWORK_ARTIFACTS_DIR"],
            skills_dir=resolved["FRAMEWORK_SKILLS_DIR"],
            templates_dir=resolved["FRAMEWORK_TEMPLATES_DIR"],
            workflows_dir=resolved["FRAMEWORK_WORKFLOWS_DIR"],
            instructions_dir=resolved["FRAMEWORK_INSTRUCTIONS_DIR"],
            workspace_dir=resolved["FRAMEWORK_WORKSPACE_DIR"],
        )

    def load_access_control_list(self, framework_root: str | Path) -> AccessControlList:
        """Load the ACL, resolving every actor's roles into one privilege set."""
        conf_dir = self._resolve_conf_dir(framework_root)
        data = self._load_source(
            conf_dir / _ACCESS_CONTROL_LIST_FILENAME, _ACCESS_CONTROL_LIST_CONTRACT
        )

        privileges_by_role = {
            role["slug"]: frozenset(
                Privilege(artifact=privilege["artifact"], action=privilege["action"])
                for privilege in role["privileges"]
            )
            for role in data["roles"]
        }

        grants: dict[str, frozenset[Privilege]] = {}
        for actor in data["actors"]:
            unknown = tuple(sorted(set(actor["roles"]) - set(privileges_by_role)))
            if unknown:
                raise ConfigurationError(
                    "unknown-acl-role",
                    f"ACL actor '{actor['slug']}' references roles no role declares: "
                    f"{', '.join(unknown)}.",
                    False,
                )
            granted: frozenset[Privilege] = frozenset()
            for role_slug in actor["roles"]:
                granted |= privileges_by_role[role_slug]
            grants[actor["slug"]] = granted

        return AccessControlList(grants=MappingProxyType(grants))

    def load_model_profiles(self, framework_root: str | Path) -> ModelProfiles:
        """Load the model catalog, rejecting duplicate profile slugs."""
        conf_dir = self._resolve_conf_dir(framework_root)
        data = self._load_source(conf_dir / _MODEL_PROFILES_FILENAME, _MODEL_PROFILES_CONTRACT)

        seen: set[str] = set()
        for profile in data["modelProfiles"]:
            slug = profile["slug"]
            if slug in seen:
                raise ConfigurationError(
                    "duplicate-model-profile-slug",
                    f"Model catalog declares a duplicate model profile slug '{slug}'.",
                    False,
                )
            seen.add(slug)

        profiles = {
            profile["slug"]: ModelProfile(
                slug=profile["slug"],
                cost_rank=profile["costRank"],
                capabilities=MappingProxyType(
                    {tag: float(score) for tag, score in profile["capabilities"].items()}
                ),
                description=profile.get("description"),
                note=profile.get("note"),
            )
            for profile in data["modelProfiles"]
        }
        return ModelProfiles(profiles=MappingProxyType(profiles))

    def load_workspace_layout(self, framework_root: str | Path) -> WorkspaceLayout:
        """Load the workspace blueprint as a tree of artifact and folder nodes."""
        conf_dir = self._resolve_conf_dir(framework_root)
        data = self._load_source(conf_dir / _WORKSPACE_FILENAME, _WORKSPACE_CONTRACT)
        nodes = tuple(self._build_node(node) for node in data["nodes"])
        self._require_unambiguous_artifact_paths(nodes)
        return WorkspaceLayout(nodes=nodes)

    def load_workflow_catalog(self, framework_root: str | Path) -> WorkflowCatalog:
        """Load every workflow configuration and validate the catalog's advisory graph."""
        layout = self.load_framework_layout(framework_root)
        paths = sorted(layout.workflows_dir.glob(f"*{_WORKFLOW_FILENAME_SUFFIX}"))
        if not paths:
            raise ConfigurationError(
                "empty-workflow-catalog",
                f"Workflow directory '{layout.workflows_dir}' holds no workflow "
                f"configuration file.",
                False,
            )

        artifact_schemas = self._index_artifact_schemas(layout.artifacts_dir)
        sources: dict[str, Mapping[str, Any]] = {}
        for path in paths:
            data = self._load_source(path, _WORKFLOW_CONTRACT)
            self._require_workflow_rules(
                data, path.name[: -len(_WORKFLOW_FILENAME_SUFFIX)], artifact_schemas
            )
            sources[data["slug"]] = data
        self._require_catalog_rules(sources)
        self._require_orchestrators_are_framework_agents(sources, framework_root)

        workflows = {slug: self._build_workflow(data) for slug, data in sources.items()}
        return WorkflowCatalog(workflows=MappingProxyType(workflows))

    def _resolve_conf_dir(self, framework_root: str | Path) -> Path:
        """Anchor the `conf/` sources on the validated layout's `FRAMEWORK_DIR`."""
        return self.load_framework_layout(framework_root).framework_dir / _CONF_DIR_NAME

    def _load_source(self, path: Path, contract_id: str) -> Mapping[str, Any]:
        """Parse one YAML source and validate it against its contract as one act."""
        if not path.is_file():
            raise ConfigurationError(
                "missing-configuration-source",
                f"Configuration source '{path}' does not exist.",
                False,
            )

        data = _to_plain_data(self._yaml_loader.load_yaml(path))
        errors = self._schema_validator.validate_instance(contract_id, data)
        if errors:
            reports = "; ".join(f"{error.path or '/'}: {error.message}" for error in errors)
            raise ConfigurationError(
                "invalid-configuration-source",
                f"Contract schema validation failed for '{path}': {reports}",
                False,
            )
        return data

    def _require_workflow_rules(
        self,
        data: Mapping[str, Any],
        filename_slug: str,
        artifact_schemas: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Apply one workflow file's semantic rules, in dependency order."""
        slug = data["slug"]
        if slug != filename_slug:
            raise ConfigurationError(
                "workflow-slug-filename-mismatch",
                f"Workflow '{slug}' contradicts its configuration filename stem "
                f"'{filename_slug}'.",
                False,
            )

        steps = data["steps"]
        self._require_unique_step_slugs(slug, steps)
        self._require_unique_condition_slugs(slug, steps)
        self._require_resolvable_step_references(slug, steps)
        self._require_acyclic_step_graph(slug, steps)
        self._require_positive_capability_weight(slug, steps)
        self._require_static_condition_expressions(slug, steps, artifact_schemas)

    def _require_unique_step_slugs(self, slug: str, steps: Sequence[Mapping[str, Any]]) -> None:
        """Spec: step slugs are unique within a workflow."""
        seen: set[str] = set()
        for step in steps:
            if step["slug"] in seen:
                raise ConfigurationError(
                    "duplicate-step-slug",
                    f"Workflow '{slug}' declares a duplicate step slug '{step['slug']}'.",
                    False,
                )
            seen.add(step["slug"])

    def _require_unique_condition_slugs(
        self, slug: str, steps: Sequence[Mapping[str, Any]]
    ) -> None:
        """Spec (function 6, invariant 3): condition slugs are unique within a step."""
        for step in steps:
            seen: set[str] = set()
            for condition in step.get("conditions", ()):
                if condition["slug"] in seen:
                    raise ConfigurationError(
                        "duplicate-condition-slug",
                        f"Workflow '{slug}' step '{step['slug']}' declares a duplicate "
                        f"condition slug '{condition['slug']}'.",
                        False,
                    )
                seen.add(condition["slug"])

    def _require_resolvable_step_references(
        self, slug: str, steps: Sequence[Mapping[str, Any]]
    ) -> None:
        """Spec: every `stepCondition.step` reference resolves within the workflow."""
        known = {step["slug"] for step in steps}
        for step in steps:
            for condition in step.get("conditions", ()):
                referenced = condition.get("step")
                if referenced is not None and referenced not in known:
                    raise ConfigurationError(
                        "unresolvable-step-reference",
                        f"Workflow '{slug}' step '{step['slug']}' condition "
                        f"'{condition['slug']}' references the unknown step '{referenced}'.",
                        False,
                    )

    def _require_acyclic_step_graph(self, slug: str, steps: Sequence[Mapping[str, Any]]) -> None:
        """Spec: the step DAG is acyclic.

        Only `kind: precondition` contributes an edge: a postcondition's `step` is the
        advisory mirror of the successor's own precondition, and the harness enforces the
        DAG edge once, from the successor's side.
        """
        edges = {
            step["slug"]: tuple(
                condition["step"]
                for condition in step.get("conditions", ())
                if condition.get("step") is not None
                and condition["kind"] == _PRECONDITION_KIND
            )
            for step in steps
        }
        if _has_cycle(edges):
            raise ConfigurationError(
                "cyclic-step-graph",
                f"Workflow '{slug}' declares a cyclic step graph across "
                f"{', '.join(sorted(edges))}.",
                False,
            )

    def _require_positive_capability_weight(
        self, slug: str, steps: Sequence[Mapping[str, Any]]
    ) -> None:
        """Spec: every step declares at least one positive capability weight."""
        for step in steps:
            if not any(weight > 0 for weight in step["capabilities"].values()):
                raise ConfigurationError(
                    "missing-capability-weight",
                    f"Workflow '{slug}' step '{step['slug']}' declares no positive "
                    f"capability weight.",
                    False,
                )

    def _index_artifact_schemas(self, artifacts_dir: Path) -> Mapping[str, Mapping[str, Any]]:
        """Index the framework's artifact schemas by the slug a condition may name."""
        schemas: dict[str, Mapping[str, Any]] = {}
        for path in sorted(artifacts_dir.glob(f"*{_ARTIFACT_SCHEMA_SUFFIX}")):
            try:
                schemas[path.name[: -len(_ARTIFACT_SCHEMA_SUFFIX)]] = json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ConfigurationError(
                    "unreadable-artifact-schema",
                    f"Artifact schema '{path}' could not be read: {error}",
                    False,
                ) from error
        return schemas

    def _require_static_condition_expressions(
        self,
        slug: str,
        steps: Sequence[Mapping[str, Any]],
        artifact_schemas: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Spec (function 5, invariant 2): statically invalid expressions never reach runtime.

        Static reach is the direct `artifacts['<slug>'].<property>` form: the expression
        must compile, its artifact slugs must resolve to a declared schema, and a property
        read straight off the slug must be declared there. A property reached through a
        macro-bound variable is left to runtime — resolving it needs a scope-tracking AST
        walk, and guessing would reject valid configurations.
        """
        environment = celpy.Environment()
        for step in steps:
            for condition in step.get("conditions", ()):
                selector = condition.get("setSelector")
                if selector is None:
                    continue
                for expression in (selector["setQuery"], condition["setPredicate"]):
                    self._require_compilable_expression(
                        environment, slug, step["slug"], condition["slug"], expression
                    )
                    self._require_declared_artifact_references(
                        slug, step["slug"], condition["slug"], expression, artifact_schemas
                    )

    def _require_compilable_expression(
        self,
        environment: celpy.Environment,
        slug: str,
        step_slug: str,
        condition_slug: str,
        expression: str,
    ) -> None:
        """Compile one CEL expression, so no unparseable expression is ever evaluated."""
        try:
            environment.program(environment.compile(expression))
        except celpy.CELParseError as error:
            raise ConfigurationError(
                "uncompilable-condition-expression",
                f"Workflow '{slug}' step '{step_slug}' condition '{condition_slug}' "
                f"declares an expression that does not compile: '{expression}' ({error}).",
                False,
            ) from error

    def _require_declared_artifact_references(
        self,
        slug: str,
        step_slug: str,
        condition_slug: str,
        expression: str,
        artifact_schemas: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Resolve every artifact slug the expression names, then its direct properties."""
        for artifact_slug in sorted(set(_ARTIFACTS_SLUG.findall(expression))):
            if artifact_slug not in artifact_schemas:
                raise ConfigurationError(
                    "unresolvable-artifact-slug",
                    f"Workflow '{slug}' step '{step_slug}' condition '{condition_slug}' "
                    f"references the artifact slug '{artifact_slug}', which the framework "
                    f"declares no schema for.",
                    False,
                )

        for artifact_slug, member in _ARTIFACTS_PROPERTY.findall(expression):
            if member in _CEL_MEMBERS:
                continue
            declared = artifact_schemas[artifact_slug].get("properties", {})
            if member not in declared:
                raise ConfigurationError(
                    "undeclared-artifact-property",
                    f"Workflow '{slug}' step '{step_slug}' condition '{condition_slug}' "
                    f"reads the property '{member}' off artifact '{artifact_slug}', which "
                    f"its schema does not declare.",
                    False,
                )

    def _require_orchestrators_are_framework_agents(
        self,
        sources: Mapping[str, Mapping[str, Any]],
        framework_root: str | Path,
    ) -> None:
        """Spec (function 1, precondition C): the session's agent is a framework orchestrator.

        Function 0's registration gate admits the framework agents the ACL declares. An
        orchestrator the ACL never declares can never open a session, so its workflows are
        unreachable and function 1 would hand back an empty instruction set. The
        complementary half — an orchestrator facilitating zero workflows — cannot arise:
        the contract makes `orchestrator` required and singular per workflow file.
        """
        access_control_list = self.load_access_control_list(framework_root)
        for slug, data in sources.items():
            orchestrator = data["orchestrator"]
            if not access_control_list.is_framework_agent(orchestrator):
                raise ConfigurationError(
                    "unknown-orchestrator-agent",
                    f"Workflow '{slug}' declares the orchestrator '{orchestrator}', which "
                    f"the access control list declares no framework agent for.",
                    False,
                )

    def _require_catalog_rules(self, sources: Mapping[str, Mapping[str, Any]]) -> None:
        """Spec: the advisory workflow graph resolves and is acyclic."""
        edges: dict[str, tuple[str, ...]] = {}
        for slug, data in sources.items():
            predecessors = _normalize_slug_refs(data.get("predecessors"))
            unknown = tuple(sorted(set(predecessors) - set(sources)))
            if unknown:
                raise ConfigurationError(
                    "unknown-workflow-predecessor",
                    f"Workflow '{slug}' declares an unknown predecessor: {', '.join(unknown)}.",
                    False,
                )
            edges[slug] = predecessors

        if _has_cycle(edges):
            raise ConfigurationError(
                "cyclic-workflow-graph",
                f"Workflow catalog declares a cyclic workflow graph across "
                f"{', '.join(sorted(edges))}.",
                False,
            )

    def _require_unambiguous_artifact_paths(
        self, nodes: Sequence[ArtifactNode | FolderNode]
    ) -> None:
        """Spec: no workspace path may resolve to two artifact kinds.

        Decidable statically: a slug's language is literals plus `[^/]+` placeholders and
        `/` occurs in neither, so two root-to-leaf paths collide exactly when they hold the
        same number of segments and every segment pair accepts a common string.
        """
        leaves = tuple(self._walk_artifact_paths(nodes, ()))
        for index, (path, artifact) in enumerate(leaves):
            for other_path, other_artifact in leaves[index + 1 :]:
                if artifact == other_artifact or not paths_can_collide(path, other_path):
                    continue
                raise ConfigurationError(
                    "ambiguous-workspace-path",
                    f"Workspace layout resolves one path to both '{artifact}' "
                    f"('{'/'.join(path)}') and '{other_artifact}' "
                    f"('{'/'.join(other_path)}').",
                    False,
                )

    def _walk_artifact_paths(
        self,
        nodes: Sequence[ArtifactNode | FolderNode],
        prefix: tuple[str, ...],
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        """Yield every root-to-leaf slug path with the artifact kind it binds."""
        for node in nodes:
            path = (*prefix, node.slug)
            if isinstance(node, FolderNode):
                yield from self._walk_artifact_paths(node.children, path)
            else:
                yield path, node.artifact

    def _build_node(self, data: Mapping[str, Any]) -> ArtifactNode | FolderNode:
        """Build one workspace node: a container of children, or an artifact binding."""
        if "children" in data:
            return FolderNode(
                slug=data["slug"],
                description=data["description"],
                children=tuple(self._build_node(child) for child in data["children"]),
                cardinality=data.get("cardinality"),
            )
        return ArtifactNode(
            slug=data["slug"],
            description=data["description"],
            cardinality=data["cardinality"],
            artifact=data["artifact"],
            template=data["template"],
        )

    def _build_workflow(self, data: Mapping[str, Any]) -> Workflow:
        """Build one workflow view, mapping the schema's names to the prescribed ones."""
        return Workflow(
            slug=data["slug"],
            facilitator=data["orchestrator"],
            steps=tuple(self._build_step(step) for step in data["steps"]),
            after=_normalize_slug_refs(data.get("predecessors")),
            skills=_normalize_slug_refs(data.get("skills")),
            instructions=_normalize_slug_refs(data.get("instructions")),
        )

    def _build_step(self, data: Mapping[str, Any]) -> Step:
        """Build one step view with read-only capability weights."""
        return Step(
            slug=data["slug"],
            actor=data["actor"],
            artifact=data["artifact"],
            instructions=_normalize_slug_refs(data["instructions"]),
            capabilities=MappingProxyType(
                {tag: float(weight) for tag, weight in data["capabilities"].items()}
            ),
            skills=_normalize_slug_refs(data.get("skills")),
            conditions=tuple(
                self._build_condition(condition) for condition in data.get("conditions", ())
            ),
        )

    def _build_condition(self, data: Mapping[str, Any]) -> StepCondition | StateCondition:
        """Build one condition: a step binding, or an artifact-backed state assertion."""
        if "step" in data:
            return StepCondition(kind=data["kind"], slug=data["slug"], step=data["step"])
        return StateCondition(
            kind=data["kind"],
            slug=data["slug"],
            set_selector=MappingProxyType(dict(data["setSelector"])),
            set_predicate=data["setPredicate"],
        )


__all__ = ["ConfigLoader"]
