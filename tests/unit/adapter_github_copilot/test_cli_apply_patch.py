"""`apply_patch` target extraction — every header or nothing (fail closed)."""

from __future__ import annotations

import pytest

from github_copilot_adapter.apply_patch import PatchParseError, parse_patch_targets

PATCH = """*** Begin Patch
*** Add File: C:\\fw\\workspace\\portfolio\\new.md
+hello
*** Update File: C:\\fw\\workspace\\portfolio\\old.md
*** Move to: C:\\fw\\workspace\\portfolio\\moved.md
@@
-a
+b
*** Delete File: C:\\fw\\workspace\\portfolio\\gone.md
*** End Patch
"""


def test_every_header_is_a_target_with_its_action() -> None:
    targets = parse_patch_targets(PATCH)
    paths = {path: action for path, action in targets}
    assert paths["C:\\fw\\workspace\\portfolio\\new.md"] == "create"
    assert paths["C:\\fw\\workspace\\portfolio\\old.md"] in ("update", "delete")
    assert paths["C:\\fw\\workspace\\portfolio\\moved.md"] == "create"
    assert paths["C:\\fw\\workspace\\portfolio\\gone.md"] == "delete"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a patch",
        "*** Begin Patch\n*** End Patch\n",
        "*** Begin Patch\n*** Move to: x.md\n*** End Patch\n",
    ],
)
def test_an_unreadable_patch_raises(text: str) -> None:
    with pytest.raises(PatchParseError):
        parse_patch_targets(text)


@pytest.mark.parametrize(
    "header",
    [
        "*** Add File: portfolio/a.md ",
        "*** Add File:  portfolio/a.md",
        "*** Add File: portfolio/a.md\r",
        "*** Add File: ",
        "*** Delete File: \tportfolio/a.md",
        "*** Update File: portfolio/a.md\n*** Move to: portfolio/b.md ",
        "  *** Add File: portfolio/a.md",
        "*** Add File:portfolio/a.md",
    ],
    ids=[
        "trailing-space",
        "leading-space",
        "trailing-cr",
        "empty",
        "leading-tab",
        "move-trailing-space",
        "indented-header",
        "no-space-after-colon",
    ],
)
def test_a_header_path_that_is_not_verbatim_makes_the_patch_unextractable(header: str) -> None:
    """The host writes the header path verbatim: `portfolio/a.md ` is another file than
    `portfolio/a.md` on POSIX, so a trimmed path would authorize the wrong target."""
    with pytest.raises(PatchParseError):
        parse_patch_targets(f"*** Begin Patch\n{header}\n+x\n*** End Patch\n")


def test_header_paths_are_taken_verbatim() -> None:
    patch = "*** Begin Patch\r\n*** Add File: portfolio/a b.md\n+x\r\n*** End Patch\r\n"
    assert parse_patch_targets(patch) == (("portfolio/a b.md", "create"),)
