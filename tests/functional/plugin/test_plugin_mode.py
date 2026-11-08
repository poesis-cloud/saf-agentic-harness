"""The rendered harness plugin, run as the host runs it (adapter spec, Plugin mode).

Every rule of plugin mode, through real processes: the rendered hook command strings, the
shims, the shell-only registration, the stable launcher, and `launcher.py` in front of the
adapter and the real harness. The runtime is the test interpreter (`SAF_HARNESS_PYTHON`),
so nothing is downloaded; provisioning from the lock is exercised by the CI bootstrap job.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from posix_shell import IS_WINDOWS, child_environment
from real_harness_rig import WORKFLOW, build_real_harness

REPO_ROOT = Path(__file__).resolve().parents[3]
FRAMEWORK = "poesis-saf-agentic-organization"
ORCHESTRATOR = "orchestrator"
HOST = "1d793d07-adc6-4f25-bec3-5b03b44e949e"
NOT_A_WORKSPACE = "this folder is not a SAF workspace; ask me to initialise it"


def _load_renderer() -> Any:
    spec = importlib.util.spec_from_file_location(
        "plugin_suite_render_plugin", REPO_ROOT / "adapters" / "github-copilot" / "render_plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDERER = _load_renderer()


@dataclass(frozen=True)
class Run:
    code: int
    stdout: str
    stderr: str

    @property
    def decision(self) -> Mapping[str, Any] | None:
        return json.loads(self.stdout) if self.stdout.strip() else None


@dataclass
class PluginRig:
    plugin: Path
    registry: Path
    data: Path
    framework: Path
    workspace: Path
    elsewhere: Path
    environment: dict[str, str]

    @property
    def version(self) -> str:
        return (self.plugin / "VERSION").read_text(encoding="utf-8").strip()

    def without_runtime(self) -> dict[str, str]:
        """No `SAF_HARNESS_PYTHON` and no provisioned environment: the runtime is missing."""
        return {key: value for key, value in self.environment.items()
                if key != "SAF_HARNESS_PYTHON"}

    def no_wait(self) -> dict[str, str]:
        return dict(self.environment, SAF_AGENTIC_REGISTRY_WAIT_SEC="0")

    def launcher(self, event: str, payload: Mapping[str, Any], *arguments: str,
                 environment: Mapping[str, str] | None = None) -> Run:
        return _run(
            [sys.executable, str(self.plugin / "launcher.py"), event, *arguments],
            payload, environment or self.environment,
        )

    def script(self, name: str, *arguments: str, payload: Mapping[str, Any] | None = None,
               folder: Path | None = None, environment: Mapping[str, str] | None = None) -> Run:
        """Run `bin/<name>` (or the registry's stable launcher) the way its hook does."""
        path = folder or self.plugin / "bin"
        if IS_WINDOWS:
            argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(path / f"{name}.ps1"), *arguments]
        else:
            argv = ["sh", str(path / f"{name}.sh"), *arguments]
        return _run(argv, payload or {}, environment or self.environment)

    def hook(self, event: str, payload: Mapping[str, Any], index: int = -1,
             environment: Mapping[str, str] | None = None) -> Run:
        """Run the RENDERED `hooks.json` entry's command string through its shell."""
        hooks = json.loads(
            (self.plugin / "com.github.copilot" / "hooks" / "hooks.json").read_text("utf-8")
        )["hooks"]
        entry = hooks[event][index]
        if IS_WINDOWS:
            argv = ["powershell", "-NoProfile", "-Command", entry["powershell"]]
        else:
            argv = ["bash", "-c", entry["bash"]]
        return _run(argv, payload, environment or self.environment)

    def register(self) -> None:
        assert self.script("register").code == 0

    def payload(self, folder: Path | None = None, **fields: Any) -> dict[str, Any]:
        return {"sessionId": HOST, "cwd": str(folder or self.workspace), **fields}

    def harness_request(self, folder: Path | None = None) -> dict[str, Any]:
        shell = "powershell" if IS_WINDOWS else "bash"
        return self.payload(folder, toolCalls=[{"id": "r", "name": shell, "args": {
            "command": f"python {self.plugin / 'harness.py'} resolve-step --workflow-slug {WORKFLOW}",
            "description": "resolve"}}])

    def shell_call(self, command: str, folder: Path | None = None) -> dict[str, Any]:
        shell = "powershell" if IS_WINDOWS else "bash"
        return self.payload(folder, toolCalls=[{"id": "s", "name": shell, "args": {
            "command": command, "description": "x"}}])

    def write_marker(self, text: str, commit: bool = True) -> None:
        marker = self.workspace / ".saf" / "workspace.yaml"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(text, encoding="utf-8")
        if commit:
            _git(self.workspace, "add", ".saf")
            _git(self.workspace, "commit", "-q", "-m", "marker")


