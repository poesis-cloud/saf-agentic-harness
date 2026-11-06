"""The configuration load's typing check over the condition language (function 5, invariant 2)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

import celpy

from errors import ConfigurationError

_ARTIFACT = "artifact"
_STEP = "step"
_WORKFLOW = "workflow"
_UNKNOWN = "unknown"
_CURRENT = "<current>"
_PRECONDITION = "precondition"

_WORKSPACE = "workspace"
_WORKFLOWS = "workflows"
_STEPS = "steps"
_SELECTED = "selected"
_LANGUAGE_VARIABLES = frozenset({_WORKSPACE, _WORKFLOWS, _STEPS, _WORKFLOW, _STEP})

_MACROS = frozenset({"all", "exists", "exists_one", "filter", "map"})
# Macros whose result ignores the elements their guard rejects, so `workspace` may be
# narrowed to the guarded kinds without changing what they answer.
_NARROWING_MACROS = frozenset({"exists", "exists_one", "filter"})
_JOINING_MACROS = frozenset({"exists", "exists_one"})
# Tree nodes that wrap exactly one meaningful child when no operator is present.
_WRAPPERS = frozenset(
    {
        "expr",
        "conditionalor",
        "conditionaland",
        "relation",
        "addition",
        "multiplication",
        "unary",
        "member",
        "primary",
        "paren_expr",
    }
)


@dataclass(frozen=True)
class _Binding:
    """What the typing check knows about one name in scope.

    `kind` is an artifact binding's guard-fixed kind; `step_slug` and `workflow`
    a step binding's literal step and its workflow (`<current>` or a workflow
    slug); `workflow_slug` a workflow binding's literal slug; `workflow_ref` the
    workflow binding a step binding's `workflow` is joined to.
    """

    type: str
    kind: str | None = None
    step_slug: str | None = None
    workflow: str | None = None
    workflow_slug: str | None = None
    workflow_ref: str | None = None


def _children(node: Any) -> list[Any]:
    """List a tree node's non-placeholder children."""
    return [child for child in getattr(node, "children", ()) if child is not None]


def _is_tree(node: Any, name: str | None = None) -> bool:
    """Tell whether a node is a parse-tree node, optionally of one rule."""
    return hasattr(node, "data") and (name is None or node.data == name)


def _unwrap(node: Any) -> Any:
    """Descend through single-child wrapper nodes to the meaningful one."""
    while _is_tree(node) and node.data in _WRAPPERS:
        children = _children(node)
        if len(children) != 1 or not _is_tree(children[0]):
            break
        node = children[0]
    return node


def _split_conjuncts(node: Any) -> list[Any]:
    """Split an `&&` chain into its conjuncts; anything else is one conjunct."""
    node = _unwrap(node)
    if _is_tree(node, "conditionaland") and len(_children(node)) == 2:
        left, right = _children(node)
        return _split_conjuncts(left) + _split_conjuncts(right)
    return [node]


def _read_string_literal(node: Any) -> str | None:
    """Read a string literal's value, or none if the node is not one."""
    node = _unwrap(node)
    if not _is_tree(node, "literal"):
        return None
    token = _children(node)[0]
    if getattr(token, "type", None) not in ("STRING_LIT", "MLSTRING_LIT"):
        return None
    text = str(token)
    if text[:1] in "rR":
        text = text[1:]
    quote = 3 if text[:3] in ('"""', "'''") else 1
    return text[quote:-quote]


def _read_member_path(node: Any) -> tuple[str, list[str]] | None:
    """Read `ident.f1.f2` (or `ident['f']`) as its root name and field names."""
    node = _unwrap(node)
    fields: list[str] = []
    while True:
        if _is_tree(node, "member_dot"):
            receiver, field = _children(node)
            fields.insert(0, str(field))
            node = _unwrap(receiver)
        elif _is_tree(node, "member_index"):
            receiver, index = _children(node)
            literal = _read_string_literal(index)
            if literal is None:
                return None
            fields.insert(0, literal)
            node = _unwrap(receiver)
        elif _is_tree(node, "ident"):
            return str(_children(node)[0]), fields
        else:
            return None


def _read_macro(node: Any) -> tuple[Any, str, str, list[Any]] | None:
    """Read `range.macro(var, body...)` as its range, macro, variable, and bodies."""
    node = _unwrap(node)
    if not _is_tree(node, "member_dot_arg"):
        return None
    children = _children(node)
    if len(children) < 3 or str(children[1]) not in _MACROS:
        return None
    arguments = _children(children[2])
    if len(arguments) < 2:
        return None
    variable = _read_member_path(arguments[0])
    if variable is None or variable[1]:
        return None
    return children[0], str(children[1]), variable[0], arguments[1:]


