"""`apply_patch` target extraction — every header or nothing (fail closed)."""

from __future__ import annotations

import pytest

from copilot_cli_adapter.apply_patch import PatchParseError, parse_patch_targets

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
