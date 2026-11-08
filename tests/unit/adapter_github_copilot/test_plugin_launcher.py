"""The plugin launcher's own decisions (adapter spec, Plugin mode), function by function."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cli_rig import ADAPTER_DIR
from contract_assertions import contract_validator

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "github_copilot_plugin_launcher", ADAPTER_DIR / "plugin" / "launcher.py"
)
assert _SPEC is not None and _SPEC.loader is not None
LAUNCHER = importlib.util.module_from_spec(_SPEC)
sys.modules["github_copilot_plugin_launcher"] = LAUNCHER
_SPEC.loader.exec_module(LAUNCHER)

MARKER_CONTRACT = "gsmarc://saf/contracts/conf/workspace-marker/v1"


@pytest.mark.parametrize(
    "text",
    [
        "framework: poesis-saf\nroot: portfolio\n",
        "---\n# a comment\nframework: 'poesis-saf'  # trailing\nroot: \"portfolio\"\n",
    ],
)
def test_a_valid_marker_parses_as_the_contract_reads_it(text: str) -> None:
    parsed = LAUNCHER.parse_marker(text)

    assert parsed == {"framework": "poesis-saf", "root": "portfolio"}
    assert not contract_validator().validate_instance(MARKER_CONTRACT, parsed)


@pytest.mark.parametrize(
    ("text", "instance"),
    [
        ("framework: poesis-saf\n", {"framework": "poesis-saf"}),
        ("framework: Poesis\nroot: portfolio\n", {"framework": "Poesis", "root": "portfolio"}),
        (
            "framework: a\nroot: b\nextra: c\n",
            {"framework": "a", "root": "b", "extra": "c"},
        ),
    ],
    ids=["missing-root", "not-a-slug", "extra-property"],
)
def test_an_off_contract_marker_is_refused_like_the_contract_refuses_it(
    text: str, instance: dict
) -> None:
    with pytest.raises(ValueError):
        LAUNCHER.parse_marker(text)
    assert contract_validator().validate_instance(MARKER_CONTRACT, instance)


def test_a_duplicated_or_malformed_line_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        LAUNCHER.parse_marker("framework: a\nframework: b\nroot: c\n")
    with pytest.raises(ValueError, match="key: value"):
        LAUNCHER.parse_marker("framework a\n")


@pytest.mark.parametrize(
    ("payload", "local"),
    [
        ({"sessionId": "s", "toolName": "bash", "toolArgs": {}}, False),
        ({"sessionId": "s", "toolCalls": []}, False),
        ({"session_id": "s", "tool_name": "Bash", "tool_input": {}}, True),
        ({"hookEventName": "PreToolUse"}, True),
        ({"hook_event_name": "PreToolUse"}, True),
    ],
)
def test_the_local_runtime_is_told_apart_by_its_payload_family(
    payload: dict, local: bool
) -> None:
    """Adapter spec, host fact 10."""
    assert LAUNCHER.is_local_runtime(payload) is local


def test_the_registry_folder_follows_the_override_then_the_platform() -> None:
    assert LAUNCHER.registry_dir({"SAF_AGENTIC_REGISTRY_DIR": "/r"}) == Path("/r")
    if LAUNCHER.os.name != "nt":
        assert LAUNCHER.registry_dir({"XDG_STATE_HOME": "/x"}) == Path(
            "/x/saf-agentic/registry"
        )
    else:
        assert LAUNCHER.registry_dir({"LOCALAPPDATA": "C:\\L"}) == Path(
            "C:\\L\\saf-agentic\\registry"
        )


def test_registry_entries_are_one_line_and_written_by_rename(tmp_path: Path) -> None:
    entry = tmp_path / "harness" / "latest"
    LAUNCHER.write_entry(entry, "1.2.3")

    assert entry.read_text(encoding="utf-8") == "1.2.3\n"
    assert LAUNCHER.read_entry(entry) == "1.2.3"
    assert [path.name for path in entry.parent.iterdir()] == ["latest"]
    assert LAUNCHER.read_entry(tmp_path / "absent") is None


@pytest.mark.parametrize("absent", ["", 7, None])
def test_an_absent_host_session_id_names_no_registry_entry(absent: object) -> None:
    assert LAUNCHER.safe_id(absent) is None


@pytest.mark.parametrize(
    ("raw", "named"),
    [("../x", "---x"), ("A/b\\C", "a-b-c"), (".hidden", "-hidden"),
     ("1D793D07-ADC6", "1d793d07-adc6")],
)
def test_a_host_session_id_is_named_as_the_adapter_names_its_records(
    raw: str, named: str
) -> None:
    """The launcher, the shims and the adapter's tracker key one host session alike."""
    from github_copilot_adapter.payload import sanitize_identifier

    assert LAUNCHER.safe_id(raw) == named == sanitize_identifier(raw)


