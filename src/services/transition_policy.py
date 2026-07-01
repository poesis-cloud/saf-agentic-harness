"""TransitionPolicy — status vocabularies, transition tables, gates, and ownership."""

from __future__ import annotations


class TransitionPolicy:
    """The kanban policy for Epic/Feature/Story: which statuses exist, which edges are
    legal, which cross a human ★ gate, and which orchestrator owns each kind. Pure policy
    — no I/O — so checks and the calculation service share one source of truth."""

    EPIC_STATUSES = {"funnel", "reviewing", "analyzing", "portfolio-backlog", "implementing", "done", "blocked"}
    FEATURE_STATUSES = {"funnel", "refined", "arch-pending", "ready", "committed", "in-progress", "done", "blocked"}
    STORY_STATUSES = {"backlog", "ready", "in-progress", "in-review", "in-qa", "awaiting-pr", "done", "blocked"}
    DEPRECATED_FEATURE_STATUSES = {"adr-pending": "arch-pending"}

    EPIC_POST_GATE = {"portfolio-backlog", "implementing", "done"}
    FEATURE_POST_GATE = {"arch-pending", "ready", "committed", "in-progress", "done"}
    STORY_POST_GATE = {"ready", "in-progress", "in-review", "in-qa", "awaiting-pr", "done"}

    ORCHESTRATOR_KINDS = {
        "value-management-officier": {"epic"},
        "release-train-engineer": {"feature"},
        "scrum-master": {"story"},
    }
    ORCHESTRATOR_ALIASES = {
        "vmo": "value-management-officier",
        "value-management-office": "value-management-officier",
        "value-management-officier": "value-management-officier",
        "rte": "release-train-engineer",
        "release-train-engineer": "release-train-engineer",
        "sm": "scrum-master",
        "scrum-master": "scrum-master",
    }

    # Legal status edges per kind. `blocked` is an orthogonal flag: any active status may
    # enter it, and it may resume to any valid status for the kind.
    EPIC_TRANSITIONS = {
        ("funnel", "reviewing"),
        ("reviewing", "analyzing"),
        ("analyzing", "portfolio-backlog"),
        ("analyzing", "funnel"),
        ("portfolio-backlog", "implementing"),
        ("implementing", "done"),
    }
    FEATURE_TRANSITIONS = {
        ("funnel", "refined"),
        ("refined", "arch-pending"),
        ("refined", "ready"),
        ("refined", "funnel"),
        ("arch-pending", "ready"),
        ("arch-pending", "refined"),
        ("ready", "committed"),
        ("committed", "in-progress"),
        ("in-progress", "done"),
    }
    STORY_TRANSITIONS = {
        ("backlog", "ready"),
        ("ready", "in-progress"),
        ("in-progress", "in-review"),
        ("in-review", "in-progress"),
        ("in-review", "in-qa"),
        ("in-qa", "in-progress"),
        ("in-qa", "awaiting-pr"),
        ("awaiting-pr", "done"),
    }
    TRANSITIONS_BY_KIND = {"epic": EPIC_TRANSITIONS, "feature": FEATURE_TRANSITIONS, "story": STORY_TRANSITIONS}
    STATUSES_BY_KIND = {"epic": EPIC_STATUSES, "feature": FEATURE_STATUSES, "story": STORY_STATUSES}

    # Edges that cross a human gate: the orchestrator may only commit them after the
    # Central Supervisor decides (encoded as --gate accept/reject).
    GATE_EDGES = {
        "epic": {
            ("analyzing", "portfolio-backlog"): "Epic Gate",
            ("implementing", "done"): "Epic Outcome Gate",
        },
        "feature": {
            ("refined", "arch-pending"): "Feature Gate",
            ("refined", "ready"): "Feature Gate",
            ("arch-pending", "ready"): "Architecture Gate",
            ("in-progress", "done"): "Demo Gate",
        },
        "story": {
            ("backlog", "ready"): "Story Gate",
            ("awaiting-pr", "done"): "PR Gate",
        },
    }
    REJECT_TARGETS = {
        ("epic", "analyzing", "portfolio-backlog"): "funnel",
        ("epic", "implementing", "done"): "implementing",
        ("feature", "refined", "arch-pending"): "funnel",
        ("feature", "refined", "ready"): "funnel",
        ("feature", "arch-pending", "ready"): "refined",
        ("feature", "in-progress", "done"): "in-progress",
        ("story", "backlog", "ready"): "backlog",
        ("story", "awaiting-pr", "done"): "awaiting-pr",
    }

    def is_legal_edge(self, kind: str, from_status: str, to_status: str) -> bool:
        if to_status == "blocked":
            return from_status != "blocked"
        if from_status == "blocked":
            return to_status in self.STATUSES_BY_KIND.get(kind, set())
        return (from_status, to_status) in self.TRANSITIONS_BY_KIND.get(kind, set())

    def gate_for_edge(self, kind: str, from_status: str, to_status: str) -> str | None:
        return self.GATE_EDGES.get(kind, {}).get((from_status, to_status))