def _run(argv: list[str], payload: Mapping[str, Any], environment: Mapping[str, str]) -> Run:
    done = subprocess.run(
        argv, input=json.dumps(payload).encode("utf-8"), capture_output=True,
        env=child_environment(environment), timeout=180,
    )
    return Run(done.returncode, done.stdout.decode("utf-8", "replace"),
               done.stderr.decode("utf-8", "replace"))


def _git(folder: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=folder, check=True, capture_output=True)


@pytest.fixture
def rig(tmp_path: Path) -> PluginRig:
    lock = tmp_path / "requirements.lock"
    lock.write_text("# offline suite: the runtime is SAF_HARNESS_PYTHON\n", encoding="utf-8")
    plugin = tmp_path / "plugin"
    RENDERER.render(plugin, lock)
    real = build_real_harness(tmp_path / "framework-rig", "github-copilot")
    registry = tmp_path / "registry"
    (registry / "frameworks" / FRAMEWORK).mkdir(parents=True)
    (registry / "frameworks" / FRAMEWORK / "root").write_text(
        f"{real.framework_dir}\n", encoding="utf-8"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "PLUGIN_ROOT": str(plugin),
        "PLUGIN_DATA": str(tmp_path / "data"),
        "SAF_AGENTIC_REGISTRY_DIR": str(registry),
        "SAF_HARNESS_PYTHON": sys.executable,
    }
    built = PluginRig(plugin, registry, tmp_path / "data", real.framework_dir,
                      real.workspace_dir.resolve(), elsewhere, environment)
    built.write_marker(f"framework: {FRAMEWORK}\nroot: report\n")
    return built


# The registry -------------------------------------------------------------------------------


def test_registration_records_this_version_and_installs_the_stable_launcher(
    rig: PluginRig,
) -> None:
    assert rig.hook("sessionStart", rig.payload(), index=0).code == 0

    harness = rig.registry / "harness"
    assert (harness / "latest").read_text("utf-8").strip() == rig.version
    assert Path((harness / rig.version / "root").read_text("utf-8").strip()) == rig.plugin
    assert Path((harness / rig.version / "data").read_text("utf-8").strip()) == rig.data
    for name in ("launcher.sh", "launcher.ps1"):
        assert (harness / name).read_bytes() == (rig.plugin / "bin" / name).read_bytes()
    assert not [path for path in rig.registry.rglob("*") if ".tmp" in path.name]


