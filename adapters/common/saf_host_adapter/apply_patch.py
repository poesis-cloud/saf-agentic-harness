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
# The host's own header prefixes, trailing space included: it takes the rest of the line
# verbatim (no trim) as the path it writes.
_FILE_HEADERS = (
    ("*** Add File: ", "create"),
    ("*** Update File: ", "update"),
    ("*** Delete File: ", "delete"),
)
_MOVE_HEADER = "*** Move to: "
# Anything that reads as a header without being one in the canonical form above.
_HEADER_LIKE = re.compile(r"^\s*\*\*\*\s*(?:(?:Add|Update|Delete)\s*File|Move\s*to)\s*:", re.I)


class PatchParseError(ValueError):
    """Raised when a patch text names no extractable target."""


def parse_patch_targets(patch_text: str) -> tuple[tuple[str, str], ...]:
    """Answer every `(path, action)` a patch writes, in header order.

    Lines are split on `\n` only and header paths are taken verbatim, as the host does. A
    path that is empty or has surrounding whitespace (a CR included) would be authorized
    under another name than the one the host writes, and a header-like line not in the
    canonical form is ambiguous: either makes the whole patch unextractable.
    """
    if not isinstance(patch_text, str) or _BEGIN not in patch_text:
        raise PatchParseError("patch text carries no '*** Begin Patch' envelope")
    targets: list[tuple[str, str]] = []
    for line in patch_text.split("\n"):
        if line.startswith(_MOVE_HEADER):
            destination = _verbatim_path(line[len(_MOVE_HEADER) :], line)
            if not targets or targets[-1][1] != "update":
                raise PatchParseError("'*** Move to:' does not follow an Update File header")
            source, _ = targets.pop()
            targets.extend(((source, "delete"), (destination, "create")))
            continue
        header = next(
            ((prefix, action) for prefix, action in _FILE_HEADERS if line.startswith(prefix)),
            None,
        )
        if header is not None:
            prefix, action = header
            targets.append((_verbatim_path(line[len(prefix) :], line), action))
        elif _HEADER_LIKE.match(line):
            raise PatchParseError(f"patch header {line!r} is not in the canonical form")
    if not targets:
        raise PatchParseError("patch text names no Add/Update/Delete File header")
    return tuple(targets)


def _verbatim_path(raw: str, line: str) -> str:
    if raw == "" or raw != raw.strip():
        raise PatchParseError(
            f"patch header {line!r} names an empty path or one with surrounding whitespace"
        )
    return raw


__all__ = ["PatchParseError", "parse_patch_targets"]