def _read_equality(node: Any) -> tuple[Any, Any] | None:
    """Read `left == right`, or none if the node is not an equality."""
    node = _unwrap(node)
    if not _is_tree(node, "relation"):
        return None
    children = _children(node)
    if len(children) != 2 or not _is_tree(children[0], "relation_eq"):
        return None
    return _children(children[0])[0], children[1]


class ConditionTypeChecker:
    """Check `stateCondition` expressions at configuration load, fail-fast.

    Spec (function 5, invariant 2, Typing): a read of `a.data.<p>` is valid only
    where the kind of `a` is fixed by a guard in the same macro — `a.kind ==
    '<literal>'`, or a path join of `a.path` with the `artifact.path` of a step
    instance of a literal step of a known workflow (the current one, or one
    fixed by `w.slug == '<literal>'` joined by `s.workflow == w.id`). A macro over
    a guarded `filter` result — `selected` included — reads that kind. Each of
    these is a configuration load error: an unguarded `a.data` read, an unknown
    kind, an unknown step or workflow slug, and, in a precondition, a reference
    to a current-workflow step that is not a declared predecessor, or a path
    join with `step`.
    """

    def __init__(
        self,
        artifact_schemas: Mapping[str, Mapping[str, Any]],
        workflow_steps: Mapping[str, Mapping[str, str]],
        base_properties: Iterable[str] = (),
    ) -> None:
        """Create the checker over the catalog's kinds and its workflows' steps.

        `workflow_steps` maps a workflow slug to its step slugs and each step's
        declared artifact kind; `base_properties` are the properties every
        artifact inherits from the harness base contract.
        """
        self._artifact_schemas = artifact_schemas
        self._workflow_steps = workflow_steps
        self._base_properties = frozenset(base_properties)
        self._environment = celpy.Environment()

    def check_condition(
        self,
        workflow_slug: str,
        step_slug: str,
        condition: Mapping[str, Any],
        predecessors: frozenset[str],
    ) -> frozenset[str] | None:
        """Check one `stateCondition`, answering the only kinds `workspace` needs.

        Answers `None` when the expressions may read every kind; the
        evaluator then loads every artifact instance of the evaluated state.
        """
        context = _CheckContext(
            checker=self,
            workflow_slug=workflow_slug,
            step_slug=step_slug,
            condition_slug=str(condition["slug"]),
            condition_kind=str(condition["kind"]),
            predecessors=predecessors,
        )
        query = self._environment.compile(condition["setSelector"]["setQuery"])
        constants = {name: _Binding(type=_UNKNOWN) for name in _LANGUAGE_VARIABLES}
        context.visit_conjunction(query, constants)
        selected = context.read_element(query, constants)
        predicate = self._environment.compile(condition["setPredicate"])
        context.visit_conjunction(predicate, {**constants, _SELECTED: selected})
        return None if context.reads_every_kind else frozenset(context.kinds)

    def read_properties(self, kind: str) -> Mapping[str, Any] | None:
        """Read one kind's declared top-level properties, the base's included."""
        schema = self._artifact_schemas.get(kind)
        if schema is None:
            return None
        properties = dict.fromkeys(self._base_properties, {})
        properties.update(schema.get("properties") or {})
        return properties

    def find_step_artifact(self, workflow: str, step: str) -> str | None:
        """Read a workflow step's declared artifact kind, or none if unknown."""
        return self._workflow_steps.get(workflow, {}).get(step)

    def is_known_kind(self, kind: str) -> bool:
        """Tell whether a kind names a declared artifact schema."""
        return kind in self._artifact_schemas

    def is_known_workflow(self, workflow: str) -> bool:
        """Tell whether a slug names a catalog workflow."""
        return workflow in self._workflow_steps

    def is_known_step(self, step: str) -> bool:
        """Tell whether a slug names a step of any catalog workflow."""
        return any(step in steps for steps in self._workflow_steps.values())