def test_an_unwritable_registry_warns_at_session_start(rig: PluginRig, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    environment = dict(rig.environment, SAF_AGENTIC_REGISTRY_DIR=str(blocker / "registry"))

    run = rig.script("register", environment=environment)

    assert run.code == 1 and "SAF_AGENTIC_REGISTRY_DIR" in run.stderr


def test_the_stable_launcher_without_a_registered_harness_fails_with_the_remedy(
    rig: PluginRig,
) -> None:
    rig.register()
    (rig.registry / "harness" / "latest").unlink()

    run = rig.script("launcher", "userPromptSubmitted", "--framework", FRAMEWORK, "--agent",
                     ORCHESTRATOR, payload=rig.payload(), folder=rig.registry / "harness",
                     environment=rig.no_wait())

    assert run.code != 0 and "harness plugin is not registered" in run.stderr


def test_the_stable_launcher_tells_an_unmarked_folder_before_the_registry_is_populated(
    rig: PluginRig,
) -> None:
    """The first prompt can run before `sessionStart` registers: no hard failure outside a
    workspace, the guidance instead, and the orchestrator's intent still recorded."""
    rig.register()
    (rig.registry / "harness" / "latest").unlink()
    launcher = rig.registry / "harness"
    arguments = ("--framework", FRAMEWORK, "--agent", ORCHESTRATOR)

    submitted = rig.script("launcher", "userPromptSubmitted", *arguments,
                           payload=rig.payload(rig.elsewhere, prompt="hi"), folder=launcher,
                           environment=rig.no_wait())
    transformed = rig.script("launcher", "userPromptTransformed", *arguments,
                             payload=rig.payload(rig.elsewhere, prompt="hi",
                                                 transformedPrompt='say "hi"\nthen\\stop'),
                             folder=launcher, environment=rig.no_wait())

    assert (submitted.code, submitted.stdout.strip()) == (0, "")
    assert transformed.code == 0
    assert transformed.decision["modifiedTransformedPrompt"] == (
        f'{NOT_A_WORKSPACE}\n\nsay "hi"\nthen\\stop')
    assert (rig.registry / "intents" / HOST).read_text("utf-8").strip() == ORCHESTRATOR


def test_the_stable_launcher_waits_for_a_registration_in_flight(rig: PluginRig) -> None:
    rig.register()
    latest = rig.registry / "harness" / "latest"
    version = latest.read_text("utf-8")
    latest.unlink()
    timer = threading.Timer(1.5, latest.write_text, (version,), {"encoding": "utf-8"})
    timer.start()
    try:
        run = rig.script("launcher", "userPromptTransformed", "--framework", FRAMEWORK,
                         "--agent", ORCHESTRATOR,
                         payload=rig.payload(rig.elsewhere, prompt="go", transformedPrompt="go",
                                             timestamp=1),
                         folder=rig.registry / "harness",
                         environment=dict(rig.environment, SAF_AGENTIC_REGISTRY_WAIT_SEC="20"))
    finally:
        timer.join()

    assert run.code == 0
    assert run.decision["modifiedTransformedPrompt"].startswith(NOT_A_WORKSPACE)


def test_an_unpopulated_framework_entry_fails_closed_in_a_workspace(rig: PluginRig) -> None:
    shutil.rmtree(rig.registry / "frameworks")

    run = rig.launcher("preToolUse", rig.shell_call("echo hi"))

    assert run.code == 0
    assert "the SAF plugins are not registered yet: start a new session" in str(run.decision)


# A governed session, end to end ---------------------------------------------------------------


def test_a_governed_session_runs_end_to_end_through_the_plugin(rig: PluginRig) -> None:
    """Registration, the announce, H0 through the stable launcher, a hook-run harness request,
    and the closure that drops the session's registry entries."""
    assert rig.hook("sessionStart", rig.payload(), index=0).code == 0
    announce = rig.hook("sessionStart", rig.payload(source="new"))
    assert announce.code == 0, announce.stderr
    assert "SAFe agentic harness" in announce.decision["additionalContext"]
    assert (rig.registry / "sessions" / HOST).read_text("utf-8").strip() == rig.version

    h0 = rig.script("launcher", "userPromptSubmitted", "--framework", FRAMEWORK, "--agent",
                    ORCHESTRATOR, payload=rig.payload(prompt="go", timestamp=1),
                    folder=rig.registry / "harness")
    assert h0.code == 0, h0.stderr
    assert (rig.registry / "intents" / HOST).is_file()
    record = json.loads(
        (rig.data / "state" / "github-copilot" / "sessions" / f"{HOST}.json").read_text("utf-8")
    )
    assert os.path.samefile(record["workspace"], rig.workspace)
    assert record["harness_version"] == rig.version

    resolved = rig.hook("preToolUse", rig.harness_request())
    assert resolved.code == 0, resolved.stderr
    reason = resolved.decision["permissionDecisionReason"]
    assert reason.startswith("SAFe agentic harness RESULT (resolve-step)")
    assert json.loads(reason.split("\n\n", 1)[1])["outcome"]["status"] == "step-resolution"

    assert rig.hook("sessionEnd", rig.payload(reason="complete")).code == 0
    assert not (rig.registry / "sessions" / HOST).exists()
    assert not (rig.registry / "intents" / HOST).exists()


def test_a_later_firing_keeps_the_sessions_recorded_workspace(rig: PluginRig) -> None:
    """Adapter spec, Workspace resolution: fixed per session, never re-resolved."""
    rig.register()
    assert rig.launcher("userPromptSubmitted", rig.payload(prompt="go", timestamp=1),
                        "--framework", FRAMEWORK, "--agent", ORCHESTRATOR).code == 0

    moved = rig.launcher("preToolUse", rig.harness_request(folder=rig.elsewhere))

    assert moved.decision is not None
    assert moved.decision["permissionDecisionReason"].startswith("SAFe agentic harness RESULT")


# Fail-closed scope -----------------------------------------------------------------------------


def test_an_ungoverned_folder_passes_even_without_the_runtime(rig: PluginRig) -> None:
    for event, payload in (
        ("preToolUse", rig.shell_call("echo hi", folder=rig.elsewhere)),
        ("subagentStart", rig.payload(rig.elsewhere, agentId="a1", agentType="x")),
        ("postToolUse", rig.payload(rig.elsewhere, toolName="bash", toolArgs={})),
    ):
        run = rig.hook(event, payload, environment=rig.without_runtime())
        assert (run.code, run.stdout) == (0, ""), (event, run.stderr)


def test_session_start_outside_a_workspace_announces_nothing(rig: PluginRig) -> None:
    run = rig.launcher("sessionStart", rig.payload(rig.elsewhere))

    assert (run.code, run.stdout) == (0, "")


def test_session_start_warns_once_when_the_runtime_cannot_be_provisioned(
    rig: PluginRig,
) -> None:
    """The shipped lock pins nothing installable here: provisioning fails, with the remedy."""
    environment = dict(rig.without_runtime(), PATH=os.environ.get("PATH", ""),
                       PIP_INDEX_URL="http://127.0.0.1:9/simple", PIP_RETRIES="0",
                       UV_INDEX_URL="http://127.0.0.1:9/simple")
    (rig.plugin / "requirements.lock").write_text(
        "pyyaml==6.0.2 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8"
    )

    run = rig.launcher("sessionStart", rig.payload(rig.elsewhere), environment=environment)
    assert run.code == 1 and "could not be provisioned" not in run.stderr
    assert "PIP_INDEX_URL / UV_INDEX_URL" in run.stderr

    denied = rig.launcher("preToolUse", rig.shell_call("echo hi"), environment=environment)
    assert "could not be provisioned" in denied.decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command", ["echo hi", "cat report/x.json"], ids=["any-call", "shell-write-guard"]
)
def test_a_workspace_denies_every_call_while_the_runtime_is_missing(
    rig: PluginRig, command: str
) -> None:
    run = rig.hook("preToolUse", rig.shell_call(command), environment=rig.without_runtime())

    assert run.code == 0
    assert run.decision["permissionDecision"] == "deny"
    assert "not provisioned yet" in run.decision["permissionDecisionReason"]


