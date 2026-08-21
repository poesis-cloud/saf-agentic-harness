"""The journal's ordering key has ONE renderer, injected into every journaling service."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from services.checking.checking_service import CheckingService
from services.context_resolution.context_resolver import ContextResolver
from services.model_resolution.step_model_resolver import StepModelResolver
from services.session_lifecycle.session_lifecycle import SessionLifecycle
from services.step_resolution.step_resolver import StepResolver
from utils.clock import Clock

_SRC_DIR = Path(__file__).resolve().parents[3] / "src"

_JOURNALING_SERVICES = (
    SessionLifecycle,
    ContextResolver,
    StepResolver,
    StepModelResolver,
    CheckingService,
)


def _modules_reading_the_wall_clock(package: str) -> tuple[str, ...]:
    """Answer every module under a src package that calls `datetime.now` itself."""
    offenders: list[str] = []
    for module_path in sorted((_SRC_DIR / package).rglob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "now"
            ):
                offenders.append(module_path.relative_to(_SRC_DIR).as_posix())
                break
    return tuple(offenders)


class TestJournalClock:
    @pytest.mark.parametrize("package", ["services", "stores"])
    def test_no_service_or_store_reads_the_wall_clock_itself(self, package: str) -> None:
        """Spec (Logging, Ordering): the `timestamp` is ONE cross-log total ordering key,
        so the rendering that makes a string sort an instant sort belongs to ONE
        collaborator — `utils.Clock` — never to each journaling module."""
        assert _modules_reading_the_wall_clock(package) == ()

    @pytest.mark.parametrize("service", _JOURNALING_SERVICES)
    def test_every_journaling_service_takes_the_shared_clock(self, service: type) -> None:
        """Spec (Classes, `utils`): the domain-free mechanics are injected collaborators
        — a service that journals an entry stamps it with the clock it was given, never
        with one it renders privately."""
        parameter = inspect.signature(service.__init__).parameters.get("clock")

        assert parameter is not None, f"{service.__name__} takes no clock"
        assert parameter.default is None
        assert parameter.annotation == "Clock | None"

    def test_the_shared_clock_is_the_domain_free_utility(self) -> None:
        """Spec (Classes, `utils`): "the domain-free mechanics" — the clock knows the
        rendering, nothing about sessions, workflows, or reports."""
        assert Clock.__module__ == "utils.clock"
        assert [
            name for name in vars(Clock) if not name.startswith("_")
        ] == ["TIMESTAMP_FORMAT", "read_timestamp"]
