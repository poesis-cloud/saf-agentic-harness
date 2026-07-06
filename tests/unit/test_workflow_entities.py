"""Unit tests — the workflow configuration entities: Workflow, Step, Condition
(one test class per src class)."""

from __future__ import annotations

from config import Condition, Step, Workflow


def _wf(steps: list[dict], **header) -> Workflow:
    return Workflow({"workflow": {"id": "wf", **header, "steps": steps}})


class TestWorkflow:
    def test_header_accessors(self):
        wf = Workflow({"workflow": {"id": "team", "facilitator": "@scrum-master", "after": ["program-sync"]}})
        assert wf.id == "team"
        assert wf.facilitator == "@scrum-master"
        assert wf.after_ids == ["program-sync"]

    def test_workflow_without_after_is_an_entry_point(self):
        assert Workflow({"workflow": {"id": "entry"}}).after_ids == []

    def test_missing_workflow_root_is_empty(self):
        wf = Workflow({"not-workflow": {}})
        assert wf.block == {}
        assert wf.steps == []

    def test_steps_and_lookup(self):
        wf = _wf([{"id": "a"}, {"id": "b"}])
        assert wf.step_ids == ["a", "b"]
        assert wf.step("b") is not None
        assert wf.step("ghost") is None

    def test_non_dict_steps_are_skipped(self):
        wf = _wf([{"id": "a"}, "not-a-step", 42])
        assert wf.step_ids == ["a"]

    def test_acyclic_after_dag_has_no_cycle(self):
        wf = _wf([
            {"id": "a"},
            {"id": "b", "conditions": [{"type": "after", "step_id": "a"}]},
            {"id": "c", "conditions": [{"type": "after", "step_id": "b"}]},
        ])
        assert wf.cycle() == []

    def test_cycle_is_detected_and_named(self):
        wf = _wf([
            {"id": "a", "conditions": [{"type": "after", "step_id": "b"}]},
            {"id": "b", "conditions": [{"type": "after", "step_id": "a"}]},
        ])
        cycle = wf.cycle()
        assert cycle, "a <-> b must be reported as a cycle"
        assert set(cycle) <= {"a", "b"}

    def test_dangling_after_reference_is_not_a_cycle(self):
        wf = _wf([{"id": "a", "conditions": [{"type": "after", "step_id": "missing"}]}])
        assert wf.cycle() == []


class TestStep:
    def test_identity_and_dispatch_metadata(self):
        step = Step({"id": "draft", "actor": "@dev", "artifacts": ["story"], "skills": ["s1", "s2"]})
        assert step.id == "draft"
        assert step.actor == "@dev"
        assert step.artifacts == ["story"]
        assert step.skills == ["s1", "s2"]

    def test_capabilities_coerced_to_float_weights(self):
        step = Step({"id": "s", "capabilities": {"deep-reasoning": 7, "coding": "3.5"}})
        assert step.capabilities == {"deep-reasoning": 7.0, "coding": 3.5}

    def test_capabilities_skip_non_numeric_weights(self):
        step = Step({"id": "s", "capabilities": {"deep-reasoning": 7, "coding": "high"}})
        assert step.capabilities == {"deep-reasoning": 7.0}

    def test_capabilities_missing_or_malformed_is_empty(self):
        assert Step({"id": "s"}).capabilities == {}
        assert Step({"id": "s", "capabilities": ["not", "a", "map"]}).capabilities == {}

    def test_instructions_and_prompts_normalize_string_or_list(self):
        step = Step({"id": "s", "instructions": "one.instructions.md", "prompts": ["a.prompt.md", "b.prompt.md"]})
        assert step.instructions == ["one.instructions.md"]
        assert step.prompts == ["a.prompt.md", "b.prompt.md"]
        assert Step({"id": "s"}).instructions == []

    def test_after_ids_preserve_order_and_dedupe(self):
        step = Step({"id": "s", "conditions": [
            {"type": "after", "step_id": "a"},
            {"type": "after", "step_id": "b"},
            {"type": "after", "step_id": "a"},
            {"type": "state", "set_predicate": "true"},
        ]})
        assert step.after_ids == ["a", "b"]

    def test_conditions_skip_non_dict_entries(self):
        step = Step({"id": "s", "conditions": [{"type": "after", "step_id": "a"}, "junk"]})
        assert len(step.conditions) == 1


class TestCondition:
    def test_after_condition_fields(self):
        cond = Condition({"id": "c1", "kind": "precondition", "type": "after", "step_id": "draft"})
        assert cond.id == "c1"
        assert cond.kind == "precondition"
        assert cond.type == "after"
        assert cond.step_id == "draft"
        assert cond.set_selector is None
        assert cond.set_predicate is None

    def test_state_condition_fields(self):
        cond = Condition({
            "id": "c2", "kind": "postcondition", "type": "state",
            "set_selector": {"set_type": "artifact"},
            "set_predicate": "selected.all(x, x.status == 'done')",
        })
        assert cond.set_selector == {"set_type": "artifact"}
        assert cond.set_predicate == "selected.all(x, x.status == 'done')"

    def test_empty_values_normalize_to_none(self):
        cond = Condition({"id": "", "step_id": "", "set_predicate": ""})
        assert cond.id is None
        assert cond.step_id is None
        assert cond.set_predicate is None
