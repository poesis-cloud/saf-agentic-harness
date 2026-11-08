#!/usr/bin/env python3
"""The harness plugin's launcher: stdlib only, run by the `bin/saf-hook` shims.

    <python> launcher.py <event> [--framework <name>] [--agent <slug>]   (payload on stdin)

Adapter spec, Plugin mode. Before the adapter ever runs, the launcher:

- tells the payload families apart and refuses VS Code's Local runtime in a governance
  context ("select Session Target: Copilot");
- hands the firing to the harness version the registry records for the host session;
- records the host session's version (`sessionStart`, H1) and, at a scoped H0, its intent
  record — before anything else can fail;
- resolves the governed workspace from the session folder's committed
  `.saf/workspace.yaml`, and the framework from the registry;
- provisions the runtime at `sessionStart`, never at any other event;
- fails closed only in a governance context, with the exact remedy.

It then runs the provisioned interpreter on the adapter. It imports nothing outside the
standard library, so it decides even when the runtime is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT") or Path(__file__).resolve().parent)
ADAPTER = Path("adapters") / "github-copilot" / "adapter.py"
MARKER = Path(".saf") / "workspace.yaml"
REQUIREMENTS_LOCK = "requirements.lock"
REQUIRED_MODULES = ("yaml", "jsonschema", "celpy", "packaging")
MINIMUM_GIT = (2, 38)
READY_STAMP = ".ready"
ROUTED_VARIABLE = "SAF_HARNESS_ROUTED"
REGISTRY_VARIABLE = "SAF_AGENTIC_REGISTRY_DIR"
STATE_VARIABLE = "SAF_HARNESS_STATE_DIR"
PYTHON_VARIABLE = "SAF_HARNESS_PYTHON"
LOCAL_RUNTIME_KEYS = ("tool_name", "tool_input", "session_id", "hookEventName", "hook_event_name")
SCOPED_EVENTS = frozenset({"userPromptSubmitted", "userPromptTransformed"})
SESSION_START = "sessionStart"
GATING_EVENT = "preToolUse"
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

NOT_A_WORKSPACE = "this folder is not a SAF workspace; ask me to initialise it"
SELECT_COPILOT = "select Session Target: Copilot"
NOT_REGISTERED = "the SAF plugins are not registered yet: start a new session"
PROVISION_PENDING = (
    "the SAF harness runtime is not provisioned yet (see the sessionStart warning): "
    "start a new session"
)


class Unavailable(Exception):
    """The runtime cannot serve this firing; the message is the remedy."""


# Registry ----------------------------------------------------------------------------------


def registry_dir(environment: Mapping[str, str] | None = None) -> Path:
    """`SAF_AGENTIC_REGISTRY_DIR`, else `%LOCALAPPDATA%` (Windows) or `$XDG_STATE_HOME`."""
    environment = os.environ if environment is None else environment
    explicit = environment.get(REGISTRY_VARIABLE)
    if explicit:
        return Path(explicit)
    if os.name == "nt" and environment.get("LOCALAPPDATA"):
        return Path(environment["LOCALAPPDATA"]) / "saf-agentic" / "registry"
    state_home = environment.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "saf-agentic" / "registry"


def read_entry(path: Path) -> str | None:
    """One registry entry: a one-line UTF-8 file, surrounding whitespace stripped."""
    try:
        value = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return value or None


def write_entry(path: Path, value: str) -> None:
    """Write through a temporary file in the same folder, then rename over the entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def safe_id(value: Any) -> str | None:
    """A host session id usable as a file name, or None."""
    return value if isinstance(value, str) and SAFE_ID.match(value) else None


def is_local_runtime(payload: Mapping[str, Any]) -> bool:
    """Adapter spec, host fact 10: VS Code's Local runtime sends snake_case payloads."""
    return any(key in payload for key in LOCAL_RUNTIME_KEYS)


# Workspace ---------------------------------------------------------------------------------


