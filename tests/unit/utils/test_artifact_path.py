"""Tests for `parse_artifact_path` — one workspace-relative path on every OS."""

from __future__ import annotations

import pytest

from utils import parse_artifact_path


@pytest.mark.parametrize(
    ("raw", "parts"),
    [
        ("portfolio/epics/one.md", ("portfolio", "epics", "one.md")),
        ("portfolio\\epics\\one.md", ("portfolio", "epics", "one.md")),
        ("portfolio\\epics/one.md#frag", ("portfolio", "epics", "one.md#frag")),
    ],
)
def test_reads_both_separators(raw: str, parts: tuple[str, ...]) -> None:
    assert parse_artifact_path(raw).parts == parts


def test_answers_posix_rendering_whatever_the_host_named() -> None:
    assert parse_artifact_path("scratch\\notes.json").as_posix() == "scratch/notes.json"