def test_an_orchestrator_selected_outside_a_workspace_is_told_and_its_intent_recorded(
    rig: PluginRig,
) -> None:
    transformed = rig.launcher(
        "userPromptTransformed",
        rig.payload(rig.elsewhere, prompt="go", transformedPrompt="go", timestamp=1),
        "--framework", FRAMEWORK, "--agent", ORCHESTRATOR,
    )

    assert transformed.code == 0
    assert transformed.decision["modifiedTransformedPrompt"].startswith(NOT_A_WORKSPACE)
    assert (rig.registry / "intents" / HOST).read_text("utf-8").strip() == ORCHESTRATOR
    assert not (rig.data / "state" / "github-copilot" / "sessions" / f"{HOST}.json").exists()

    allowed = rig.launcher("preToolUse", rig.shell_call("echo hi", folder=rig.elsewhere))
    assert (allowed.code, allowed.stdout) == (0, "")
    denied = rig.launcher("preToolUse", rig.shell_call("echo hi", folder=rig.elsewhere),
                          environment=rig.without_runtime())
    assert "not provisioned yet" in denied.decision["permissionDecisionReason"]


def test_a_workspace_of_another_framework_counts_as_unmarked_for_this_one(
    rig: PluginRig,
) -> None:
    run = rig.launcher(
        "userPromptTransformed",
        rig.payload(prompt="go", transformedPrompt="go", timestamp=1),
        "--framework", "another-framework", "--agent", ORCHESTRATOR,
    )

    assert run.decision["modifiedTransformedPrompt"].startswith(NOT_A_WORKSPACE)