def git(arguments: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )


def require_git() -> None:
    """Git >= 2.38 (`git merge-tree --write-tree`), or the remedy."""
    if shutil.which("git") is None:
        raise Unavailable("Git is not installed: install Git >= 2.38, then start a new session")
    completed = git(["--version"])
    found = re.search(r"(\d+)\.(\d+)", completed.stdout)
    version = (int(found.group(1)), int(found.group(2))) if found else (0, 0)
    if version < MINIMUM_GIT:
        shown = completed.stdout.strip().removeprefix("git version ") or "unknown"
        raise Unavailable(f"Git >= 2.38 required (found {shown})")


def find_marker_file(folder: Path) -> Path | None:
    """The nearest folder at or above `folder` holding `.saf/workspace.yaml`, by file tests."""
    for candidate in (folder, *folder.parents):
        if (candidate / MARKER).is_file():
            return candidate
    return None


def parse_marker(text: str) -> dict[str, str]:
    """Read the marker's two slug properties (contracts/conf/workspace-marker.schema.json)."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        line = stripped.split(" #", 1)[0].strip()
        key, separator, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not separator or not key:
            raise ValueError(f"line {number} is not 'key: value'")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in values:
            raise ValueError(f"'{key}' is declared twice")
        values[key] = value
    unknown = sorted(set(values) - {"framework", "root"})
    if unknown:
        raise ValueError(f"unknown properties: {', '.join(unknown)}")
    for key in ("framework", "root"):
        if key not in values:
            raise ValueError(f"'{key}' is required")
        if not SLUG.match(values[key]):
            raise ValueError(f"'{key}' is not a slug: {values[key]!r}")
    return values


class Workspace:
    """The SAF workspace a session folder belongs to: its Git top-level and its marker."""

    def __init__(self, toplevel: Path, framework: str, root: str) -> None:
        self.toplevel = toplevel
        self.framework = framework
        self.root = root


def resolve_workspace(folder: Path) -> Workspace | None:
    """Adapter spec, Workspace resolution: the Git top-level holding a COMMITTED marker.

    None for an unmarked folder. Raises `Unavailable` when a marker is there but cannot be
    used: Git missing or too old, the marker not committed, or off its contract.
    """
    if find_marker_file(folder) is None:
        return None
    require_git()
    toplevel = git(["rev-parse", "--show-toplevel"], cwd=folder)
    if toplevel.returncode != 0:
        return None
    top = Path(toplevel.stdout.strip())
    if not (top / MARKER).is_file():
        return None
    committed = git(["show", f"HEAD:{MARKER.as_posix()}"], cwd=top)
    if committed.returncode != 0:
        raise Unavailable(
            f"{top / MARKER} is not committed: commit it to make this folder a SAF workspace"
        )
    try:
        marker = parse_marker(committed.stdout)
    except ValueError as failure:
        raise Unavailable(f"the SAF workspace marker {top / MARKER} is invalid: {failure}")
    return Workspace(top, marker["framework"], marker["root"])


# Provisioning ------------------------------------------------------------------------------


def read_version(root: Path | None = None) -> str:
    return ((root or PLUGIN_ROOT) / "VERSION").read_text(encoding="utf-8").strip()


def data_dir(environment: Mapping[str, str] | None = None) -> Path:
    """`PLUGIN_DATA`, else a per-user folder beside the registry."""
    environment = os.environ if environment is None else environment
    explicit = environment.get("PLUGIN_DATA")
    if explicit:
        return Path(explicit)
    return registry_dir(environment).parent / "harness-data"


def environment_key() -> str:
    """`<version>-<lock sha256[:12]>-<Python ABI tag>-<platform tag>`."""
    lock = PLUGIN_ROOT / REQUIREMENTS_LOCK
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()[:12] if lock.is_file() else "nolock"
    abi = f"{sys.implementation.name}{sys.version_info[0]}{sys.version_info[1]}"
    platform = re.sub(r"[^A-Za-z0-9]+", "_", sysconfig.get_platform())
    return f"{read_version()}-{digest}-{abi}-{platform}"


def environment_dir() -> Path:
    return data_dir() / "venv" / environment_key()


def environment_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def runtime_python() -> Path:
    """The interpreter the adapter runs under, or the remedy when it is not ready."""
    if os.environ.get(PYTHON_VARIABLE):
        return Path(sys.executable)
    venv = environment_dir()
    if not (venv / READY_STAMP).is_file():
        failure = read_entry(venv.with_name(venv.name + ".failed"))
        raise Unavailable(
            f"the SAF harness runtime could not be provisioned: {failure}"
            if failure
            else PROVISION_PENDING
        )
    return environment_python(venv)


def provision() -> Path:
    """Adapter spec, Python provisioning: build the keyed environment once, `.ready` last."""
    if os.environ.get(PYTHON_VARIABLE):
        check_imports(Path(sys.executable))
        return Path(sys.executable)
    venv = environment_dir()
    failed = venv.with_name(venv.name + ".failed")
    with ExclusiveLock(venv.with_name(venv.name + ".lock")):
        if (venv / READY_STAMP).is_file():
            return environment_python(venv)
        try:
            build_environment(venv)
        except Unavailable as failure:
            write_entry(failed, str(failure))
            raise
        failed.unlink(missing_ok=True)
        write_entry(venv / READY_STAMP, read_version())
    return environment_python(venv)


def installer_steps(venv: Path, lock: Path) -> list[list[str]]:
    """`uv` when it is on PATH, else `venv` + `pip`; hashes required, binaries only."""
    uv = shutil.which("uv")
    if uv:
        return [
            [uv, "venv", "--quiet", "--python", sys.executable, str(venv)],
            [uv, "pip", "install", "--quiet", "--python", str(environment_python(venv)),
             "--require-hashes", "--only-binary", ":all:", "-r", str(lock)],
        ]
    return [
        [sys.executable, "-m", "venv", str(venv)],
        [str(environment_python(venv)), "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check", "--require-hashes", "--only-binary=:all:",
         "-r", str(lock)],
    ]


def build_environment(venv: Path) -> None:
    lock = PLUGIN_ROOT / REQUIREMENTS_LOCK
    if not lock.is_file():
        raise Unavailable(f"the harness plugin ships no {REQUIREMENTS_LOCK}")
    shutil.rmtree(venv, ignore_errors=True)
    child = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    for step in installer_steps(venv, lock):
        completed = subprocess.run(
            step, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=child, timeout=600,
        )
        if completed.returncode != 0:
            detail = " / ".join((completed.stderr or completed.stdout).strip().splitlines()[-3:])
            raise Unavailable(
                f"installing the harness requirements for Python {platform_python()} failed "
                f"({Path(step[0]).name}): {detail or 'no output'}. Check the network, or set "
                f"PIP_INDEX_URL / UV_INDEX_URL; if no wheel fits this interpreter, set "
                f"SAF_HARNESS_PYTHON to another Python >= 3.10; then start a new session"
            )
    check_imports(environment_python(venv))


def platform_python() -> str:
    """This interpreter, as a provisioning failure names it: version and platform."""
    return f"{sys.version.split()[0]} ({sysconfig.get_platform()})"


def check_imports(python: Path) -> None:
    completed = subprocess.run(
        [str(python), "-c", f"import {', '.join(REQUIRED_MODULES)}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if completed.returncode != 0:
        last = (completed.stderr.strip().splitlines() or ["no output"])[-1]
        raise Unavailable(
            f"{python} cannot import the harness requirements "
            f"({', '.join(REQUIRED_MODULES)}): {last}"
        )


class ExclusiveLock:
    """An exclusive OS file lock (`fcntl.flock` / `msvcrt.locking`), polled until free."""

    def __init__(self, path: Path, timeout: float = 900.0) -> None:
        self._path, self._timeout, self._handle = path, timeout, -1

    def __enter__(self) -> "ExclusiveLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                _lock(handle)
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(handle)
                    raise Unavailable(f"another session holds {self._path}: start a new session")
                time.sleep(0.1)
                continue
            self._handle = handle
            return self

    def __exit__(self, *_: object) -> None:
        try:
            _unlock(self._handle)
        finally:
            os.close(self._handle)


def _lock(handle: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)


# Launch ------------------------------------------------------------------------------------


def deny(reason: str, out: Any) -> int:
    """A structured `preToolUse` denial carrying the remedy."""
    out.write(json.dumps({
        "permissionDecision": "deny",
        "permissionDecisionReason": f"SAFe agentic harness: {reason}",
    }) + "\n")
    out.flush()
    return 0


def warn(reason: str, err: Any) -> int:
    """A `sessionStart` warning: a non-zero exit the host shows the user, never a veto."""
    err.write(f"[saf-agentic-harness] {reason}\n")
    err.flush()
    return 1


def parse_arguments(argv: Sequence[str]) -> tuple[str, str | None, str | None]:
    if not argv:
        raise ValueError("usage: launcher.py <event> [--framework <name>] [--agent <slug>]")
    event, rest = argv[0], list(argv[1:])
    options: dict[str, str] = {}
    while rest:
        flag = rest.pop(0)
        if flag not in ("--framework", "--agent") or not rest:
            raise ValueError(f"unexpected argument {flag!r}")
        options[flag] = rest.pop(0)
    return event, options.get("--framework"), options.get("--agent")


def route(argv: Sequence[str], raw: bytes, host_id: str | None, registry: Path,
          version: str, out: Any, err: Any) -> int | None:
    """Hand the firing to the harness version the registry records for the host session."""
    if os.environ.get(ROUTED_VARIABLE) or host_id is None:
        return None
    recorded = read_entry(registry / "sessions" / host_id)
    if not recorded or recorded == version:
        return None
    root = read_entry(registry / "harness" / recorded / "root")
    if not root or not (Path(root) / "launcher.py").is_file():
        return None
    environment = dict(os.environ, PLUGIN_ROOT=root, **{ROUTED_VARIABLE: "1"})
    data = read_entry(registry / "harness" / recorded / "data")
    if data:
        environment["PLUGIN_DATA"] = data
    completed = subprocess.run(
        [sys.executable, str(Path(root) / "launcher.py"), *argv],
        input=raw, capture_output=True, env=environment,
    )
    out.write(completed.stdout.decode("utf-8", "replace"))
    err.write(completed.stderr.decode("utf-8", "replace"))
    return completed.returncode


def state_dir() -> Path:
    """The adapter's state folder: `SAF_HARNESS_STATE_DIR`, else `$PLUGIN_DATA/state`."""
    return Path(os.environ.get(STATE_VARIABLE) or data_dir() / "state")


