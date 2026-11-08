"""Function 10's acceptance verdict: was the step ref promoted into workspace state?"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Promotion:
    """Carry whether the step ref was promoted, and the workspace branch tip left.

    Spec (function 10, Out; invariant 6): `promoted` when the workspace branch
    fast-forwarded to the evaluated step state's commit, `withheld` otherwise —
    `workspace_commit` is then the unchanged tip.
    """

    outcome: str
    workspace_commit: str

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `promotion` object."""
        return {"outcome": self.outcome, "workspaceCommit": self.workspace_commit}


__all__ = ["Promotion"]
