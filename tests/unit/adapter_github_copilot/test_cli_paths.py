"""Host paths → workspace-relative artifact paths, Windows first (adapter spec H3/H5)."""

from __future__ import annotations

import pytest

from cli_rig import CWD, WORKSPACE
from github_copilot_adapter.paths import host_basename, is_windows_path, relativize, resolve_host_path


@pytest.mark.parametrize(
    "raw, expected",
    [
        (r"C:\Users\cleme\saf-org\workspace\portfolio\a.md", "portfolio/a.md"),
        (r"c:/users/CLEME/saf-org/Workspace/portfolio/a.md", "portfolio/a.md"),
        (r"workspace\portfolio\art\x.md", "portfolio/art/x.md"),
        (r"C:\Users\cleme\saf-org\workspace\portfolio\..\portfolio\b.md", "portfolio/b.md"),
        (r"D:\other\a.md", "D:/other/a.md"),
        (r"\\server\share\a.md", "//server/share/a.md"),
        (WORKSPACE, "C:/Users/cleme/saf-org/workspace"),
    ],
)
def test_windows_paths_relativize_against_the_workspace(raw: str, expected: str) -> None:
    assert relativize(raw, CWD, WORKSPACE) == expected


def test_posix_paths_relativize_against_a_posix_workspace() -> None:
    assert relativize("/srv/fw/workspace/portfolio/a.md", "/srv/fw", "/srv/fw/workspace") == (
        "portfolio/a.md"
    )
    assert relativize("workspace/portfolio/a.md", "/srv/fw", "/srv/fw/workspace") == (
        "portfolio/a.md"
    )


def test_a_backslash_name_under_a_posix_cwd_stays_one_literal_file_name() -> None:
    relative = relativize(r"a\b.md", "/srv/fw/workspace", "/srv/fw/workspace")
    assert relative == r"a\b.md"


def test_windows_paths_compare_case_insensitively() -> None:
    assert resolve_host_path(r"C:\H\Harness.py", None) == resolve_host_path(
        r"c:\h\harness.py", r"C:\elsewhere"
    )


def test_flavor_detection_and_basename() -> None:
    assert is_windows_path(r"C:\x") and is_windows_path("c:/x") and is_windows_path(r"\\s\h")
    assert not is_windows_path("/x/y")
    assert host_basename(WORKSPACE) == "workspace"
    assert host_basename("/srv/fw/ws/") == "ws"