def recorded_folder(event: str, payload: Mapping[str, Any]) -> Path | None:
    """The workspace the session was fixed to when its adapter record was created.

    Adapter spec, Workspace resolution: later firings never re-resolve it; H1 takes its
    dispatcher's. None when no record names one.
    """
    host_ids = [safe_id(payload.get("sessionId"))]
    if event == "subagentStart":
        host_ids.insert(0, safe_id(payload.get("parentAgentId")))
    for host_id in host_ids:
        if host_id is None:
            continue
        record = state_dir() / "github-copilot" / "sessions" / f"{host_id}.json"
        try:
            workspace = json.loads(record.read_text(encoding="utf-8")).get("workspace")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(workspace, str) and workspace:
            return Path(workspace)
    return None


def adapter_environment() -> dict[str, str]:
    """The adapter derives the layout from the framework's `.env`: no inherited FRAMEWORK_*."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("FRAMEWORK_")
    }
    environment[STATE_VARIABLE] = str(state_dir())
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return environment


def record_session(event: str, payload: Mapping[str, Any], agent: str | None,
                   registry: Path, version: str) -> Path | None:
    """The registry entries a firing writes or deletes; answers the session's intent path."""
    host_id = safe_id(payload.get("sessionId"))
    intent = registry / "intents" / host_id if host_id else None
    if agent and event in SCOPED_EVENTS and intent is not None:
        write_entry(intent, agent)  # before anything else can fail
    if event == SESSION_START and host_id:
        write_entry(registry / "sessions" / host_id, version)
    if event == "subagentStart":
        agent_id = safe_id(payload.get("agentId"))
        if agent_id:
            write_entry(registry / "sessions" / agent_id, version)
    if event == "sessionEnd" and host_id and intent is not None:
        (registry / "sessions" / host_id).unlink(missing_ok=True)
        intent.unlink(missing_ok=True)
        return None
    return intent


