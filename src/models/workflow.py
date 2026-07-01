"""A parsed workflow.yaml — the `workflow` root (header + steps) as an entity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .step import Step


class Workflow:
    """The single `workflow` root node of a workflow.yaml: header fields + a steps model.

    A Workflow abstracts one workflow.yaml file. The `WorkflowRepository` maps the file
    to this entity; checkers read the header (`id`/`rank`/`facilitator`/`fsm`/`drives`)
    and the `steps` (each a `Step`) through it, plus the `after` DAG cycle check.
    """

    def __init__(self, data: dict[str, Any], path: Path | None = None) -> None:
        self._data = data if isinstance(data, dict) else {}
        self.path = path

    @property
    def block(self) -> dict[str, Any]:
        workflow = self._data.get("workflow")
        return workflow if isinstance(workflow, dict) else {}

    @property
    def id(self) -> Any:
        return self.block.get("id")

    @property
    def rank(self) -> Any:
        return self.block.get("rank")

    @property
    def facilitator(self) -> Any:
        return self.block.get("facilitator")

    @property
    def parent(self) -> Any:
        return self.block.get("parent")

    @property
    def skills(self) -> list[str]:
        raw = self.block.get("skills")
        return [str(s) for s in raw] if isinstance(raw, list) else []

    @property
    def drives(self) -> Any:
        return self.block.get("drives")

    @property
    def fsm(self) -> set[str]:
        return set(self.block.get("fsm") or [])

    @property
    def is_root(self) -> bool:
        return self.block.get("rank") == "root"

    @property
    def steps(self) -> list[Step]:
        raw = self.block.get("steps")
        return [Step(step) for step in raw if isinstance(step, dict)] if isinstance(raw, list) else []

    def step(self, step_id: str) -> Step | None:
        return next((step for step in self.steps if step.id == step_id), None)

    @property
    def step_ids(self) -> list[str]:
        return [step.id for step in self.steps if step.raw_id is not None]

    def cycle(self) -> list[str]:
        """Return one cycle in the `after` DAG as a list of step ids, or [] if acyclic."""
        graph: dict[str, list[str]] = {}
        for step in self.steps:
            if step.raw_id is not None:
                graph[step.id] = step.after_ids
        white, grey, black = 0, 1, 2
        color: dict[str, int] = {node: white for node in graph}
        stack: list[str] = []

        def visit(node: str) -> list[str]:
            color[node] = grey
            stack.append(node)
            for dep in graph.get(node, []):
                if dep not in graph:
                    continue
                if color[dep] == grey:
                    return stack[stack.index(dep):] + [dep]
                if color[dep] == white:
                    found = visit(dep)
                    if found:
                        return found
            stack.pop()
            color[node] = black
            return []

        for node in graph:
            if color[node] == white:
                found = visit(node)
                if found:
                    return found
        return []

    @property
    def raw(self) -> dict[str, Any]:
        return self._data
