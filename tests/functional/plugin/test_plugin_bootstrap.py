"""The harness plugin bootstraps from its lock (adapter spec, Plugin mode — Python provisioning).

Run by the CI plugin job on every OS × Python of the matrix, over a plugin rendered with the
real, hash-locked `requirements.lock`:

    SAF_PLUGIN_BOOTSTRAP_ROOT=<rendered plugin> pytest tests/functional/plugin/test_plugin_bootstrap.py

It needs the network (or a mirror), so it is skipped unless that variable names a plugin.
No `SAF_HARNESS_PYTHON`: `sessionStart` provisions the keyed environment in `PLUGIN_DATA`,
and a governed session then runs end to end on it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from posix_shell import IS_WINDOWS
from test_plugin_mode import FRAMEWORK, HOST, ORCHESTRATOR, PluginRig, _git
from real_harness_rig import build_real_harness

BOOTSTRAP_ROOT = os.environ.get("SAF_PLUGIN_BOOTSTRAP_ROOT")

pytestmark = pytest.mark.skipif(
    not BOOTSTRAP_ROOT, reason="set SAF_PLUGIN_BOOTSTRAP_ROOT to a rendered plugin"
)


@pytest.fixture
def bootstrap(tmp_path: Path) -> PluginRig:
    plugin = Path(str(BOOTSTRAP_ROOT)).resolve()
    real = build_real_harness(tmp_path / "framework-rig", "github-copilot")
    registry = tmp_path / "registry"
    (registry / "frameworks" / FRAMEWORK).mkdir(parents=True)
    (registry / "frameworks" / FRAMEWORK / "root").write_text(
        f"{real.framework_dir}\n", encoding="utf-8"
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key not in ("SAF_HARNESS_PYTHON", "VIRTUAL_ENV", "PYTHONPATH")
        and not key.startswith("FRAMEWORK_")
    }
    environment.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        XDG_STATE_HOME=str(tmp_path / "xdg"),
        LOCALAPPDATA=str(tmp_path / "localappdata"),
        PLUGIN_ROOT=str(plugin),
        PLUGIN_DATA=str(tmp_path / "data"),
        SAF_AGENTIC_REGISTRY_DIR=str(registry),
    )
    rig = PluginRig(plugin, registry, tmp_path / "data", real.framework_dir,
                    real.workspace_dir.resolve(), tmp_path, environment)
    marker = rig.workspace / ".saf" / "workspace.yaml"
    marker.parent.mkdir()
    marker.write_text(f"framework: {FRAMEWORK}\nroot: report\n", encoding="utf-8")
    _git(rig.workspace, "add", ".saf")
    _git(rig.workspace, "commit", "-q", "-m", "marker")
    return rig


def test_session_start_provisions_from_the_lock_and_a_governed_session_runs(
    bootstrap: PluginRig,
) -> None:
    rig = bootstrap
    assert rig.hook("sessionStart", rig.payload(), index=0).code == 0

    started = rig.hook("sessionStart", rig.payload(source="new"))
    assert started.code == 0, started.stderr
    assert "SAFe agentic harness" in started.decision["additionalContext"]
    stamps = list((rig.data / "venv").glob(f"{rig.version}-*/.ready"))
    assert len(stamps) == 1

    h0 = rig.script("launcher", "userPromptSubmitted", "--framework", FRAMEWORK, "--agent",
                    ORCHESTRATOR, payload=rig.payload(prompt="go", timestamp=1),
                    folder=rig.registry / "harness")
    assert h0.code == 0, h0.stderr

    resolved = rig.hook("preToolUse", rig.harness_request())
    assert resolved.code == 0, resolved.stderr
    reason = resolved.decision["permissionDecisionReason"]
    assert reason.startswith("SAFe agentic harness RESULT (resolve-step)"), reason
    assert json.loads(reason.split("\n\n", 1)[1])["outcome"]["status"] == "step-resolution"

    again = rig.hook("sessionStart", dict(rig.payload(source="resume"), sessionId=HOST + "x"))
    assert again.code == 0
    assert list((rig.data / "venv").glob(f"{rig.version}-*/.ready")) == stamps


@pytest.mark.skipif(not IS_WINDOWS, reason="the PowerShell hook strings run on Windows")
def test_the_powershell_hook_strings_pass_the_payload_through(bootstrap: PluginRig) -> None:
    rig = bootstrap
    run = rig.hook("preToolUse", rig.shell_call("echo hi", folder=rig.elsewhere))

    assert (run.code, run.stdout) == (0, "")
