"""Unit tests for workflow catalog configuration views."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from errors import ConfigurationError
from tests.unit.config.conftest import capabilities, workflow_yaml, write_yaml


class TestStepCondition:
    """Verify step-binding condition typed views."""

    def test_is_frozen_step_reference(self) -> None:
        """Spec: step conditions bind another step by slug."""
        from config import StepCondition

        condition = StepCondition(kind="precondition", slug="draft-complete", step="draft")

        assert condition.step == "draft"
        with pytest.raises(FrozenInstanceError):
            condition.step = "review"  # type: ignore[misc]


class TestStateCondition:
    """Verify state-binding condition typed views."""

    def test_is_frozen_state_assertion(self) -> None:
        """Spec: state conditions bind setSelector and setPredicate."""
        from config import StateCondition

        condition = StateCondition(kind="postcondition", slug="state", set_selector={"setQuery": "selected"}, set_predicate="true")

        assert condition.set_selector["setQuery"] == "selected"
        with pytest.raises(FrozenInstanceError):
            condition.set_predicate = "false"  # type: ignore[misc]


class TestStep:
    """Verify workflow step typed views."""

    def test_exposes_tuple_refs_and_read_only_capabilities(self, config_loader, framework_root) -> None:
        """Spec: each step declares actor, artifact, refs, conditions, and capabilities."""
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml())

        step = config_loader.load_workflow_catalog(framework_root).find_workflow("planning").steps[0]

        assert step.skills == ("drafting",)
        assert step.instructions == ("draft-instructions",)
        with pytest.raises(TypeError):
            step.capabilities["coding"] = 9  # type: ignore[index]


class TestWorkflow:
    """Verify one workflow typed view."""

    def test_maps_schema_names_to_prescribed_class_attributes(self, config_loader, framework_root) -> None:
        """Spec: Workflow exposes facilitator and after attributes from schema source."""
        write_yaml(framework_root / "conf" / "workflows" / "setup.workflow.conf.yaml", workflow_yaml(slug="setup"))
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml(predecessor="setup"))

        workflow = config_loader.load_workflow_catalog(framework_root).find_workflow("planning")

        assert workflow.facilitator == "facilitator"
        assert workflow.after == ("setup",)
        assert workflow.instructions == ("run-workflow",)
        assert workflow.description is None
        assert workflow.steps[0].description is None
        with pytest.raises(FrozenInstanceError):
            workflow.facilitator = "other"  # type: ignore[misc]

    def test_maps_optional_description_on_workflow_and_step(self, config_loader, framework_root) -> None:
        """Spec: optional description is loaded on the workflow and on each step."""
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml(
                description="Tactical PI commit: challenge objectives before the RTE records them.",
                step_description="Main artifact draft: the business Epic hypothesis.",
            ),
        )

        workflow = config_loader.load_workflow_catalog(framework_root).find_workflow("planning")

        assert workflow.description == (
            "Tactical PI commit: challenge objectives before the RTE records them."
        )
        assert workflow.steps[0].description == (
            "Main artifact draft: the business Epic hypothesis."
        )
        assert workflow.steps[1].description is None


class TestWorkflowCatalog:
    """Verify catalog loading and semantic workflow rules."""

    def test_lists_workflows_facilitated_by_actor(self, config_loader, framework_root) -> None:
        """Spec: catalog can find workflows by slug and facilitator."""
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml())

        catalog = config_loader.load_workflow_catalog(framework_root)

        assert catalog.find_workflow("planning").slug == "planning"
        assert tuple(workflow.slug for workflow in catalog.list_facilitated_workflows("facilitator")) == ("planning",)
        with pytest.raises(ConfigurationError, match="unknown workflow"):
            catalog.find_workflow("missing")

    def test_loads_a_workflow_nested_under_a_layer_folder(
        self, config_loader, framework_root
    ) -> None:
        """The catalog discovers workflow files recursively under FRAMEWORK_WORKFLOWS_DIR."""
        write_yaml(
            framework_root / "conf" / "workflows" / "program" / "planning.workflow.conf.yaml",
            workflow_yaml(),
        )

        catalog = config_loader.load_workflow_catalog(framework_root)

        assert catalog.find_workflow("planning").slug == "planning"

    def test_rejects_empty_workflow_directory(self, config_loader, framework_root) -> None:
        """Spec: workflow catalog source fails fast when no workflow files exist."""
        with pytest.raises(ConfigurationError, match="no workflow"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_filename_slug_mismatch(self, config_loader, framework_root) -> None:
        """Spec: workflowSlug is the workflow configuration filename stem."""
        write_yaml(framework_root / "conf" / "workflows" / "wrong.workflow.conf.yaml", workflow_yaml(slug="planning"))

        with pytest.raises(ConfigurationError, match="filename"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_duplicate_workflow_slugs_in_nested_folders(
        self, config_loader, framework_root
    ) -> None:
        """A slug may appear once in the catalog, even across layer folders."""
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml(),
        )
        write_yaml(
            framework_root / "conf" / "workflows" / "program" / "planning.workflow.conf.yaml",
            workflow_yaml(),
        )

        with pytest.raises(ConfigurationError, match="declared more than once"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_duplicate_step_slugs(self, config_loader, framework_root) -> None:
        """Spec: step slugs are unique within a workflow."""
        workflow = workflow_yaml().replace("  - slug: review", "  - slug: draft")
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow)

        with pytest.raises(ConfigurationError, match="duplicate step slug"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_unresolvable_step_condition_references(self, config_loader, framework_root) -> None:
        """Spec: every stepCondition.step reference resolves within the workflow."""
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml(second_step_condition="missing"))

        with pytest.raises(ConfigurationError, match="missing"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_duplicate_condition_slugs_within_step(self, config_loader, framework_root) -> None:
        """Spec: condition slugs are unique within each step."""
        workflow = workflow_yaml().replace("slug: state-ready", "slug: draft-complete")
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow)

        with pytest.raises(ConfigurationError, match="duplicate condition slug"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_cyclic_step_dag(self, config_loader, framework_root) -> None:
        """Spec: workflow step graph is acyclic."""
        workflow = workflow_yaml() + """  - slug: final
    actor: closer
    artifact: epic
    instructions: close
    capabilities:
""" + "\n".join(f"      {key}: {value}" for key, value in capabilities().items()) + """
    conditions:
      - kind: precondition
        slug: review-complete
        step: review
"""
        workflow = workflow.replace("step: draft\n", "step: final\n", 1)
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow)

        with pytest.raises(ConfigurationError, match="cyclic step"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_step_without_positive_capability_weight(self, config_loader, framework_root) -> None:
        """Spec: every step has at least one positive capability weight."""
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml(positive_capabilities=False))

        with pytest.raises(ConfigurationError, match="positive capability"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_unknown_workflow_predecessor_reference(self, config_loader, framework_root) -> None:
        """Spec: advisory workflow predecessor references resolve."""
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml(predecessor="missing"))

        with pytest.raises(ConfigurationError, match="unknown predecessor"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_cyclic_advisory_workflow_graph(self, config_loader, framework_root) -> None:
        """Spec: advisory workflow graph is acyclic."""
        write_yaml(framework_root / "conf" / "workflows" / "alpha.workflow.conf.yaml", workflow_yaml(slug="alpha", predecessor="beta"))
        write_yaml(framework_root / "conf" / "workflows" / "beta.workflow.conf.yaml", workflow_yaml(slug="beta", predecessor="alpha"))

        with pytest.raises(ConfigurationError, match="cyclic workflow"):
            config_loader.load_workflow_catalog(framework_root)


class TestOrchestratorFacilitation:
    """Verify function 1's (C) precondition is established at configuration load."""

    def test_rejects_an_orchestrator_that_is_not_a_framework_agent(self, config_loader, framework_root) -> None:
        """Spec (function 1, precondition C): the session's agent is a framework orchestrator.

        Function 0's registration gate admits framework agents — the ACL actors. An
        orchestrator the ACL never declares can therefore never open a session, so the
        workflows it drives are unreachable and the agents that do open sessions
        facilitate zero workflows. The load rejects the catalog rather than let function 1
        hand back an empty instruction set.
        """
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml(orchestrator="ghost"),
        )

        with pytest.raises(ConfigurationError, match="ghost"):
            config_loader.load_workflow_catalog(framework_root)

    def test_every_declared_orchestrator_facilitates_at_least_one_workflow(self, config_loader, framework_root) -> None:
        """Spec (function 1, precondition C): an orchestrator facilitating zero workflows is rejected at load.

        The complementary half of the clause holds by construction: the workflow contract
        makes `orchestrator` required and singular, and the filename-stem rule keeps
        workflow slugs unique, so every orchestrator the catalog knows facilitates at
        least one workflow. This pins that construction — were `orchestrator` ever
        relaxed to optional or plural, or a workflow dropped while building the catalog,
        the facilitation index would go empty for some orchestrator and this fails.
        """
        write_yaml(framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow_yaml())
        write_yaml(framework_root / "conf" / "workflows" / "review.workflow.conf.yaml", workflow_yaml(slug="review"))

        catalog = config_loader.load_workflow_catalog(framework_root)

        orchestrators = {workflow.facilitator for workflow in catalog.workflows.values()}
        assert orchestrators
        for orchestrator in orchestrators:
            assert catalog.list_facilitated_workflows(orchestrator)


