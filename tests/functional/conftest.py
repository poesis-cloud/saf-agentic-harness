"""Shared fixtures for the functional suite: one framework and workspace per test.

Spec (Functional testing): every functional test drives the real command entry point
over a fixture framework configuration and a fixture workspace. Isolation is by tmp
directory and constructor injection; the only monkeypatching is of the process
environment carrying the framework layout (Configuration plane: the layout IS
environment, and the process environment takes precedence over the `.env` file).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pytest

from functional_fixtures import FRAMEWORK_ENV_KEYS, FunctionalHarness, build_framework

HarnessBuilder = Callable[..., FunctionalHarness]


@pytest.fixture()
def build_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HarnessBuilder:
    """Answer a builder of independent framework + workspace rigs under one tmp path."""
    for key in FRAMEWORK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    built: list[FunctionalHarness] = []

    def build(
        workflows: Mapping[str, Mapping[str, Any]] | None = None,
        model_profiles: Sequence[Mapping[str, Any]] | None = None,
        access_control_list: Mapping[str, Any] | None = None,
        workspace_layout: Mapping[str, Any] | None = None,
    ) -> FunctionalHarness:
        root = tmp_path / f"rig-{len(built)}"
        root.mkdir()
        harness = build_framework(
            root,
            workflows=workflows,
            model_profiles=model_profiles,
            access_control_list=access_control_list,
            workspace_layout=workspace_layout,
        )
        built.append(harness)
        return harness

    return build


@pytest.fixture()
def harness(build_harness: HarnessBuilder) -> FunctionalHarness:
    """Answer the default rig: two workflows, three agents, one artifact kind."""
    return build_harness()


@pytest.fixture()
def orchestrator_session(harness: FunctionalHarness) -> str:
    """Open a registered root orchestrator session and answer its id."""
    session_id = "orchestrator-session"
    harness.invoke("start-session", sessionId=session_id, agent="orchestrator")
    return session_id
