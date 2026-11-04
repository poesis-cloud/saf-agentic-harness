"""Host-path handling that is correct for Windows and POSIX paths alike.

The Copilot app on Windows names every path as an absolute Windows path
(`C:\\Users\\...`); the harness contracts name workspace artifacts as workspace-RELATIVE,
forward-slash paths. The flavor is chosen from the path text itself rather than from the
running OS, so the same code is exercised by the Linux test suite with Windows payloads.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath

_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|//[^/])")
_DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:(?![\\/])")


def is_windows_path(raw: str) -> bool:
    """Tell whether a path text is Windows-shaped (drive letter, UNC, or backslash)."""
    return bool(_WINDOWS_ABSOLUTE.match(raw) or _DRIVE_RELATIVE.match(raw)) or "\\" in raw


def _is_posix_absolute(raw: str) -> bool:
    return raw.startswith("/") and not raw.startswith("//")


def _flavor(raw: str, *anchors: str) -> type[PurePath]:
    """Pick the path flavor: an absolute anchor (cwd, workspace) decides, else the path."""
    for anchor in anchors:
        if anchor and _WINDOWS_ABSOLUTE.match(anchor):
            return PureWindowsPath
        if anchor and _is_posix_absolute(anchor):
            return PureWindowsPath if _WINDOWS_ABSOLUTE.match(raw) else PurePosixPath
    return PureWindowsPath if is_windows_path(raw) else PurePosixPath


def _normalize(flavor: type[PurePath], raw: str) -> PurePath:
    module = ntpath if flavor is PureWindowsPath else posixpath
    return flavor(module.normpath(raw))


def resolve_host_path(raw: str, cwd: str | None, workspace_dir: str = "") -> PurePath:
    """Resolve a tool-argument path against the event's `cwd`, normalized for its flavor."""
    flavor = _flavor(raw, cwd or "", workspace_dir)
    candidate = flavor(raw)
    if not candidate.is_absolute() and cwd:
        candidate = flavor(cwd) / candidate
    return _normalize(flavor, str(candidate))


def relativize(raw: str, cwd: str | None, workspace_dir: str) -> str:
    """Answer the workspace-relative, forward-slash path, or the absolute host path.

    A path outside the workspace keeps its absolute form (forward slashes): the harness,
    not the adapter, decides what a write outside the governed tree means.
    """
    resolved = resolve_host_path(raw, cwd, workspace_dir)
    flavor = type(resolved)
    workspace = _normalize(flavor, workspace_dir) if workspace_dir else None
    if workspace is not None and _same_flavor_absolute(workspace, flavor):
        try:
            relative = resolved.relative_to(workspace)
        except ValueError:
            relative = None
        if relative is not None and str(relative) not in ("", "."):
            return relative.as_posix()
    return resolved.as_posix()


def is_within(raw: str, cwd: str | None, workspace_dir: str) -> bool:
    """Tell whether a path, resolved against `cwd`, is the workspace or lies inside it."""
    if not workspace_dir:
        return False
    resolved = resolve_host_path(raw, cwd, workspace_dir)
    workspace = _normalize(type(resolved), workspace_dir)
    if not _same_flavor_absolute(workspace, type(resolved)) or not resolved.is_absolute():
        return False
    return resolved == workspace or workspace in resolved.parents


def _same_flavor_absolute(path: PurePath, flavor: type[PurePath]) -> bool:
    return isinstance(path, flavor) and path.is_absolute()




def host_basename(raw: str) -> str:
    """The last component of a host path, in that path's own flavor."""
    flavor = PureWindowsPath if is_windows_path(raw) else PurePosixPath
    return flavor(raw.rstrip("\\/") or raw).name


__all__ = ["host_basename", "is_within", "is_windows_path", "relativize", "resolve_host_path"]
