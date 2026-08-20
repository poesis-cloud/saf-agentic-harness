"""Tests for `Application` — the composition root and the command exit plane."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from application import Application, main
from errors import ConfigurationError

CAPABILITY_KEYS = (
    "deep-reasoning",
    "coding",
    "tool-use",
    "long-context",
    "multimodal",
    "writing-quality",
    "instruction-following",
    "fast-iteration",
    "schema-adherence",
)
FRAMEWORK_ENV_KEYS = (
    "FRAMEWORK_DIR",
    "FRAMEWORK_AGENTS_DIR",
    "FRAMEWORK_ARTIFACTS_DIR",
    "FRAMEWORK_SKILLS_DIR",
    "FRAMEWORK_TEMPLATES_DIR",
    "FRAMEWORK_WORKFLOWS_DIR",
    "FRAMEWORK_INSTRUCTIONS_DIR",
    "FRAMEWORK_WORKSPACE_DIR",
)
WIRED_FUNCTIONS = frozenset(
    {
        "start-session",
        "resolve-workflow-instructions",
        "resolve-workflow-skills",
        "resolve-step",
        "resolve-step-model",
        "check-step-preconditions",
        "resolve-step-instructions",
        "resolve-step-skills",
        "check-step-postconditions",
        "end-session",
    }
)

ACCESS_CONTROL_LIST = """actors:
  - slug: facilitator
    roles:
      - author
  - slug: planner
    roles:
      - author
  - slug: reviewer
    roles:
      - author
roles:
  - slug: author
    privileges:
      - artifact: epic
        action: create
      - artifact: epic
        action: update
"""

WORKSPACE = """nodes:
  - slug: portfolio
    description: Portfolio folder
    children:
      - slug: epics
        description: Epic folder
        children:
          - slug: <item-slug>.md
            description: Epic artifact
            cardinality: 0..*
            artifact: epic
            template: epic
"""


def _capability_block(indent: str) -> str:
    """Render a contract-valid capability map at one indentation."""
    return "\n".join(
        f"{indent}{key}: {1.0 if key == 'coding' else 0.0}" for key in CAPABILITY_KEYS
    )


def _model_profiles() -> str:
    """Render a one-profile model catalog."""
    return f"""modelProfiles:
  - slug: fast-coder
    costRank: 2
    description: Fast coding model
    note: Cheap enough for iteration
    capabilities:
{_capability_block("      ")}
"""


def _workflow(actor: str = "planner", capability: str = "coding") -> str:
    """Render a one-step workflow whose actor and capability tag are steerable."""
    weights = "\n".join(
        f"      {key}: {1.0 if key == 'coding' else 0.0}" for key in CAPABILITY_KEYS
    )
    if capability != "coding":
        weights = f"      {capability}: 1.0"
    return f"""slug: planning
orchestrator: facilitator
skills:
  - orchestrate
instructions:
  - run-workflow
steps:
  - slug: draft
    actor: {actor}
    artifact: epic
    skills:
      - drafting
    instructions:
      - draft-instructions
    capabilities:
{weights}
"""


@pytest.fixture()
def framework_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a coherent framework tree and isolate the FRAMEWORK_* process variables."""
    for key in FRAMEWORK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    root = tmp_path / "framework"
    workspace = tmp_path / "workspace"
    for path in (
        root / "agents",
        root / "artifacts",
        root / "skills",
        root / "templates",
        root / "conf" / "workflows",
        root / "instructions",
        workspace,
    ):
        path.mkdir(parents=True)

    (root / ".env").write_text(
        "\n".join(
            (
                "FRAMEWORK_AGENTS_DIR=agents",
                "FRAMEWORK_ARTIFACTS_DIR=artifacts",
                "FRAMEWORK_SKILLS_DIR=skills",
                "FRAMEWORK_TEMPLATES_DIR=templates",
                "FRAMEWORK_WORKFLOWS_DIR=conf/workflows",
                "FRAMEWORK_INSTRUCTIONS_DIR=instructions",
                "FRAMEWORK_WORKSPACE_DIR=../workspace",
            )
        ),
        encoding="utf-8",
    )
    (root / "conf" / "access-control-list.conf.yaml").write_text(
        ACCESS_CONTROL_LIST, encoding="utf-8"
    )
    (root / "conf" / "model-profiles.conf.yaml").write_text(
        _model_profiles(), encoding="utf-8"
    )
    (root / "conf" / "workspace.conf.yaml").write_text(WORKSPACE, encoding="utf-8")
    (root / "conf" / "workflows" / "planning.workflow.conf.yaml").write_text(
        _workflow(), encoding="utf-8"
    )
    (root / "artifacts" / "epic.artifact.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gsmarc://saf/artifacts/epic/v1",
                "type": "object",
            }
        ),
        encoding="utf-8",
    )
    for skill in ("orchestrate", "drafting"):
        (root / "skills" / f"{skill}.skill.md").write_text("# skill\n", encoding="utf-8")
    for instruction in ("run-workflow", "draft-instructions"):
        (root / "instructions" / f"{instruction}.instructions.md").write_text(
            "# instructions\n", encoding="utf-8"
        )
    return root


