"""CelEvaluator — the only check language + the read-only fact set."""

from __future__ import annotations

import json
from typing import Any

import celpy
from celpy import celtypes

from models import Artifact
from persistence import ArtifactRepository, Workspace


class CelEvaluator:
    """Compiles and evaluates `cel` condition expressions against the always-present
    read-only fact set assembled for one unit. This is the only thing the harness
    *evaluates*; structural refs and `instruction` files are resolved by `StepChecker`.
    The derived facts (gate_packet_ok / wsjf_correct / …) delegate to the artifact checker
    and calculation service, so CEL stays side-effect-free.
    """

    _ENV = celpy.Environment()

    def __init__(self, workspace: Workspace, artifacts: ArtifactRepository, artifact_checker, calculation) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.artifact_checker = artifact_checker
        self.calculation = calculation

    # --- coercion helpers ---------------------------------------------------
    @staticmethod
    def _jsonable(obj: Any) -> Any:
        """Coerce YAML-loaded values (dates, etc.) into JSON-native types for json_to_cel."""
        return json.loads(json.dumps(obj, default=str))

    @staticmethod
    def _unit_view(artifact: Artifact) -> dict[str, Any]:
        return {"id": artifact.artifact_id, "kind": artifact.kind, "status": artifact.status}

    # --- derived facts ------------------------------------------------------
    def _depends_on_met(self, artifact: Artifact | None) -> bool:
        if artifact is None:
            return True
        for dep_id in artifact.dependency_ids():
            deps = self.artifacts.resolve_dependency(dep_id)
            if len(deps) != 1 or deps[0].status != "done":
                return False
        return True

    def _all_children_done(self, artifact: Artifact | None) -> bool:
        if artifact is None:
            return False
        children = self.artifacts.child_stories(artifact)
        return bool(children) and all(child.status == "done" for child in children)

    def _child_qa_signoffs_present(self, artifact: Artifact | None) -> bool:
        if artifact is None:
            return False
        children = self.artifacts.child_stories(artifact)
        return bool(children) and all(self.artifacts.find_qa_signoff(child) is not None for child in children)

    def _blocked_route_ok(self, artifact: Artifact | None) -> bool:
        if artifact is None or artifact.status != "blocked":
            return True
        has_reason = artifact.field_value("block_reason") is not None or artifact.field_value("blocked_reason") is not None
        return has_reason or artifact.has_blocking_open_items()

    def _gate_packet_ok(self, unit_id: str) -> bool:
        return not self.artifact_checker.check_gate_packet(unit_id).has_errors()

    def _inventory_complete(self, artifact: Artifact | None) -> bool:
        if artifact is None or artifact.kind != "feature":
            return True
        if not artifact.bool_field("structurant"):
            return True
        inventory = artifact.field("architecture_inventory")
        if inventory in (None, "null", ""):
            return False
        base = self.artifacts.product_root(artifact)
        if not (base / str(inventory)).is_file():
            return False
        return all(self.artifacts.find_adr(base, adr_id) is not None for adr_id in artifact.list_field("adrs"))

    def _wsjf_correct(self, artifact: Artifact | None) -> bool:
        if artifact is None:
            return True
        computed, stored = self.calculation.compute_wsjf_score(artifact)
        if computed is None:
            return True
        return abs(self.calculation.coerce_number(stored) - computed) < 1e-9

    def _cost_rollup_correct(self, artifact: Artifact | None) -> bool:
        if artifact is None:
            return True
        cost = self.calculation.cost_block(artifact)
        if cost is None or cost.get("tokens_self") is None:
            return True
        return self.calculation.coerce_number(cost["tokens_self"]) == cost["tokens_in"] + cost["tokens_out"]

    # --- activation + evaluation -------------------------------------------
    def build_activation(self, unit_id: str, product: str | None, artifact: Artifact | None) -> tuple[Any, dict[str, Any]]:
        """Assemble the read-only CEL fact set + functions for one unit. Every fact is always
        in scope, so an unbound reference in a cel value is a contract typo (hard error)."""
        facts: dict[str, Any] = {
            "status": artifact.status if artifact else None,
            "unit": dict(artifact.fields) if artifact else {},
            "child_features": [self._unit_view(a) for a in (self.artifacts.child_features(artifact) if artifact else [])],
            "child_stories": [self._unit_view(a) for a in (self.artifacts.child_stories(artifact) if artifact else [])],
            "product": product,
            "unit_id": unit_id,
            "open_items_clear": (not artifact.has_blocking_open_items()) if artifact else True,
            "depends_on_met": self._depends_on_met(artifact),
            "all_children_done": self._all_children_done(artifact),
            "child_qa_signoffs_present": self._child_qa_signoffs_present(artifact),
            "blocked_route_ok": self._blocked_route_ok(artifact),
            "gate_packet_ok": self._gate_packet_ok(unit_id),
            "inventory_complete": self._inventory_complete(artifact),
            "wsjf_correct": self._wsjf_correct(artifact),
            "cost_rollup_correct": self._cost_rollup_correct(artifact),
        }
        activation = celpy.json_to_cel(self._jsonable(facts))
        return activation, {}

    @classmethod
    def compile_expr(cls, src: str) -> tuple[bool | None, str | None]:
        """Compile a CEL `src` for syntax (used by check-contracts). (True, None) or (None, msg)."""
        try:
            cls._ENV.compile(src)
            return True, None
        except Exception as exc:  # parse / syntax error
            return None, str(exc)

    def evaluate_expr(self, src: str, activation: Any, functions: dict[str, Any]) -> tuple[str, Any]:
        """Evaluate one CEL condition against the always-present fact set. ('bool', x) or
        ('error', msg) — an unbound reference is a contract typo, since every fact is in scope."""
        try:
            program = self._ENV.program(self._ENV.compile(src), functions=functions)
        except Exception as exc:
            return ("error", f"invalid expr {src!r}: {exc}")
        try:
            result = program.evaluate(activation)
        except celpy.CELEvalError as exc:
            return ("error", f"expr {src!r} failed: {exc}")
        if isinstance(result, (celtypes.BoolType, bool)):
            return ("bool", bool(result))
        return ("error", f"expr {src!r} must yield a bool, got {type(result).__name__}")

    # --- set-query execution (for state conditions) -------------------------
    def _enumerate_artifacts(self, artifact_types: list[dict[str, Any]]) -> dict[str, list[Any]]:
        """Build a dict of {alias: [artifacts]} for each FROM binding in artifact_types.
        Each entry collects all artifacts matching the schema_id and converts to CEL-friendly dicts.
        Returns empty dict if any schema_id is unknown."""
        result: dict[str, list[Any]] = {}
        for binding in artifact_types:
            alias = binding.get("alias", "")
            schema_id = binding.get("schema_id", "")
            if not alias or not schema_id:
                continue
            artifacts = self.artifacts.collect_by_schema_id(schema_id)
            result[alias] = [{"id": a.artifact_id, "kind": a.kind, "status": a.status} for a in artifacts]
        return result

    def build_list_activation(self, artifact_types: list[dict[str, Any]]) -> tuple[Any | None, str | None]:
        """Build a CEL activation binding aliases to artifact lists (for set_query evaluation).
        Returns (activation, None) on success or (None, error_msg) on failure."""
        enum_result = self._enumerate_artifacts(artifact_types)
        if not enum_result:
            return None, "no valid artifact_types decoded from set_selector"
        try:
            # Build facts dict with all aliases + runtime constants
            facts = dict(enum_result)
            facts["product"] = None  # add standard runtime constants if needed
            activation = celpy.json_to_cel(self._jsonable(facts))
            return activation, None
        except Exception as exc:
            return None, f"failed to build list activation: {exc}"

    def evaluate_set_query(self, src: str, activation: Any) -> tuple[str, Any]:
        """Evaluate a CEL set_query (list-returning expression) against aliases bound in activation.
        Returns ('list', [items]) if result is list-like, or ('error', msg) on failure."""
        try:
            program = self._ENV.program(self._ENV.compile(src))
        except Exception as exc:
            return ("error", f"invalid set_query {src!r}: {exc}")
        try:
            result = program.evaluate(activation)
        except celpy.CELEvalError as exc:
            return ("error", f"set_query {src!r} failed: {exc}")
        # Accept list, ListType, or any iterable (but not string)
        if isinstance(result, str):
            return ("error", f"set_query {src!r} yielded a string; expected list")
        try:
            result_list = list(result)
            return ("list", result_list)
        except TypeError:
            return ("error", f"set_query {src!r} must yield a list, got {type(result).__name__}")

    def evaluate_set_predicate(self, src: str, activation: Any) -> tuple[str, Any]:
        """Evaluate a CEL set_predicate (bool-returning expression) over the selected set.
        Reuses evaluate_expr but binds to the set-query activation (which includes set aliases)."""
        return self.evaluate_expr(src, activation, {})