def run(argv: Sequence[str], stdin: Any, out: Any, err: Any) -> int:
    event, framework, agent = parse_arguments(argv)
    raw = stdin.read()
    try:
        payload = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    version = read_version()
    registry = registry_dir()

    routed = route(argv, raw, safe_id(payload.get("sessionId")), registry, version, out, err)
    if routed is not None:
        return routed

    intent = record_session(event, payload, agent, registry, version)
    folder = recorded_folder(event, payload) or Path(str(payload.get("cwd") or os.getcwd()))
    unreadable: str | None = None
    try:
        workspace = resolve_workspace(folder)
    except Unavailable as failure:
        workspace, unreadable = None, str(failure)
    marked = workspace is not None or unreadable is not None
    governed = marked or (intent is not None and intent.is_file())

    if is_local_runtime(payload):
        if governed and event == GATING_EVENT:
            err.write(f"SAFe agentic harness: {SELECT_COPILOT}\n")
            return 2
        return 0

    scoped = agent is not None and event in SCOPED_EVENTS
    if scoped and framework and unreadable is None and (
        workspace is None or workspace.framework != framework
    ):
        if event == "userPromptTransformed":
            transformed = payload.get("transformedPrompt")
            prompt = (
                f"{NOT_A_WORKSPACE}\n\n{transformed}"
                if isinstance(transformed, str) and transformed
                else NOT_A_WORKSPACE
            )
            out.write(json.dumps({"modifiedTransformedPrompt": prompt}) + "\n")
        return 0
    if not governed and event != SESSION_START:
        return 0

    try:
        python = provision() if event == SESSION_START else runtime_python()
        require_git()
        if unreadable is not None:
            raise Unavailable(unreadable)
        if workspace is None:
            return 0  # no governance context, or an intent in an unmarked folder
        framework_root = read_entry(registry / "frameworks" / workspace.framework / "root")
        if not framework_root or not Path(framework_root).is_dir():
            raise Unavailable(NOT_REGISTERED)
    except Unavailable as failure:
        if event == GATING_EVENT and governed:
            return deny(str(failure), out)
        if event == SESSION_START:
            return warn(str(failure), err)
        return 0

    command = [
        str(python), str(PLUGIN_ROOT / ADAPTER), "hook", "--event", event,
        "--framework-dir", framework_root, "--workspace-dir", str(workspace.toplevel),
        "--workspace-root", workspace.root,
    ]
    if agent:
        command += ["--agent", agent]
    completed = subprocess.run(command, input=raw, capture_output=True, env=adapter_environment())
    out.write(completed.stdout.decode("utf-8", "replace"))
    err.write(completed.stderr.decode("utf-8", "replace"))
    out.flush()
    err.flush()
    return completed.returncode


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    try:
        return run(sys.argv[1:], sys.stdin.buffer, sys.stdout, sys.stderr)
    except Exception as failure:  # noqa: BLE001 - a defect denies; it never crashes open
        sys.stderr.write(
            f"[saf-agentic-harness] launcher failed: {type(failure).__name__}: {failure}\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