@pytest.mark.parametrize(
    ("marker", "commit", "reason"),
    [
        (f"framework: {FRAMEWORK}\nroot: report\n", False, "is not committed"),
        ("framework: Not A Slug\nroot: report\n", True, "is invalid"),
    ],
    ids=["uncommitted", "off-contract"],
)
def test_an_unusable_marker_fails_closed(
    rig: PluginRig, marker: str, commit: bool, reason: str
) -> None:
    _git(rig.workspace, "rm", "-q", "--cached", ".saf/workspace.yaml")
    _git(rig.workspace, "commit", "-q", "-m", "unmark")
    rig.write_marker(marker, commit=commit)

    run = rig.launcher("preToolUse", rig.shell_call("echo hi"))

    assert reason in run.decision["permissionDecisionReason"]


def test_a_marker_root_outside_the_workspace_layout_fails_closed(rig: PluginRig) -> None:
    rig.write_marker(f"framework: {FRAMEWORK}\nroot: elsewhere\n")

    run = rig.launcher("preToolUse", rig.shell_call("echo hi"))

    assert run.code == 2 and "not a top-level node" in run.stderr


@pytest.mark.skipif(IS_WINDOWS, reason="a stand-in git is a POSIX shell script")
def test_an_old_git_fails_closed_with_the_version_found(rig: PluginRig, tmp_path: Path) -> None:
    fake = tmp_path / "old-git"
    fake.mkdir()
    real_git = shutil.which("git")
    (fake / "git").write_text(
        f'#!/bin/sh\n[ "$1" = --version ] && echo "git version 2.30.0" && exit 0\n'
        f'exec "{real_git}" "$@"\n', encoding="utf-8",
    )
    (fake / "git").chmod(0o755)
    environment = dict(rig.environment, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}")

    run = rig.launcher("preToolUse", rig.shell_call("echo hi"), environment=environment)

    assert run.decision["permissionDecisionReason"].endswith(
        "Git >= 2.38 required (found 2.30.0)"
    )


# The Local runtime and version routing ---------------------------------------------------------


