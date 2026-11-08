"""One workflow: the ordered steps one facilitator drives."""

from __future__ import annotations

from dataclasses import dataclass

from config.step import Step


@dataclass(frozen=True)
class Workflow:
    """Carry one workflow's facilitator, advisory predecessors, refs, and steps.

    Spec (`workflow.conf.schema.json`): the schema's `orchestrator` is exposed as
    `facilitator` and its advisory `predecessors` as `after` — the workflows that
    NATURALLY come before this one, shaping `propose` only, never a hard gate.
    """

    slug: str
    facilitator: str
    steps: tuple[Step, ...]
    after: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    description: str | None = None


__all__ = ["Workflow"]
