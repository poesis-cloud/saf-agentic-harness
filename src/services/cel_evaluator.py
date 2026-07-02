"""CelEvaluator — the only check language + the read-only fact set."""

from __future__ import annotations

import json
from typing import Any

import celpy
import lark
from celpy import celtypes

from models import Artifact
from persistence import ArtifactRepository, SchemaRepository, Workspace


class CelEvaluator:
    """Compiles and evaluates `cel` condition expressions against the always-present
    read-only fact set assembled for one unit. This is the only thing the harness
    *evaluates*; structural refs and `instruction` files are resolved by `StepChecker`.
    The derived facts (gate_packet_ok / wsjf_correct / …) delegate to the artifact checker
    and calculation service, so CEL stays side-effect-free.

    For `type: state` conditions it runs the two-CEL selector/predicate pipeline:
    `set_selector.set_query` selects a bounded set of artifacts (typed by their schema),
    and `set_predicate` asserts a boolean over that selected set. Field references in both
    CEL expressions are statically validated against the closed property set of each alias's
    schema — an undeclared property is a hard error (celpy resolves a missing map key to
    ``null`` at runtime, so closedness must be enforced statically before evaluation).
    """

    _ENV = celpy.Environment()
    # CEL comprehension macros that bind an iteration variable over a range's elements.
    _BINDING_MACROS = {"all", "exists", "exists_one", "filter", "map"}

    def __init__(
        self,
        workspace: Workspace,
        artifacts: ArtifactRepository,
        schemas: SchemaRepository | None = None,
    ) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.schemas = schemas
        self._schema_prop_index: dict[str, set[str]] | None = None

    # --- coercion helpers ---------------------------------------------------
    @staticmethod
    def _jsonable(obj: Any) -> Any:
        """Coerce YAML-loaded values (dates, etc.) into JSON-native types for json_to_cel."""
        return json.loads(json.dumps(obj, default=str))

    def evaluate_expr(self, src: str, activation: Any, functions: dict[str, Any]) -> tuple[str, Any]:
        """Evaluate one bool CEL expression against an activation. ('bool', x) or ('error', msg)."""
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
    # A `type: state` condition runs two CEL evaluations over a bounded artifact universe:
    #   1. set_selector.set_query  → selects a set of artifacts (the SELECTED set)
    #   2. set_predicate           → asserts a boolean over that selected set (bound as `selected`)
    # Each artifact is exposed as a CLOSED map of exactly its schema's declared properties, so a
    # reference to an undeclared property is invalid. celpy resolves a missing map key to `null`
    # at runtime (never raising), so closedness is enforced by a static AST field-check BEFORE
    # evaluation — that is what actually invalidates an off-schema property reference.

    def _schema_properties_index(self) -> dict[str, set[str]]:
        """Map every artifact schema_id (and its artifact_kind alias) to its closed set of
        authored property names — the `__`-prefixed harness-injected keys are excluded, since
        they are not part of an artifact's queryable state. Cached per evaluator instance."""
        if self._schema_prop_index is None:
            index: dict[str, set[str]] = {}
            if self.schemas is not None:
                for artifact_schema in self.schemas.load():
                    props = {
                        key
                        for key in artifact_schema.schema.get("properties", {}).keys()
                        if not key.startswith("__")
                    }
                    index[artifact_schema.schema_id] = props
                    index[artifact_schema.artifact_kind] = props
            self._schema_prop_index = index
        return self._schema_prop_index

    def _alias_props(self, artifact_types: list[dict[str, Any]]) -> tuple[dict[str, set[str]] | None, str | None]:
        """Resolve each FROM binding (alias + schema_id) to the alias's closed property set.
        Returns (alias_props, None) on success or (None, error_msg) if a binding is malformed
        or names an unknown schema_id."""
        index = self._schema_properties_index()
        alias_props: dict[str, set[str]] = {}
        for binding in artifact_types:
            alias = binding.get("alias", "")
            schema_id = binding.get("schema_id", "")
            if not alias or not schema_id:
                return None, f"artifact_types binding missing alias/schema_id: {binding!r}"
            if schema_id not in index:
                return None, f"unknown schema_id {schema_id!r} in set_selector (no artifact schema declares it)"
            alias_props[alias] = index[schema_id]
        if not alias_props:
            return None, "set_selector.artifact_types is empty"
        return alias_props, None

    def _artifact_view(self, artifact: Artifact, props: set[str]) -> dict[str, Any]:
        """Expose ONE artifact as a closed map of exactly its schema's declared properties."""
        return {prop: artifact.field(prop) for prop in props}

    def _enumerate_artifacts(self, artifact_types: list[dict[str, Any]], alias_props: dict[str, set[str]]) -> dict[str, list[dict[str, Any]]]:
        """Build {alias: [closed artifact maps]} for each FROM binding — the bounded universe
        the set_query selects from."""
        result: dict[str, list[dict[str, Any]]] = {}
        for binding in artifact_types:
            alias = binding["alias"]
            schema_id = binding["schema_id"]
            props = alias_props[alias]
            artifacts = self.artifacts.collect_by_schema_id(schema_id)
            result[alias] = [self._artifact_view(a, props) for a in artifacts]
        return result

    # --- static field-reference validation (schema closedness) --------------
    @staticmethod
    def _leftmost_ident(node: Any) -> str | None:
        """Descend a `member` chain to its base identifier name (or None if the base is not a
        bare identifier, e.g. a literal or a parenthesised expression)."""
        current = node
        while isinstance(current, lark.Tree):
            if current.data == "ident":
                token = current.children[0]
                return str(getattr(token, "value", token))
            if not current.children:
                return None
            current = current.children[0]
        return None

    def _validate_field_refs(self, src: str, alias_props: dict[str, set[str]]) -> str | None:
        """Statically reject any `<artifact>.<property>` reference whose property is not declared
        in that artifact's schema. Resolves comprehension-bound variables to their range's element
        schema (`alias.all(x, x.prop)` → `x` has the alias's properties). Returns an error message
        or None. Bases that are not artifact-typed (runtime constants like `product`) are ignored."""
        try:
            ast = self._ENV.compile(src)
        except Exception as exc:
            return f"invalid CEL {src!r}: {exc}"

        violations: list[str] = []

        def resolve_alias(member_node: Any, scope: dict[str, str]) -> str | None:
            base = self._leftmost_ident(member_node)
            if base is None:
                return None
            if base in scope:
                return scope[base]
            if base in alias_props:
                return base
            return None

        def walk(node: Any, scope: dict[str, str]) -> None:
            if not isinstance(node, lark.Tree):
                return
            if node.data == "member_dot_arg" and len(node.children) >= 2:
                range_member = node.children[0]
                macro = str(getattr(node.children[1], "value", node.children[1]))
                exprlist = node.children[2] if len(node.children) > 2 else None
                if macro in self._BINDING_MACROS and isinstance(exprlist, lark.Tree) and len(exprlist.children) >= 2:
                    # range keeps its element type through filter/all/exists/…; map is treated
                    # conservatively as element-preserving (its transform is not type-tracked).
                    element_alias = resolve_alias(range_member, scope)
                    var_name = self._leftmost_ident(exprlist.children[0])
                    walk(range_member, scope)
                    body_scope = dict(scope)
                    if var_name and element_alias:
                        body_scope[var_name] = element_alias
                    for body in exprlist.children[1:]:
                        walk(body, body_scope)
                    return
                for child in node.children:
                    walk(child, scope)
                return
            if node.data == "member_dot" and len(node.children) >= 2:
                base_member = node.children[0]
                prop = str(getattr(node.children[1], "value", node.children[1]))
                base_alias = resolve_alias(base_member, scope)
                if base_alias is not None and prop not in alias_props.get(base_alias, set()):
                    violations.append(f"property '{prop}' is not declared in schema of '{base_alias}'")
                walk(base_member, scope)
                return
            for child in node.children:
                walk(child, scope)

        walk(ast, {})
        if violations:
            # de-duplicate while preserving order
            seen: dict[str, None] = {}
            for v in violations:
                seen.setdefault(v, None)
            return "; ".join(seen.keys())
        return None

    # --- runtime constants + evaluation -------------------------------------
    @staticmethod
    def _with_constants(facts: dict[str, Any], unit_id: str | None = None) -> dict[str, Any]:
        """Bind the always-in-scope runtime constants so a set expression may reference them without
        an unbound-name error. `unit_id` scopes a query to the acting unit; `product` is reserved."""
        facts.setdefault("product", None)
        facts.setdefault("unit_id", unit_id)
        return facts

    def evaluate_set_query(self, src: str, activation: Any) -> tuple[str, Any]:
        """Evaluate a CEL set_query (list-returning expression) against the bounded universe.
        Returns ('list', [items]) if the result is list-like, or ('error', msg) on failure."""
        try:
            program = self._ENV.program(self._ENV.compile(src))
        except Exception as exc:
            return ("error", f"invalid set_query {src!r}: {exc}")
        try:
            result = program.evaluate(activation)
        except celpy.CELEvalError as exc:
            return ("error", f"set_query {src!r} failed: {exc}")
        if isinstance(result, str):
            return ("error", f"set_query {src!r} yielded a string; expected a set (list)")
        try:
            return ("list", list(result))
        except TypeError:
            return ("error", f"set_query {src!r} must yield a set (list), got {type(result).__name__}")

    def evaluate_set_predicate(self, src: str, activation: Any) -> tuple[str, Any]:
        """Evaluate a CEL set_predicate (bool-returning) over the selected set (bound as
        `selected` in the activation). Returns ('bool', x) or ('error', msg)."""
        return self.evaluate_expr(src, activation, {})

    def validate_state_condition(self, selector: dict[str, Any] | None, predicate_src: str | None) -> str | None:
        """Design-time (no-portfolio) static validation of a `type: state` condition: the
        artifact-backed selector declares `artifact_types` (alias → schema_id) and a CEL `set_query`
        yielding the `selected` set the `set_predicate` asserts over. Every `<alias>.<property>`
        reference (in query and predicate) is checked against that alias's schema — an undeclared
        property is a hard error. Returns an error message or None.
        """
        selector = selector or {}
        if not predicate_src:
            return "set_predicate is required"
        artifact_types = selector.get("artifact_types") or []
        set_query = selector.get("set_query") or ""
        if not artifact_types:
            return "set_selector.artifact_types is required"
        if not set_query:
            return "set_selector.set_query is required"
        alias_props, err = self._alias_props(artifact_types)
        if err or alias_props is None:
            return err or "could not resolve set_selector aliases"
        query_err = self._validate_field_refs(set_query, alias_props)
        if query_err:
            return f"set_query: {query_err}"
        union_props = set().union(*alias_props.values()) if alias_props else set()
        predicate_aliases = dict(alias_props)
        predicate_aliases["selected"] = union_props
        predicate_err = self._validate_field_refs(predicate_src, predicate_aliases)
        if predicate_err:
            return f"set_predicate: {predicate_err}"
        return None

    def evaluate_state(self, selector: dict[str, Any] | None, predicate_src: str | None, unit_id: str | None = None) -> tuple[str, str]:
        """Evaluate a `type: state` condition. SELECT a set via `set_query` (the `unit_id` runtime
        constant is in scope, so `epic.filter(e, e.id == unit_id)` scopes to the acting unit), then
        ASSERT `set_predicate` over that set (bound as `selected`). Field references are statically
        validated against the schemas before evaluation. Returns ('pass'|'fail'|'error', detail).
        """
        static_err = self.validate_state_condition(selector, predicate_src)
        if static_err:
            return ("error", static_err)

        selector = selector or {}
        artifact_types = selector.get("artifact_types") or []
        set_query = selector.get("set_query") or ""
        alias_props, _ = self._alias_props(artifact_types)
        assert alias_props is not None  # guaranteed by validate_state_condition above

        # 1. evaluate the selector → the SELECTED set
        universe = self._enumerate_artifacts(artifact_types, alias_props)
        total = sum(len(v) for v in universe.values())
        query_activation = celpy.json_to_cel(self._jsonable(self._with_constants(dict(universe), unit_id)))
        qkind, selected = self.evaluate_set_query(set_query, query_activation)
        if qkind == "error":
            return ("error", selected)
        selected_py = self._jsonable(selected)

        # 2. evaluate the predicate over the selected set, bound as `selected`
        predicate_facts = dict(universe)
        predicate_facts["selected"] = selected_py
        predicate_activation = celpy.json_to_cel(self._jsonable(self._with_constants(predicate_facts, unit_id)))
        pkind, ok = self.evaluate_set_predicate(predicate_src or "", predicate_activation)
        if pkind == "error":
            return ("error", ok)
        detail = f"selected {len(selected_py)} of {total} artifact(s)"
        return ("pass" if ok else "fail", detail)