class TestConditionExpressionStaticValidation:
    """Verify statically invalid condition expressions cannot reach runtime."""

    def test_rejects_a_condition_expression_that_does_not_compile(self, config_loader, framework_root) -> None:
        """Spec (function 5, invariant 2): statically invalid expressions cannot reach runtime.

        The runtime evaluator only maps `CELEvalError` to `condition-evaluation-failed`;
        an expression that does not parse raises through it uncaught. The load compiles
        every `setQuery` and `setPredicate` so no such expression is ever reachable.
        """
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml(set_predicate="size(selected) >"),
        )

        with pytest.raises(ConfigurationError, match="does not compile"):
            config_loader.load_workflow_catalog(framework_root)

    def _load_with(self, config_loader, framework_root, **expressions):
        """Load the planning workflow whose `review` precondition uses these expressions."""
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml(**expressions),
        )
        return config_loader.load_workflow_catalog(framework_root)

    @staticmethod
    def _state_condition(catalog):
        """Read the `review` step's `state-ready` condition back from the catalog."""
        review = catalog.find_workflow("planning").steps[1]
        return next(condition for condition in review.conditions if condition.slug == "state-ready")

    @pytest.mark.parametrize(
        ("set_query", "set_predicate", "code"),
        [
            pytest.param(
                "workspace.filter(a, a.data.state == 'x')",
                "size(selected) > 0",
                "unguarded-artifact-data",
                id="unguarded-data-read",
            ),
            pytest.param(
                "workspace",
                "selected.all(a, a.data.state == 'x')",
                "unguarded-artifact-data",
                id="unguarded-selected-read",
            ),
            pytest.param(
                "workspace.filter(a, a.kind == 'ghost')",
                "size(selected) > 0",
                "unknown-artifact-kind",
                id="unknown-kind",
            ),
            pytest.param(
                "workspace.filter(a, a.kind == 'epic' && a.data.ghost == 'x')",
                "size(selected) > 0",
                "undeclared-artifact-property",
                id="undeclared-property",
            ),
            pytest.param(
                "steps.filter(s, s.slug == 'ghost')",
                "size(selected) > 0",
                "unknown-condition-step",
                id="unknown-step",
            ),
            pytest.param(
                "workflows.filter(w, w.slug == 'ghost')",
                "size(selected) > 0",
                "unknown-condition-workflow",
                id="unknown-workflow",
            ),
            pytest.param(
                "steps.filter(s, s.workflow == workflow.id && s.slug == 'review')",
                "size(selected) > 0",
                "precondition-step-not-predecessor",
                id="not-a-predecessor",
            ),
            pytest.param(
                "workspace.filter(a, a.path == step.artifact.path)",
                "size(selected) > 0",
                "precondition-step-path-join",
                id="path-join-with-step",
            ),
            pytest.param(
                "artifacts['epic']",
                "size(selected) > 0",
                "unknown-condition-variable",
                id="retired-artifacts-map",
            ),
            pytest.param(
                "workspace.filter(a, a.kind == artifact)",
                "size(selected) > 0",
                "unknown-condition-variable",
                id="retired-artifact-constant",
            ),
        ],
    )
    def test_rejects_an_ill_typed_condition_at_load(
        self, config_loader, framework_root, set_query, set_predicate, code
    ) -> None:
        """Spec (function 5, invariant 2, Typing): each of these is a configuration load
        error, never a false pass — an unguarded `a.data` read, an unknown kind, an
        undeclared property of the guarded kind, an unknown step or workflow slug, and,
        in a precondition, a current-workflow step that is not a declared predecessor or
        a path join with `step`. The retired `artifacts` map and `artifact` constant are
        no variables of the condition language."""
        with pytest.raises(ConfigurationError) as refused:
            self._load_with(
                config_loader, framework_root, set_query=set_query, set_predicate=set_predicate
            )

        assert refused.value.code == code
        assert "state-ready" in refused.value.message

    @pytest.mark.parametrize(
        ("set_query", "set_predicate", "kinds"),
        [
            pytest.param(
                "workspace.filter(a, a.kind == 'epic' && a.data.state == 'approved')",
                "size(selected) > 0",
                frozenset({"epic"}),
                id="kind-guard",
            ),
            pytest.param(
                "workspace.filter(a, steps.exists(s, s.workflow == workflow.id"
                " && s.slug == 'draft' && s.outcome == 'pass' && s.artifact.path == a.path))",
                "selected.all(a, a.data.state == 'approved')",
                frozenset({"epic"}),
                id="path-join-with-a-declared-predecessor",
            ),
            pytest.param(
                "workspace.filter(a, workflows.exists(w, w.slug == 'planning'"
                " && steps.exists(s, s.workflow == w.id && s.slug == 'draft'"
                " && s.artifact.path == a.path)))",
                "selected.exists(a, a.data.state == 'approved')",
                frozenset({"epic"}),
                id="path-join-through-a-literal-workflow",
            ),
            pytest.param(
                "workspace.filter(a, steps.exists(s, s.slug == 'draft'"
                " && s.artifact.path == a.path && workflows.exists(w, w.slug == 'planning'"
                " && s.workflow == w.id)))",
                "selected.exists(a, a.data.state == 'approved')",
                frozenset({"epic"}),
                id="workflow-fixed-in-a-nested-macro",
            ),
            pytest.param(
                "steps.filter(s, s.workflow == workflow.id && s.slug == 'draft' && s.outcome == 'pass')",
                "size(selected) == 1",
                frozenset(),
                id="log-plane-only",
            ),
            pytest.param(
                "workspace.filter(a, (a.kind == 'epic' && a.data.state == 'x')"
                " || (a.kind == 'epic' && a.data.slug == 'y'))",
                "size(selected) > 0",
                None,
                id="guards-under-or",
            ),
            pytest.param(
                "workspace.all(a, a.kind == 'epic')",
                "selected",
                None,
                id="all-reads-every-kind",
            ),
        ],
    )
    def test_admits_a_well_typed_condition_and_records_the_kinds_it_reads(
        self, config_loader, framework_root, set_query, set_predicate, kinds
    ) -> None:
        """Spec (function 5, invariant 2, Typing): a `data` read is valid under a kind
        guard in the same macro — `a.kind == '<literal>'`, or a path join with a step
        instance of a literal step of a known workflow (the current one or a literal
        one), its kind that step's declared artifact — and a macro over a guarded
        `filter` result, `selected` included, reads that kind. The load records the
        only kinds `workspace` needs (`None`: every kind), so only those are read."""
        catalog = self._load_with(
            config_loader, framework_root, set_query=set_query, set_predicate=set_predicate
        )

        assert self._state_condition(catalog).workspace_kinds == kinds