class _CheckContext:
    """One condition's walk: scope tracking, guards, and the kinds it reads."""

    def __init__(
        self,
        checker: ConditionTypeChecker,
        workflow_slug: str,
        step_slug: str,
        condition_slug: str,
        condition_kind: str,
        predecessors: frozenset[str],
    ) -> None:
        self._checker = checker
        self._workflow_slug = workflow_slug
        self._step_slug = step_slug
        self._condition_slug = condition_slug
        self._condition_kind = condition_kind
        self._predecessors = predecessors
        self.kinds: set[str] = set()
        self.reads_every_kind = False

    # -- the walk ----------------------------------------------------------------

    def visit_conjunction(self, node: Any, scope: Mapping[str, _Binding]) -> None:
        """Apply the guards of an `&&` chain, then visit each conjunct under them."""
        conjuncts = _split_conjuncts(node)
        guarded = self._apply_guards(conjuncts, scope)
        for conjunct in conjuncts:
            self._visit_node(conjunct, guarded)

    def _visit_node(self, node: Any, scope: Mapping[str, _Binding]) -> None:
        """Visit one node: an `&&` chain, a macro, a member path, or anything else."""
        node = _unwrap(node)
        if not _is_tree(node):
            return
        if _is_tree(node, "conditionaland") and len(_children(node)) == 2:
            self.visit_conjunction(node, scope)
            return
        macro = _read_macro(node)
        if macro is not None:
            self._visit_macro(macro, scope)
            return
        equality = _read_equality(node)
        if equality is not None:
            self._check_equality(equality, scope)
        path = _read_member_path(node)
        if path is not None:
            self._check_member_path(path, scope)
            return
        if _is_tree(node, "member_index"):
            receiver, index = _children(node)
            self._visit_node(receiver, scope)
            self.visit_conjunction(index, scope)
            return
        if _is_tree(node, "member_dot_arg"):
            receiver, _, *arguments = _children(node)
            self._visit_node(receiver, scope)
            for argument in arguments:
                for child in _children(argument):
                    self.visit_conjunction(child, scope)
            return
        for child in _children(node):
            if _is_tree(child):
                self.visit_conjunction(child, scope)

    def _visit_macro(
        self, macro: tuple[Any, str, str, list[Any]], scope: Mapping[str, _Binding]
    ) -> None:
        """Visit a macro: its range, then its bodies with the variable bound."""
        receiver, name, variable, bodies = macro
        over_workspace = _read_member_path(receiver) == (_WORKSPACE, [])
        if not over_workspace:
            self._visit_node(receiver, scope)
        element = self.read_element(receiver, scope)
        inner = {**scope, variable: element}
        self.visit_conjunction(bodies[0], inner)
        if len(bodies) == 2:
            # Three-argument `map(v, predicate, transform)`: the transform only runs on
            # elements the predicate admits, so it reads under the predicate's guards.
            refined = self._apply_guards(_split_conjuncts(bodies[0]), inner)
            self.visit_conjunction(bodies[1], refined)
        if over_workspace:
            guarded = self._apply_guards(_split_conjuncts(bodies[0]), inner)[variable]
            narrows = name in _NARROWING_MACROS or (name == "map" and len(bodies) == 2)
            if narrows and guarded.kind is not None:
                self.kinds.add(guarded.kind)
            else:
                self.reads_every_kind = True

    def read_element(self, node: Any, scope: Mapping[str, _Binding]) -> _Binding:
        """Read the element binding of a list expression: its type and guard facts."""
        node = _unwrap(node)
        if _is_tree(node, "ident"):
            name = str(_children(node)[0])
            if name == _WORKSPACE:
                return _Binding(type=_ARTIFACT)
            if name == _STEPS:
                return _Binding(type=_STEP)
            if name == _WORKFLOWS:
                return _Binding(type=_WORKFLOW)
            if name == _SELECTED and name in scope:
                return scope[name]
            return _Binding(type=_UNKNOWN)
        macro = _read_macro(node)
        if macro is None or macro[1] != "filter":
            return _Binding(type=_UNKNOWN)
        receiver, _, variable, bodies = macro
        inner = {**scope, variable: self.read_element(receiver, scope)}
        return self._apply_guards(_split_conjuncts(bodies[0]), inner)[variable]

    # -- guards ------------------------------------------------------------------

    def _apply_guards(
        self, conjuncts: Sequence[Any], scope: Mapping[str, _Binding]
    ) -> dict[str, _Binding]:
        """Derive what the conjuncts of one `&&` chain fix about the names in scope."""
        guarded = dict(scope)
        joins: list[tuple[str, str]] = []
        for conjunct in conjuncts:
            equality = _read_equality(conjunct)
            if equality is not None:
                self._apply_equality(equality, guarded, joins)
        for conjunct in conjuncts:
            macro = _read_macro(conjunct)
            if macro is not None and macro[1] in _JOINING_MACROS:
                self._apply_nested_guards(macro, guarded)
        for name in list(guarded):
            self._resolve_step_workflow(name, guarded)
        for artifact, step in joins:
            self._apply_path_join(artifact, step, guarded)
        return guarded

    def _apply_equality(
        self,
        equality: tuple[Any, Any],
        guarded: dict[str, _Binding],
        joins: list[tuple[str, str]],
    ) -> None:
        """Record one equality's fact: a kind, a slug, a workflow link, or a path join."""
        for left, right in (equality, equality[::-1]):
            path = _read_member_path(left)
            if path is None:
                continue
            name, fields = path
            binding = guarded.get(name)
            literal = _read_string_literal(right)
            other = _read_member_path(right)
            if binding is None:
                continue
            if fields == ["kind"] and binding.type == _ARTIFACT and literal is not None:
                if not self._checker.is_known_kind(literal):
                    self._fail(
                        "unknown-artifact-kind",
                        f"guards on the unknown artifact kind '{literal}'",
                    )
                guarded[name] = replace(binding, kind=literal)
            elif fields == ["slug"] and binding.type == _STEP and literal is not None:
                guarded[name] = replace(binding, step_slug=literal)
            elif fields == ["slug"] and binding.type == _WORKFLOW and literal is not None:
                if not self._checker.is_known_workflow(literal):
                    self._fail(
                        "unknown-condition-workflow",
                        f"names the unknown workflow '{literal}'",
                    )
                guarded[name] = replace(binding, workflow_slug=literal)
            elif fields == ["workflow"] and binding.type == _STEP and other is not None:
                if other == (_WORKFLOW, ["id"]):
                    guarded[name] = replace(binding, workflow=_CURRENT)
                elif other[1] == ["id"] and guarded.get(other[0], _Binding(_UNKNOWN)).type == _WORKFLOW:
                    guarded[name] = replace(binding, workflow_ref=other[0])
            elif fields == ["path"] and binding.type == _ARTIFACT and other is not None:
                if other[1] == ["artifact", "path"]:
                    joins.append((name, other[0]))

    def _check_equality(
        self, equality: tuple[Any, Any], scope: Mapping[str, _Binding]
    ) -> None:
        """Check any equality's literals and, in a precondition, a path join with `step`.

        Unlike a guard, these hold wherever the equality sits — under `||` or `!`
        included.
        """
        for left, right in (equality, equality[::-1]):
            path = _read_member_path(left)
            if path is None:
                continue
            name, fields = path
            binding = scope.get(name)
            other = _read_member_path(right)
            if (
                self._condition_kind == _PRECONDITION
                and path == (_STEP, ["artifact", "path"])
                and scope.get(_STEP) == _Binding(type=_UNKNOWN)
                and other is not None
                and other[1][-1:] == ["path"]
            ):
                self._fail(
                    "precondition-step-path-join",
                    "joins a path with `step`, whose artifact path is null in a "
                    "precondition",
                )
            literal = _read_string_literal(right)
            if binding is None or literal is None:
                continue
            if fields == ["kind"] and binding.type == _ARTIFACT:
                if not self._checker.is_known_kind(literal):
                    self._fail(
                        "unknown-artifact-kind",
                        f"compares with the unknown artifact kind '{literal}'",
                    )
            elif fields == ["slug"] and binding.type == _STEP:
                if not self._checker.is_known_step(literal):
                    self._fail(
                        "unknown-condition-step",
                        f"names the unknown step '{literal}'",
                    )
            elif fields == ["slug"] and binding.type == _WORKFLOW:
                if not self._checker.is_known_workflow(literal):
                    self._fail(
                        "unknown-condition-workflow",
                        f"names the unknown workflow '{literal}'",
                    )

    def _apply_nested_guards(
        self, macro: tuple[Any, str, str, list[Any]], guarded: dict[str, _Binding]
    ) -> None:
        """Lift the kinds a nested `exists` / `exists_one` fixes for outer artifacts."""
        receiver, _, variable, bodies = macro
        inner = {**guarded, variable: self.read_element(receiver, guarded)}
        lifted = self._apply_guards(_split_conjuncts(bodies[-1]), inner)
        for name, binding in list(guarded.items()):
            if name == variable:
                continue
            nested = lifted[name]
            if binding.type == _ARTIFACT and binding.kind is None and nested.kind is not None:
                guarded[name] = replace(binding, kind=nested.kind)
            elif binding.type == _STEP:
                guarded[name] = replace(
                    binding,
                    step_slug=binding.step_slug or nested.step_slug,
                    workflow=binding.workflow or nested.workflow,
                )

    def _resolve_step_workflow(self, name: str, guarded: dict[str, _Binding]) -> None:
        """Fix a step binding's workflow from a joined workflow binding, then check it."""
        binding = guarded[name]
        if binding.type != _STEP:
            return
        if binding.workflow is None and binding.workflow_ref is not None:
            workflow = guarded.get(binding.workflow_ref)
            if workflow is not None and workflow.workflow_slug is not None:
                binding = replace(binding, workflow=workflow.workflow_slug)
                guarded[name] = binding
        if binding.step_slug is None:
            return
        workflow = self._read_workflow(binding)
        if workflow is None:
            if not self._checker.is_known_step(binding.step_slug):
                self._fail(
                    "unknown-condition-step",
                    f"names the unknown step '{binding.step_slug}'",
                )
            return
        if self._checker.find_step_artifact(workflow, binding.step_slug) is None:
            self._fail(
                "unknown-condition-step",
                f"names the step '{binding.step_slug}', which workflow '{workflow}' "
                f"does not declare",
            )
        if (
            self._condition_kind == _PRECONDITION
            and workflow == self._workflow_slug
            and binding.step_slug not in self._predecessors
        ):
            self._fail(
                "precondition-step-not-predecessor",
                f"refers to step '{binding.step_slug}' of the current workflow, which "
                f"is not a declared predecessor of step '{self._step_slug}' and has "
                f"not necessarily run yet",
            )

    def _apply_path_join(
        self, artifact: str, step: str, guarded: dict[str, _Binding]
    ) -> None:
        """Fix an artifact binding's kind from a path join with a fixed step instance."""
        if step == _STEP and guarded.get(_STEP) == _Binding(type=_UNKNOWN):
            if self._condition_kind == _PRECONDITION:
                self._fail(
                    "precondition-step-path-join",
                    "joins a path with `step`, whose artifact path is null in a "
                    "precondition",
                )
            kind = self._checker.find_step_artifact(self._workflow_slug, self._step_slug)
        else:
            binding = guarded.get(step)
            if binding is None or binding.type != _STEP or binding.step_slug is None:
                return
            workflow = self._read_workflow(binding)
            if workflow is None:
                return
            kind = self._checker.find_step_artifact(workflow, binding.step_slug)
        if kind is not None and guarded[artifact].kind is None:
            guarded[artifact] = replace(guarded[artifact], kind=kind)

    def _read_workflow(self, binding: _Binding) -> str | None:
        """Read the workflow slug a step binding is fixed to, if any."""
        if binding.workflow == _CURRENT:
            return self._workflow_slug
        return binding.workflow

    # -- reads -------------------------------------------------------------------

    def _check_member_path(
        self, path: tuple[str, list[str]], scope: Mapping[str, _Binding]
    ) -> None:
        """Check a name is in scope and, for an artifact's `data`, its guard and schema."""
        name, fields = path
        binding = scope.get(name)
        if binding is None:
            self._fail(
                "unknown-condition-variable",
                f"reads '{name}', which is no variable of the condition language "
                f"({', '.join(sorted(_LANGUAGE_VARIABLES))}, `selected` in "
                f"setPredicate) nor a macro variable in scope",
            )
        if name == _WORKSPACE and binding.type == _UNKNOWN:
            self.reads_every_kind = True
        if binding.type != _ARTIFACT or not fields or fields[0] != "data":
            return
        if binding.kind is None:
            self._fail(
                "unguarded-artifact-data",
                f"reads '{name}.{'.'.join(fields)}' without a guard fixing the kind of "
                f"'{name}' in the same macro (`{name}.kind == '<kind>'`, or a path "
                f"join with a step instance of a literal step of a known workflow)",
            )
        self._check_data_path(name, binding.kind, fields[1:])

    def _check_data_path(self, name: str, kind: str, fields: Sequence[str]) -> None:
        """Check a `data` read's property path against the kind's schema."""
        properties = self._checker.read_properties(kind)
        for depth, field in enumerate(fields):
            if properties is None:
                return
            if field not in properties:
                self._fail(
                    "undeclared-artifact-property",
                    f"reads '{name}.data.{'.'.join(fields[: depth + 1])}', which the "
                    f"'{kind}' schema does not declare",
                )
            nested = properties[field]
            properties = nested.get("properties") if isinstance(nested, Mapping) else None

    def _fail(self, code: str, detail: str) -> None:
        """Raise the configuration load error naming the condition."""
        raise ConfigurationError(
            code,
            f"Workflow '{self._workflow_slug}' step '{self._step_slug}' condition "
            f"'{self._condition_slug}' {detail}.",
            False,
        )


__all__ = ["ConditionTypeChecker"]
