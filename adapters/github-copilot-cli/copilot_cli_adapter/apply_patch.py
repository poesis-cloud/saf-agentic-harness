"""Target extraction from `apply_patch` patch text — the fail-closed write-path parser.

The patch grammar names its targets only in file headers:

    *** Begin Patch
    *** Add File: <path>        -> create
    *** Update File: <path>     -> update (a following `*** Move to: <dest>` turns the
                                   source into a delete and the destination into a create)
    *** Delete File: <path>     -> delete
    *** End Patch

Adapter spec H3 (fail closed): a patch whose targets cannot be extracted raises, and the
caller denies the call — an unparseable write is never passed through.
"""

from __future__ import annotations

import re

_BEGIN = "*** Begin Patch"
_FILE_HEADER = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$")
_MOVE_HEADER = re.compile(r"^\*\*\* Move to:\s*(.+?)\s*$")
_ACTIONS = {"Add": "create", "Update": "update", "Delete": "delete"}


class PatchParseError(ValueError):
    """Raised when a patch text names no extractable target."""


def parse_patch_targets(patch_text: str) -> tuple[tuple[str, str], ...]:
    """Answer every `(path, action)` a patch writes, in header order."""
    if not isinstance(patch_text, str) or _BEGIN not in patch_text:
        raise PatchParseError("patch text carries no '*** Begin Patch' envelope")
    targets: list[tuple[str, str]] = []
    for line in patch_text.splitlines():
        header = _FILE_HEADER.match(line.strip())
        if header is not None:
            targets.append((header.group(2), _ACTIONS[header.group(1)]))
            continue
        move = _MOVE_HEADER.match(line.strip())
        if move is not None:
            if not targets or targets[-1][1] != "update":
                raise PatchParseError("'*** Move to:' does not follow an Update File header")
            source, _ = targets.pop()
            targets.extend(((source, "delete"), (move.group(1), "create")))
    if not targets:
        raise PatchParseError("patch text names no Add/Update/Delete File header")
    return tuple(targets)


__all__ = ["PatchParseError", "parse_patch_targets"]
