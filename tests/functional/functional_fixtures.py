"""The functional rig: a real framework, a real Git workspace, real command runs.

Spec (Functional testing): `tests/functional/` exercises the real command entry point
over a fixture framework configuration and a fixture workspace, asserting the full
Interface (In -> Out), the Postconditions, and the invariants observable from outside.
Nothing here injects a fake into the system under test — the only monkeypatching is of
the process environment carrying the framework layout (Configuration plane).
"""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from application import Application
from utils.schema_validator import SchemaValidator, ValidationErrorRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = REPO_ROOT / "contracts"
ENTRY_SHIM = REPO_ROOT / "harness.py"

LOG_ENTRY_CONTRACT_ID = "gsmarc://saf/contracts/log-entry/v1"
LOG_FILE_SUFFIX = ".log.jsonl"

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

CAPABILITY_TAGS = (
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

# The argv spelling of each contract property, so a test builds the `in` object the
# contract describes and the rig renders the invocation a host would make.
_FLAG_SPELLINGS: Mapping[str, str] = {
    "sessionId": "--session-id",
    "parentSessionId": "--parent-session-id",
    "agent": "--agent",
    "workflowSlug": "--workflow-slug",
    "artifactPath": "--artifact-path",
    "artifactPaths": "--artifact-path",
    "action": "--action",
}
_SET_VALUED_PROPERTIES = frozenset({"artifactPaths"})

_GIT_AUTHOR = ("-c", "user.name=Harness Fixture", "-c", "user.email=fixture@example.test")


def build_capabilities(**weights: float) -> dict[str, float]:
    """Build a contract-complete capability map: every tag explicit, unnamed tags zero."""
    return {tag: float(weights.get(tag.replace("-", "_"), 0)) for tag in CAPABILITY_TAGS}


DEFAULT_ACCESS_CONTROL_LIST: Mapping[str, Any] = {
    "actors": [
        {"slug": "orchestrator", "roles": ["facilitator"]},
        {"slug": "builder", "roles": ["author"]},
        {"slug": "reviewer", "roles": ["author"]},
    ],
    "roles": [
        {"slug": "facilitator", "privileges": [{"artifact": "report", "action": "create"}]},
        {
            "slug": "author",
            "privileges": [
                {"artifact": "report", "action": "create"},
                {"artifact": "report", "action": "update"},
            ],
        },
    ],
}

DEFAULT_MODEL_PROFILES: Sequence[Mapping[str, Any]] = (
    {
        "slug": "deep-thinker",
        "costRank": 5,
        "description": "Reasoning-heavy profile.",
        "capabilities": build_capabilities(deep_reasoning=9, coding=5, writing_quality=8),
    },
    {
        "slug": "fast-coder",
        "costRank": 2,
        "description": "Cheap coding profile.",
        "capabilities": build_capabilities(deep_reasoning=3, coding=9, fast_iteration=9),
    },
)

# Three profiles scoring identically on every tag: the highest score is a three-way tie,
# so the selection is decided by `costRank` first and by the slug second.
TIED_MODEL_PROFILES: Sequence[Mapping[str, Any]] = (
    {"slug": "omega-twin", "costRank": 3, "capabilities": build_capabilities(coding=4)},
    {"slug": "pricey-twin", "costRank": 7, "capabilities": build_capabilities(coding=4)},
    {"slug": "alpha-twin", "costRank": 3, "capabilities": build_capabilities(coding=4)},
)

DEFAULT_WORKSPACE_LAYOUT: Mapping[str, Any] = {
    "nodes": [
        {
            "slug": "report",
            "description": "The report artifact folder.",
            "children": [
                {
                    "slug": "<report-slug>.json",
                    "description": "One report artifact.",
                    "cardinality": "0..*",
                    "artifact": "report",
                    "template": "report",
                }
            ],
        }
    ]
}

PLANNING_WORKFLOW: Mapping[str, Any] = {
    "slug": "planning",
    "orchestrator": "orchestrator",
    "skills": ["workflow-selection", "planning-procedure"],
    "instructions": ["workflow-selection-handling", "step-resolution-handling"],
    "steps": [
        {
            "slug": "draft",
            "actor": "builder",
            "artifact": "report",
            "skills": ["drafting"],
            "instructions": ["draft-guidance"],
            "capabilities": build_capabilities(coding=8),
        },
        {
            "slug": "review",
            "actor": "reviewer",
            "artifact": "report",
            "instructions": ["review-guidance"],
            "capabilities": build_capabilities(deep_reasoning=9),
            "conditions": [
                {"kind": "precondition", "slug": "after-draft", "step": "draft"},
                {
                    "kind": "precondition",
                    "slug": "report-exists",
                    "setSelector": {"setQuery": "artifacts['report']"},
                    "setPredicate": "size(selected) > 0",
                },
            ],
        },
    ],
}

VERIFICATION_WORKFLOW: Mapping[str, Any] = {
    "slug": "verification",
    "orchestrator": "orchestrator",
    "skills": ["verification-procedure"],
    "instructions": ["workflow-selection-handling", "no-next-step-handling"],
    "steps": [
        {
            "slug": "verify",
            "actor": "reviewer",
            "artifact": "report",
            "instructions": ["verify-guidance"],
            "capabilities": build_capabilities(deep_reasoning=6, schema_adherence=4),
        }
    ],
}

# A workflow whose only precondition is a CEL predicate that evaluates to a list rather
# than a boolean: the expression fails at RUNTIME, which function 5 owes a `state-error`.
UNEVALUABLE_WORKFLOW: Mapping[str, Any] = {
    "slug": "probing",
    "orchestrator": "orchestrator",
    "skills": ["workflow-selection"],
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "probe",
            "actor": "builder",
            "artifact": "report",
            "instructions": ["draft-guidance"],
            "capabilities": build_capabilities(coding=5),
            "conditions": [
                {
                    "kind": "precondition",
                    "slug": "unevaluable",
                    "setSelector": {"setQuery": "artifacts['report']"},
                    "setPredicate": "selected",
                }
            ],
        }
    ],
}

