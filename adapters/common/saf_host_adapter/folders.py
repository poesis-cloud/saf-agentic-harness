"""Create the missing folders of an allowed `create` target — never outside the workspace.

The containment check of a target path is lexical, but folder creation follows whatever
the filesystem holds: a symlink or a Windows junction anywhere under the workspace would
carry `mkdir` outside it. So creation starts from the REAL workspace path and walks one
component at a time, refusing any component that is a symlink, a junction (any reparse
point) or not a folder.

On POSIX each step is opened relative to its already-verified parent with `O_NOFOLLOW`,
so a component swapped for a link between the check and the next step is refused, not
followed. Windows has no `dir_fd`: each component is checked after it exists, and the
deepest folder's real path must still be the expected one inside the real workspace.
"""

from __future__ import annotations

import os
import stat
from typing import Sequence

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UnsafeFolderError(OSError):
    """Raised when a folder to create would not be a plain folder inside the workspace."""


def make_contained_folders(workspace_dir: str, parts: Sequence[str]) -> None:
    """Create `<real workspace>/<parts...>` folder by folder, refusing any link ancestor."""
    for part in parts:
        if part in ("", ".", "..") or "/" in part or "\\" in part or "\0" in part:
            raise UnsafeFolderError(f"unsafe folder name {part!r}")
    root = os.path.realpath(workspace_dir)
    if not os.path.isdir(root):
        raise UnsafeFolderError(f"the workspace is not a folder: {root}")
    if not parts:
        return
    if _supports_dir_fd():
        _make_relative_to_descriptors(root, parts)
    else:
        _make_checked_paths(root, parts)


def _supports_dir_fd() -> bool:
    return (
        os.mkdir in os.supports_dir_fd
        and os.open in os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )


def _make_relative_to_descriptors(root: str, parts: Sequence[str]) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            try:
                os.mkdir(part, dir_fd=descriptor)
            except FileExistsError:
                pass
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as failure:
                raise UnsafeFolderError(
                    f"'{part}' under the workspace is a link or not a folder: {failure}"
                ) from failure
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _make_checked_paths(root: str, parts: Sequence[str]) -> None:
    current = root
    for part in parts:
        current = os.path.join(current, part)
        try:
            os.mkdir(current)
        except FileExistsError:
            pass
        _require_plain_folder(current)
    expected = os.path.normcase(os.path.join(root, *parts))
    if os.path.normcase(os.path.realpath(current)) != expected:
        raise UnsafeFolderError(f"'{current}' does not resolve inside the workspace")


def _require_plain_folder(path: str) -> None:
    status = os.lstat(path)
    if (
        stat.S_ISLNK(status.st_mode)
        or getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
        or not stat.S_ISDIR(status.st_mode)
    ):
        raise UnsafeFolderError(f"'{path}' is a link, a junction or not a folder")


__all__ = ["UnsafeFolderError", "make_contained_folders"]