def test_a_local_runtime_payload_is_refused_in_a_workspace_only(rig: PluginRig) -> None:
    local = {"session_id": HOST, "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "echo hi"}}

    refused = rig.hook("preToolUse", dict(local, cwd=str(rig.workspace)))
    passed = rig.hook("preToolUse", dict(local, cwd=str(rig.elsewhere)))

    assert refused.code != 0 and "select Session Target: Copilot" in refused.stderr
    assert (passed.code, passed.stdout) == (0, "")


def test_a_firing_is_routed_to_the_version_that_serves_its_session(
    rig: PluginRig, tmp_path: Path
) -> None:
    other = tmp_path / "other-version"
    other.mkdir()
    (other / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (other / "launcher.py").write_text(
        "import os, sys\nsys.stdin.read()\n"
        "print('routed', os.environ['PLUGIN_ROOT'], os.environ.get('PLUGIN_DATA'))\n",
        encoding="utf-8",
    )
    (rig.registry / "harness" / "9.9.9").mkdir(parents=True)
    (rig.registry / "harness" / "9.9.9" / "root").write_text(f"{other}\n", encoding="utf-8")
    (rig.registry / "harness" / "9.9.9" / "data").write_text("/other-data\n", encoding="utf-8")
    (rig.registry / "sessions").mkdir()
    (rig.registry / "sessions" / HOST).write_text("9.9.9\n", encoding="utf-8")

    run = rig.launcher("preToolUse", rig.shell_call("echo hi"))

    assert run.stdout.split() == ["routed", str(other), "/other-data"]


# Without Python ----------------------------------------------------------------------------------


@pytest.fixture
def no_python(rig: PluginRig, tmp_path: Path) -> dict[str, str]:
    """An environment whose PATH reaches the shell tools but no Python interpreter."""
    environment = rig.without_runtime()
    if IS_WINDOWS:
        system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
        environment["PATH"] = os.pathsep.join(
            (str(system), str(system / "WindowsPowerShell" / "v1.0"))
        )
        return environment
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("cat", "sed", "tr", "grep", "dirname", "mkdir", "head", "mv", "cp", "sh"):
        located = shutil.which(name)
        if located:
            (tools / name).symlink_to(located)
    environment["PATH"] = str(tools)
    return environment


def test_without_python_the_shim_decides_alone(rig: PluginRig, no_python: dict[str, str]) -> None:
    marked = rig.script("saf-hook", "preToolUse", payload=rig.shell_call("echo hi"),
                        environment=no_python)
    unmarked = rig.script("saf-hook", "preToolUse",
                          payload=rig.shell_call("echo hi", folder=rig.elsewhere),
                          environment=no_python)
    started = rig.script("saf-hook", "sessionStart", payload=rig.payload(rig.elsewhere),
                         environment=no_python)

    assert marked.code == 2 and "Python >= 3.10 was not found" in marked.stderr
    assert (unmarked.code, unmarked.stdout) == (0, "")
    assert started.code == 1 and "SAF_HARNESS_PYTHON" in started.stderr


def test_without_python_a_selected_orchestrator_still_fails_closed(
    rig: PluginRig, no_python: dict[str, str]
) -> None:
    payload = rig.payload(rig.elsewhere, prompt="go", timestamp=1)
    assert rig.script("saf-hook", "userPromptSubmitted", "--framework", FRAMEWORK, "--agent",
                      ORCHESTRATOR, payload=payload, environment=no_python).code == 0
    assert (rig.registry / "intents" / HOST).is_file()

    run = rig.script("saf-hook", "preToolUse",
                     payload=rig.shell_call("echo hi", folder=rig.elsewhere),
                     environment=no_python)

    assert run.code == 2


def test_without_python_a_recorded_worker_session_still_fails_closed(
    rig: PluginRig, no_python: dict[str, str]
) -> None:
    """A worker the adapter recorded keeps its session governed even when its own folder is
    unmarked: the shim consults the session's adapter record."""
    worker = "ecdda9f8-7300-43cc-9fae-ad08c0456604"
    record = rig.data / "state" / "github-copilot" / "sessions" / f"{worker}.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"workspace": str(rig.workspace)}), encoding="utf-8")

    run = rig.script("saf-hook", "preToolUse",
                     payload=dict(rig.shell_call("echo hi", folder=rig.elsewhere),
                                  sessionId=worker),
                     environment=no_python)

    assert run.code == 2


@pytest.mark.skipif(IS_WINDOWS, reason="JSON-escaped POSIX paths")
def test_without_python_a_json_escaped_folder_is_still_scanned(
    rig: PluginRig, no_python: dict[str, str]
) -> None:
    escaped = json.dumps(str(rig.workspace)).replace("/", "\\/")[1:-1]
    payload = '{"sessionId": "s1", "cwd": "%s", "toolCalls": []}' % escaped
    done = subprocess.run(
        ["sh", str(rig.plugin / "bin" / "saf-hook.sh"), "preToolUse"],
        input=payload.encode("utf-8"), capture_output=True,
        env=child_environment(no_python), cwd=rig.elsewhere, timeout=60,
    )

    assert done.returncode == 2


def test_an_uppercase_session_id_keys_the_same_records_everywhere(
    rig: PluginRig, no_python: dict[str, str]
) -> None:
    """The launcher, the shims and the adapter name a host session's files alike."""
    upper = HOST.upper()
    assert rig.launcher("userPromptSubmitted",
                        dict(rig.payload(prompt="go", timestamp=1), sessionId=upper),
                        "--framework", FRAMEWORK, "--agent", ORCHESTRATOR).code == 0
    assert (rig.registry / "intents" / HOST).is_file()
    assert (rig.data / "state" / "github-copilot" / "sessions" / f"{HOST}.json").is_file()

    run = rig.script("saf-hook", "preToolUse",
                     payload=dict(rig.shell_call("echo hi", folder=rig.elsewhere),
                                  sessionId=upper),
                     environment=no_python)

    assert run.code == 2


@pytest.mark.skipif(IS_WINDOWS, reason="the PowerShell shim parses the payload as JSON")
def test_without_python_a_nested_member_never_stands_in_for_the_top_level_one(
    rig: PluginRig, no_python: dict[str, str]
) -> None:
    (rig.registry / "intents").mkdir(parents=True)
    (rig.registry / "intents" / HOST).write_text("orchestrator\n", encoding="utf-8")
    payload = dict(rig.shell_call("echo hi", folder=rig.elsewhere))
    payload["toolCalls"][0]["args"].update(sessionId="someone-else", cwd=str(rig.elsewhere))

    run = rig.script("saf-hook", "preToolUse", payload=payload, environment=no_python)

    assert run.code == 2