DEFAULT_WORKFLOWS: Mapping[str, Mapping[str, Any]] = {
    "planning": PLANNING_WORKFLOW,
    "verification": VERIFICATION_WORKFLOW,
}


def _as_slug_tuple(value: Any) -> tuple[str, ...]:
    """Normalize a contract's scalar-or-array slug reference into a tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _iter_artifact_slugs(nodes: Iterable[Mapping[str, Any]]) -> Iterable[str]:
    """Walk a workspace blueprint, answering every artifact slug it binds."""
    for node in nodes:
        children = node.get("children")
        if children is not None:
            yield from _iter_artifact_slugs(children)
        else:
            yield node["artifact"]


@dataclass(frozen=True)
class CommandRun:
    """One command invocation: the `in` object, the exit plane, and the `out` object."""

    function: str
    inquiry: Mapping[str, Any]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def report(self) -> Mapping[str, Any] | None:
        """Answer the rendered report, or none when the invocation produced none."""
        rendered = self.stdout.strip()
        return json.loads(rendered) if rendered else None

    @property
    def status(self) -> str:
        """Answer the outcome status of the returned report."""
        return self.outcome["status"]

    @property
    def outcome(self) -> Mapping[str, Any]:
        """Answer the outcome object of the returned report."""
        report = self.report
        assert report is not None, f"{self.function} returned no report: {self.stderr}"
        return report["outcome"]

    @property
    def error_code(self) -> str | None:
        """Answer the error code of the returned report, or none on a success outcome."""
        error = self.outcome.get("error")
        return None if error is None else error["code"]


@dataclass(frozen=True)
class FunctionalHarness:
    """Drive the assembled harness as a host does, then observe what it left behind."""

    framework_dir: Path
    workspace_dir: Path
    contracts: SchemaValidator

    def render_flags(self, inquiry: Mapping[str, Any]) -> tuple[str, ...]:
        """Render one `in` object as the flags a host passes on the command line."""
        flags: list[str] = []
        for property_name, value in inquiry.items():
            if value is None:
                continue
            spelling = _FLAG_SPELLINGS[property_name]
            values = value if property_name in _SET_VALUED_PROPERTIES else (value,)
            for item in values:
                flags.extend((spelling, str(item)))
        return tuple(flags)

    def invoke(self, function: str, **inquiry: Any) -> CommandRun:
        """Run one function through the real composition root, capturing both streams."""
        flags = self.render_flags(inquiry)
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            exit_code = Application(self.framework_dir).dispatch_command(
                [function, *flags]
            )
        return CommandRun(
            function=function,
            inquiry=dict(inquiry),
            exit_code=exit_code,
            stdout=out.getvalue(),
            stderr=err.getvalue(),
        )

    def invoke_entry_shim(self, function: str, **inquiry: Any) -> CommandRun:
        """Run one function through `harness.py`, the repository's entry shim."""
        flags = self.render_flags(inquiry)
        completed = subprocess.run(
            [sys.executable, str(ENTRY_SHIM), function, *flags],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "FRAMEWORK_DIR": str(self.framework_dir),
            },
        )
        return CommandRun(
            function=function,
            inquiry=dict(inquiry),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def validate_inquiry(
        self, function: str, inquiry: Mapping[str, Any]
    ) -> tuple[ValidationErrorRecord, ...]:
        """Validate one `in` object against the function's own input contract."""
        return self.contracts.validate_instance(
            f"gsmarc://saf/contracts/api/{function}.input/v1", dict(inquiry)
        )

    def validate_report(
        self, function: str, report: Mapping[str, Any]
    ) -> tuple[ValidationErrorRecord, ...]:
        """Validate one report against the function's own output contract."""
        return self.contracts.validate_instance(
            f"gsmarc://saf/contracts/api/{function}.output/v1", report
        )

    def validate_log_entry(
        self, entry: Mapping[str, Any]
    ) -> tuple[ValidationErrorRecord, ...]:
        """Validate one journaled entry against the log-entry contract."""
        return self.contracts.validate_instance(LOG_ENTRY_CONTRACT_ID, entry)

    def log_path(self, session_id: str) -> Path:
        """Answer where the session's log file belongs (Logging: 1 session = 1 file)."""
        return self.workspace_dir / "logs" / f"{session_id}{LOG_FILE_SUFFIX}"

    def is_session_logged(self, session_id: str) -> bool:
        """Tell whether the session's log file exists."""
        return self.log_path(session_id).is_file()

    def read_log(self, session_id: str) -> tuple[Mapping[str, Any], ...]:
        """Read the session's journaled entries, in file order."""
        path = self.log_path(session_id)
        if not path.is_file():
            return ()
        return tuple(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
        )

    def read_log_lines(self, session_id: str) -> tuple[str, ...]:
        """Read the session's journaled entries as raw lines, for byte comparisons."""
        path = self.log_path(session_id)
        if not path.is_file():
            return ()
        return tuple(line for line in path.read_text(encoding="utf-8").splitlines() if line)

    def list_journaled_functions(self, session_id: str) -> tuple[str, ...]:
        """List the functions the session journaled, in order."""
        return tuple(entry["report"]["context"]["function"] for entry in self.read_log(session_id))

    def commit_artifact(self, ref: str, data: Mapping[str, Any]) -> None:
        """Commit one artifact into the workspace: committed state IS workspace state."""
        path = self.workspace_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        run_git(self.workspace_dir, "add", "--", ref)
        run_git(self.workspace_dir, *_GIT_AUTHOR, "commit", "-q", "-m", f"artifact: add {ref}")

    def list_committed_paths(self) -> tuple[str, ...]:
        """List every path in the workspace's committed state."""
        listing = run_git(self.workspace_dir, "ls-tree", "-r", "--name-only", "HEAD")
        return tuple(sorted(line for line in listing.splitlines() if line))

    def count_commits(self) -> int:
        """Count the workspace repository's commits — the write boundary's only trace."""
        return int(run_git(self.workspace_dir, "rev-list", "--count", "HEAD").strip())


def run_git(workspace_dir: Path, *arguments: str) -> str:
    """Run one Git command in the fixture workspace, failing loudly."""
    completed = subprocess.run(
        ["git", *arguments],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def build_framework(
    root: Path,
    *,
    workflows: Mapping[str, Mapping[str, Any]] | None = None,
    model_profiles: Sequence[Mapping[str, Any]] | None = None,
    access_control_list: Mapping[str, Any] | None = None,
    workspace_layout: Mapping[str, Any] | None = None,
) -> FunctionalHarness:
    """Write a whole framework and a whole Git workspace, then answer their harness.

    Spec (Configuration plane): the framework's layout is environment loaded from a
    `.env` at the framework root, and WHAT it declares lives in `conf/` under contract
    schemas — so the rig writes exactly those sources and nothing the harness invents.
    """
    catalog = dict(DEFAULT_WORKFLOWS if workflows is None else workflows)
    profiles = tuple(DEFAULT_MODEL_PROFILES if model_profiles is None else model_profiles)
    acl = dict(DEFAULT_ACCESS_CONTROL_LIST if access_control_list is None else access_control_list)
    layout = dict(DEFAULT_WORKSPACE_LAYOUT if workspace_layout is None else workspace_layout)

    framework_dir = root / "framework"
    workspace_dir = root / "workspace"
    directories = {
        "agents": framework_dir / "agents",
        "artifacts": framework_dir / "artifacts",
        "skills": framework_dir / "skills",
        "templates": framework_dir / "templates",
        "instructions": framework_dir / "instructions",
        "workflows": framework_dir / "conf" / "workflows",
    }
    for directory in (*directories.values(), workspace_dir):
        directory.mkdir(parents=True, exist_ok=True)

    (framework_dir / ".env").write_text(
        "\n".join(
            (
                "FRAMEWORK_AGENTS_DIR=agents",
                "FRAMEWORK_ARTIFACTS_DIR=artifacts",
                "FRAMEWORK_SKILLS_DIR=skills",
                "FRAMEWORK_TEMPLATES_DIR=templates",
                "FRAMEWORK_WORKFLOWS_DIR=conf/workflows",
                "FRAMEWORK_INSTRUCTIONS_DIR=instructions",
                "FRAMEWORK_WORKSPACE_DIR=../workspace",
                "",
            )
        ),
        encoding="utf-8",
    )

    conf_dir = framework_dir / "conf"
    _write_yaml(conf_dir / "access-control-list.conf.yaml", acl)
    _write_yaml(conf_dir / "model-profiles.conf.yaml", {"modelProfiles": list(profiles)})
    _write_yaml(conf_dir / "workspace.conf.yaml", layout)
    for slug, workflow in catalog.items():
        _write_yaml(directories["workflows"] / f"{slug}.workflow.conf.yaml", workflow)

    _materialize_context_refs(directories, catalog)
    _materialize_artifact_schemas(directories["artifacts"], catalog, layout)
    _initialize_workspace_repository(workspace_dir)

    contracts = SchemaValidator.compile_contracts(sorted(CONTRACTS_ROOT.rglob("*.schema.json")))
    return FunctionalHarness(
        framework_dir=framework_dir, workspace_dir=workspace_dir, contracts=contracts
    )


def _write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    """Write one configuration source as YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(data), sort_keys=False), encoding="utf-8")


def _materialize_context_refs(
    directories: Mapping[str, Path], catalog: Mapping[str, Mapping[str, Any]]
) -> None:
    """Create a file for every skill and instruction ref the catalog declares.

    The composition root refuses a configuration whose refs resolve to no file, so the
    rig materializes exactly the refs its own workflows name — no more.
    """
    skills: set[str] = set()
    instructions: set[str] = set()
    for workflow in catalog.values():
        skills.update(_as_slug_tuple(workflow.get("skills")))
        instructions.update(_as_slug_tuple(workflow.get("instructions")))
        for step in workflow["steps"]:
            skills.update(_as_slug_tuple(step.get("skills")))
            instructions.update(_as_slug_tuple(step.get("instructions")))
    for slug in sorted(skills):
        (directories["skills"] / f"{slug}.skill.md").write_text(
            f"# {slug}\n", encoding="utf-8"
        )
    for slug in sorted(instructions):
        (directories["instructions"] / f"{slug}.instructions.md").write_text(
            f"# {slug}\n", encoding="utf-8"
        )


def _materialize_artifact_schemas(
    artifacts_dir: Path,
    catalog: Mapping[str, Mapping[str, Any]],
    workspace_layout: Mapping[str, Any],
) -> None:
    """Write one artifact schema per declared artifact slug, keyed by a unique `$id`."""
    slugs = set(_iter_artifact_slugs(workspace_layout["nodes"]))
    slugs.update(step["artifact"] for workflow in catalog.values() for step in workflow["steps"])
    for slug in sorted(slugs):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"gsmarc://saf/tests/functional/artifacts/{slug}/v1",
            "title": f"{slug} fixture artifact schema",
            "type": "object",
            "required": ["slug", "state"],
            "properties": {
                "slug": {"type": "string", "pattern": "^[a-z0-9-]+$"},
                "state": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        (artifacts_dir / f"{slug}.artifact.schema.json").write_text(
            json.dumps(schema, indent=2), encoding="utf-8"
        )


def _initialize_workspace_repository(workspace_dir: Path) -> None:
    """Initialize the workspace as a Git repository with a committed baseline.

    Spec (Workspace Git plane): committed state IS workspace state, so the fixture
    workspace has a `HEAD` from the start — every reader reads committed bytes.
    """
    run_git(workspace_dir, "init", "-q")
    run_git(workspace_dir, "config", "commit.gpgsign", "false")
    # Function 9 commits with the workspace's OWN identity, and the entry shim runs with a
    # stripped environment: without a repository-local committer the commit gate would
    # depend on the developer's global Git configuration.
    run_git(workspace_dir, "config", "user.name", "Harness Fixture")
    run_git(workspace_dir, "config", "user.email", "fixture@example.test")
    (workspace_dir / ".gitignore").write_text("logs/\n", encoding="utf-8")
    run_git(workspace_dir, "add", "--", ".gitignore")
    run_git(workspace_dir, *_GIT_AUTHOR, "commit", "-q", "-m", "chore: workspace baseline")


def assert_contract_round_trip(harness: FunctionalHarness, run: CommandRun) -> Mapping[str, Any]:
    """Assert the full In -> Out round trip against both of the function's contracts."""
    assert harness.validate_inquiry(run.function, run.inquiry) == (), (
        f"the {run.function} inquiry fails its own input contract: {run.inquiry}"
    )
    report = run.report
    assert report is not None, f"{run.function} returned no report: {run.stderr}"
    assert harness.validate_report(run.function, report) == (), (
        f"the {run.function} report fails its own output contract: {report}"
    )
    assert run.exit_code == 0
    return report


def assert_journal_contract(
    harness: FunctionalHarness, session_id: str
) -> tuple[Mapping[str, Any], ...]:
    """Assert every journaled entry of one session against the log-entry contract."""
    entries = harness.read_log(session_id)
    for position, entry in enumerate(entries):
        assert harness.validate_log_entry(entry) == (), (
            f"log entry {position} of '{session_id}' violates the log-entry contract: {entry}"
        )
    return entries


def assert_report_journaled_byte_identically(
    harness: FunctionalHarness, run: CommandRun, position: int
) -> None:
    """Assert the returned report is the journaled entry's report, byte for byte."""
    line = harness.read_log_lines(run.inquiry["sessionId"])[position]
    journaled = json.loads(line)["report"]
    rendered = json.dumps(journaled, ensure_ascii=False, separators=(",", ":"))
    assert rendered == run.stdout.strip()


__all__ = [
    "CAPABILITY_TAGS",
    "CommandRun",
    "DEFAULT_ACCESS_CONTROL_LIST",
    "DEFAULT_MODEL_PROFILES",
    "DEFAULT_WORKFLOWS",
    "DEFAULT_WORKSPACE_LAYOUT",
    "FRAMEWORK_ENV_KEYS",
    "FunctionalHarness",
    "PLANNING_WORKFLOW",
    "TIED_MODEL_PROFILES",
    "UNEVALUABLE_WORKFLOW",
    "VERIFICATION_WORKFLOW",
    "assert_contract_round_trip",
    "assert_journal_contract",
    "assert_report_journaled_byte_identically",
    "build_capabilities",
    "build_framework",
    "run_git",
]