def _workspace_dir(framework_root: Path) -> Path:
    """Answer the workspace the framework's layout environment points at."""
    return framework_root.parent / "workspace"


class TestApplication:
    """The composition root: fail-fast wiring, argv dispatch, and the exit plane."""

    def test_builds_every_configuration_view_before_any_function_runs(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): `Application` builds every configuration
        dataclass at instantiation, so every invocation of any function fails fast on
        an invalid configuration before any function runs.
        """
        (framework_root / "conf" / "model-profiles.conf.yaml").unlink()

        with pytest.raises(ConfigurationError):
            Application(framework_root)

    def test_refuses_a_layout_environment_that_names_a_missing_directory(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): the layout environment is validated fail-fast —
        every required variable present and pointing to an existing directory.
        """
        (framework_root / "templates").rmdir()

        with pytest.raises(ConfigurationError):
            Application(framework_root)

    def test_refuses_a_workflow_actor_the_access_control_list_never_declares(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): the cross-configuration coherence rules include
        `workflow actors exist in the ACL` — a rule spanning two sources, so the
        composition root enforces it fail-fast.
        """
        (framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml").write_text(
            _workflow(actor="ghost"), encoding="utf-8"
        )

        with pytest.raises(ConfigurationError) as failure:
            Application(framework_root)

        assert failure.value.code == "unknown-actor"

    def test_refuses_a_capability_tag_outside_the_shared_vocabulary(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): `capability tags belong to the model catalog's
        vocabulary`. Steps and profiles reference ONE shared `capabilities` definition
        (`model-profiles.conf.schema.json#/$defs/capabilities`), so the fail-fast
        contract validation at the config boundary IS the enforcement point — no
        second check exists to drift from it.
        """
        (framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml").write_text(
            _workflow(capability="telepathy"), encoding="utf-8"
        )

        with pytest.raises(ConfigurationError) as failure:
            Application(framework_root)

        assert failure.value.code == "invalid-configuration-source"

    def test_refuses_a_step_artifact_slug_that_resolves_to_no_schema(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): `step artifact slugs resolve to artifact
        schemas`; spec (C5): every artifact is schema-bound to one of the framework's
        artifact schemas.
        """
        (framework_root / "artifacts" / "epic.artifact.schema.json").unlink()

        with pytest.raises(ConfigurationError) as failure:
            Application(framework_root)

        assert failure.value.code == "unresolved-artifact-schema"

    def test_refuses_an_instruction_ref_that_resolves_to_no_file(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): `instruction/skill refs resolve to files in the
        framework layout`; spec (slug convention): the harness resolves a referenced
        entity by joining its canonical directory, the slug, and the fixed extension.
        """
        (framework_root / "instructions" / "draft-instructions.instructions.md").unlink()

        with pytest.raises(ConfigurationError) as failure:
            Application(framework_root)

        assert failure.value.code == "unresolved-context-ref"

    def test_refuses_a_skill_ref_that_resolves_to_no_file(
        self, framework_root: Path
    ) -> None:
        """Spec (Internal validation): `instruction/skill refs resolve to files in the
        framework layout` — the workflow's own skills are refs like any other.
        """
        (framework_root / "skills" / "orchestrate.skill.md").unlink()

        with pytest.raises(ConfigurationError) as failure:
            Application(framework_root)

        assert failure.value.code == "unresolved-context-ref"

    def test_registers_one_command_per_harness_function_it_can_wire(
        self, framework_root: Path
    ) -> None:
        """Spec (Classes, `commands`): `Command` is realized by twelve commands —
        exactly one per harness function — and nothing else, no hook command.

        Functions 8 and 9 are absent from this set only because
        `StepAuthorizationChecker` and `StepArtifactChecker` do not yet exist under
        `services/checking/`; wiring them here is the only change their landing needs.
        """
        application = Application(framework_root)

        assert application.list_functions() == tuple(sorted(WIRED_FUNCTIONS))

    def test_dispatches_argv_to_the_command_the_function_name_selects(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (`application`): the composition root builds the object graph, then
        dispatches `argv` to ONE command.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command(
            ["start-session", "--session-id", "s1", "--agent", "facilitator"]
        )

        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["context"]["function"] == "start-session"
        assert report["outcome"]["status"] == "started"

    def test_renders_the_report_byte_identically_to_the_journaled_one(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Classes, report identity rule): every service returns a concrete
        `Report`; every command returns that same object as its `out`; and the log
        entry stores that exact object under `report` — the log is not a second
        projection of the result.
        """
        application = Application(framework_root)

        application.dispatch_command(
            ["start-session", "--session-id", "s1", "--agent", "facilitator"]
        )

        rendered = capsys.readouterr().out.strip()
        journaled = (
            _workspace_dir(framework_root) / "logs" / "s1.log.jsonl"
        ).read_text(encoding="utf-8").strip()
        assert f'"report":{rendered}' in journaled

    def test_answers_a_never_journaled_outcome_on_its_ordinary_exit_path(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Outcomes rule 2): `not-applicable` is a success status, never
        journaled — function 0 answers it for a root session whose `agent` names no
        framework agent.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command(
            ["start-session", "--session-id", "s1", "--agent", "stranger"]
        )

        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["outcome"]["status"] == "not-applicable"
        assert not (_workspace_dir(framework_root) / "logs" / "s1.log.jsonl").exists()

    def test_surfaces_an_invalid_inquiry_at_the_exit_plane_without_a_report(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Outcomes rule 4): a contract-validation failure produces NO report at
        all — the failure surfaces at the command exit plane (stderr + nonzero exit),
        exactly like a crashed invocation.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command(
            ["start-session", "--session-id", "Not A Slug", "--agent", "facilitator"]
        )

        captured = capsys.readouterr()
        assert exit_code != 0
        assert captured.out == ""
        assert "invalid-inquiry" in captured.err

    def test_surfaces_a_missing_required_field_at_the_exit_plane(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Outcomes rule 1): a missing `agent` is one of the named
        `invalid-inquiry` cases, and rule 4 puts it at the exit plane with no report.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command(["start-session", "--session-id", "s1"])

        captured = capsys.readouterr()
        assert exit_code != 0
        assert captured.out == ""
        assert "invalid-inquiry" in captured.err

    def test_surfaces_an_unparsable_argument_at_the_exit_plane(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Outcomes rule 4): no contract-valid report can be built from an
        invocation whose arguments never reach the inquiry, so it surfaces exactly like
        a contract-validation failure.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command(
            ["start-session", "--session-id", "s1", "--agent", "facilitator", "--rogue", "x"]
        )

        captured = capsys.readouterr()
        assert exit_code != 0
        assert captured.out == ""

    def test_refuses_a_name_no_harness_function_owns(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Invocation surfaces): the harness core exposes exactly one command per
        function and nothing else, no hook command.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command(["handle-hook", "--session-id", "s1"])

        captured = capsys.readouterr()
        assert exit_code != 0
        assert captured.out == ""
        assert "handle-hook" in captured.err

    def test_refuses_an_invocation_that_names_no_function_at_all(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Invocation surfaces): every harness function is exposed as a harness
        command — an invocation naming none is no invocation at all.
        """
        application = Application(framework_root)

        exit_code = application.dispatch_command([])

        assert exit_code != 0
        assert capsys.readouterr().out == ""

    def test_reads_the_session_attribution_the_surrounding_mechanism_supplies(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Session attribution): every journaling invocation carries the
        `sessionId` of the session it runs in, and the surrounding mechanism supplies
        the pair — the command surface accepts both attribution flags.
        """
        application = Application(framework_root)
        application.dispatch_command(
            ["start-session", "--session-id", "p1", "--agent", "facilitator"]
        )
        capsys.readouterr()

        exit_code = application.dispatch_command(
            [
                "end-session",
                "--session-id",
                "p1",
                "--parent-session-id",
                "root",
            ]
        )

        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["outcome"]["status"] == "ended"

    def test_dispatches_the_workflow_slug_of_a_mediated_resolution(
        self, framework_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (function 3 / adapter H4): the mediated surface invokes
        `harness.py resolve-step --workflow <slug>`; the runner's own flag rendering
        emits `--workflow-slug`, so the command surface honors both spellings.
        """
        application = Application(framework_root)
        application.dispatch_command(
            ["start-session", "--session-id", "p1", "--agent", "facilitator"]
        )
        capsys.readouterr()

        exit_code = application.dispatch_command(
            ["resolve-step", "--session-id", "p1", "--workflow", "planning"]
        )

        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["context"]["function"] == "resolve-step"
        assert report["outcome"]["status"] == "step-resolution"


class TestMain:
    """The process entry point behind `harness.py`."""

    def test_builds_the_application_from_the_framework_anchor_variable(
        self,
        framework_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Spec (Configuration plane): `FRAMEWORK_DIR` anchors the layout — it is the
        one ABSOLUTE path, and the process environment takes precedence.
        """
        monkeypatch.setenv("FRAMEWORK_DIR", str(framework_root))

        exit_code = main(["start-session", "--session-id", "s1", "--agent", "facilitator"])

        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["context"]["sessionId"] == "s1"

    def test_refuses_to_run_without_the_framework_anchor(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec (Configuration plane): the framework's layout is environment, not file
        configuration — with no anchor there is no framework to harness.
        """
        monkeypatch.delenv("FRAMEWORK_DIR", raising=False)

        exit_code = main(["end-session", "--session-id", "s1"])

        captured = capsys.readouterr()
        assert exit_code != 0
        assert captured.out == ""
        assert "FRAMEWORK_DIR" in captured.err