class TestProvisioning:
    """Adapter spec, Python provisioning."""

    @pytest.fixture
    def plugin_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = tmp_path / "plugin"
        root.mkdir()
        (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        (root / "requirements.lock").write_text("lock-1\n", encoding="utf-8")
        monkeypatch.setattr(LAUNCHER, "PLUGIN_ROOT", root)
        monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
        monkeypatch.delenv("SAF_HARNESS_PYTHON", raising=False)
        return root

    def test_the_environment_is_keyed_by_version_lock_abi_and_platform(
        self, plugin_root: Path
    ) -> None:
        first = LAUNCHER.environment_key()
        (plugin_root / "requirements.lock").write_text("lock-2\n", encoding="utf-8")

        assert first.startswith("1.2.3-") and first != LAUNCHER.environment_key()
        assert f"{sys.version_info[0]}{sys.version_info[1]}" in first

    def test_the_ready_stamp_is_written_last_and_reused(
        self, plugin_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[Path] = []

        def build(venv: Path) -> None:
            assert not (venv / LAUNCHER.READY_STAMP).exists()
            built.append(venv)
            venv.mkdir(parents=True)

        monkeypatch.setattr(LAUNCHER, "build_environment", build)

        first = LAUNCHER.provision()
        second = LAUNCHER.provision()

        assert len(built) == 1 and first == second
        assert (built[0] / LAUNCHER.READY_STAMP).read_text(encoding="utf-8") == "1.2.3\n"
        assert LAUNCHER.runtime_python() == first

    def test_until_ready_the_runtime_is_unavailable_with_the_remedy(
        self, plugin_root: Path
    ) -> None:
        with pytest.raises(LAUNCHER.Unavailable, match="not provisioned yet"):
            LAUNCHER.runtime_python()

    def test_a_failed_provisioning_is_recorded_and_named(
        self, plugin_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def build(venv: Path) -> None:
            raise LAUNCHER.Unavailable("no network")

        monkeypatch.setattr(LAUNCHER, "build_environment", build)

        with pytest.raises(LAUNCHER.Unavailable):
            LAUNCHER.provision()
        with pytest.raises(LAUNCHER.Unavailable, match="could not be provisioned: no network"):
            LAUNCHER.runtime_python()

    def test_an_environment_without_its_stamp_is_rebuilt(
        self, plugin_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        venv = LAUNCHER.environment_dir()
        (venv / "half-built").mkdir(parents=True)
        monkeypatch.setattr(
            LAUNCHER, "installer_steps", lambda venv, lock: [[sys.executable, "-c", "pass"]]
        )
        monkeypatch.setattr(LAUNCHER, "check_imports", lambda python: None)

        LAUNCHER.provision()

        assert not (venv / "half-built").exists()
        assert (venv / LAUNCHER.READY_STAMP).is_file()

    def test_the_installers_require_hashes_and_binaries(
        self, plugin_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        lock = plugin_root / "requirements.lock"
        for uv in ("/bin/uv", None):
            monkeypatch.setattr(LAUNCHER.shutil, "which", lambda name, uv=uv: uv)
            install = LAUNCHER.installer_steps(tmp_path / "venv", lock)[-1]
            assert "--require-hashes" in install and str(lock) in install
            assert "--only-binary=:all:" in install or (
                "--only-binary" in install and ":all:" in install
            )

    def test_an_installer_failure_names_the_mirror_remedy(
        self, plugin_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            LAUNCHER,
            "installer_steps",
            lambda venv, lock: [[sys.executable, "-c", "import sys; sys.exit('offline')"]],
        )

        with pytest.raises(LAUNCHER.Unavailable, match="PIP_INDEX_URL / UV_INDEX_URL"):
            LAUNCHER.build_environment(tmp_path / "venv")

    def test_saf_harness_python_skips_provisioning(
        self, plugin_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAF_HARNESS_PYTHON", sys.executable)
        monkeypatch.setattr(LAUNCHER, "build_environment", lambda venv: pytest.fail("built"))
        monkeypatch.setattr(LAUNCHER, "check_imports", lambda python: None)

        assert LAUNCHER.provision() == Path(sys.executable)
        assert LAUNCHER.runtime_python() == Path(sys.executable)


def test_the_deny_is_the_adapters_structured_shape() -> None:
    import io

    out = io.StringIO()
    assert LAUNCHER.deny("Git >= 2.38 required (found 2.30.0)", out) == 0
    assert json.loads(out.getvalue()) == {
        "permissionDecision": "deny",
        "permissionDecisionReason": "SAFe agentic harness: Git >= 2.38 required (found 2.30.0)",
    }
