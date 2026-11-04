"""Every entry answers UTF-8 whatever the console codepage of the process running it.

On Windows a child's stdio defaults to the ANSI codepage (cp1252): the em dash every
diagnostic carries becomes byte 0x97, which a host or a harness reading UTF-8 cannot
decode. Agent-scoped hooks carry no `PYTHONUTF8`, so the entries themselves must force
UTF-8. Forcing the child's stdio to cp1252 reproduces the Windows condition on any OS.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _cp1252_environment(tmp_path: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP")
        if name in os.environ
    }
    environment.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        SAF_HARNESS_STATE_DIR=str(tmp_path / "state"),
        PYTHONIOENCODING="cp1252",
        PYTHONUTF8="0",
    )
    return environment


@pytest.mark.parametrize(
    "arguments",
    [
        ("adapters/vscode-github-copilot-chat/adapter.py", "hook", "--event", "PreToolUse"),
        (
            "adapters/github-copilot-cli/adapter.py",
            "hook",
            "--event",
            "preToolUse",
            "--framework-dir",
            "missing-framework",
        ),
    ],
    ids=["vscode-github-copilot-chat", "github-copilot-cli"],
)
def test_a_hook_diagnostic_is_utf8_under_a_legacy_codepage(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    """A bootstrap failure's diagnostic (it carries an em dash) decodes strictly as UTF-8."""
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / arguments[0]), *arguments[1:]],
        input='\ufeff{"sessionId": "s\u00e9ance", "toolCalls": []}'.encode("utf-8"),
        capture_output=True,
        env=_cp1252_environment(tmp_path),
        cwd=tmp_path,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 2
    assert b"\x97" not in completed.stderr
    assert "\u2014" in completed.stderr.decode("utf-8")


def test_the_harness_answers_utf8_under_a_legacy_codepage(tmp_path: Path) -> None:
    """`harness.py` forces UTF-8 stdio too, so its reports reach the adapter intact."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; sys.argv = ['harness.py']; "
            "sys.modules['application'] = type(sys)('application'); "
            "sys.modules['application'].main = lambda: print('\\u2014') or 0; "
            f"runpy.run_path({str(REPO_ROOT / 'harness.py')!r}, run_name='__main__')",
        ],
        capture_output=True,
        env=_cp1252_environment(tmp_path),
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.decode("utf-8").strip() == "\u2014"
