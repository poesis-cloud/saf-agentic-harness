"""The store owning the artifact side: committed state is workspace state (C6)."""

from __future__ import annotations

import json
import subprocess
from itertools import chain
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence

from config.workspace_layout import WorkspaceLayout
from errors import ConfigurationError, StateError, SystemFailureError
from stores.artifact_store.artifact import Artifact
from stores.artifact_store.finding import Finding
from utils.json_loader import JsonLoader
from utils.markdown_loader import MarkdownLoader
from utils.schema_validator import SchemaValidator, ValidationErrorRecord

_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
_MARKDOWN_SUFFIX = ".md"


class ArtifactStore:
    """Read committed artifacts, validate staged ones, and commit or discard them.

    Git is the transaction mechanism: `HEAD` is workspace state, the working
    tree is the write staging area, and `commit_artifacts` / `revert_artifact`
    are the harness's only deliberate Git writes (C6, function 9).
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        artifact_schemas: Mapping[str, Path],
        workspace_layout: WorkspaceLayout,
        schema_validator: SchemaValidator | None = None,
        json_loader: JsonLoader | None = None,
        markdown_loader: MarkdownLoader | None = None,
    ) -> None:
        """Create the store over a workspace repository, compiling contracts once.

        `artifact_schemas` maps an artifact slug to its schema file; which slug a
        given workspace path is bound to is the `workspace_layout`'s answer.
        """
        self._workspace_dir = Path(workspace_dir)
        self._workspace_layout = workspace_layout
        self._json_loader = json_loader or JsonLoader()
        self._markdown_loader = markdown_loader or MarkdownLoader()
        schema_paths = tuple(Path(path) for path in artifact_schemas.values())
        self._schema_ids: Mapping[str, str] = MappingProxyType(
            {
                kind: self._read_schema_id(Path(path))
                for kind, path in artifact_schemas.items()
            }
        )
        self._validator = schema_validator or SchemaValidator.compile_contracts(
            chain(sorted(_CONTRACTS_DIR.rglob("*.schema.json")), schema_paths)
        )

    def discover_artifacts(self, selector: str) -> tuple[Artifact, ...]:
        """Read committed artifacts under a selector, raising on any invalid one.

        C6: `discover()` raises rather than yield a schema-invalid artifact, so
        every reader of committed state fails loudly on foreign corruption.
        """
        artifacts: list[Artifact] = []
        findings: list[Finding] = []
        for artifact_path in self.scan_raw_paths(Path(selector)):
            ref = artifact_path.as_posix()
            kind = self._resolve_kind(ref)
            committed = self._run_git("show", f"HEAD:{ref}")
            path_findings, data = self._validate_document(ref, kind, committed)
            if path_findings:
                findings.extend(path_findings)
                continue
            artifacts.append(Artifact(ref=ref, kind=kind, data=data))
        if findings:
            first = findings[0]
            raise StateError(
                "artifact-invalid",
                f"Committed artifact '{first.source}' is not valid: {first.message}",
                False,
            )
        return tuple(sorted(artifacts, key=lambda artifact: artifact.ref))

    def scan_raw_paths(self, scope: Path) -> Iterator[Path]:
        """Enumerate the raw universe of committed paths under a scope.

        The sweep's reader: unlike `discover_artifacts` it never validates, so
        whoever must enumerate invalids can (internal validation, C6).
        """
        listing = self._run_git(
            "ls-tree", "-r", "--name-only", "HEAD", "--", Path(scope).as_posix()
        )
        paths = tuple(Path(line) for line in sorted(listing.splitlines()) if line)
        return iter(paths)

    def validate_artifact(self, artifact_path: Path) -> tuple[Finding, ...]:
        """Validate the staged bytes of one path against its matched schema.

        Function 9 validates the working tree — the staging area — before the
        commit gate promotes it; the path exists because the write just landed.
        """
        ref = Path(artifact_path).as_posix()
        kind = self._resolve_kind(ref)
        staged = (self._workspace_dir / artifact_path).read_text(encoding="utf-8")
        return self._validate_document(ref, kind, staged)[0]

    def is_staging_clean(self, artifact_path: Path) -> bool:
        """Answer whether a target path is absent, or tracked and clean against `HEAD`.

        Function 8's invariant 5 staging baseline: a dirty tracked target and a
        pre-existing untracked target are both unclean.
        """
        status = self._run_git(
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            Path(artifact_path).as_posix(),
        )
        return status == ""

    def commit_artifacts(
        self, artifact_paths: Sequence[Path], *, session_id: str
    ) -> None:
        """Promote one validated write set into committed state as ONE commit.

        Function 9 invariant 3: 1 validated set = 1 commit, attributed to the
        acting session. Invariant 4: a set byte-identical to `HEAD` stages
        nothing and creates no commit.
        """
        refs = tuple(Path(artifact_path).as_posix() for artifact_path in artifact_paths)
        if not refs:
            return
        self._run_git("add", "--", *refs)
        if self._run_git("diff", "--cached", "--name-only", "--", *refs) == "":
            return
        self._run_git(
            "commit",
            "-m",
            f"artifact: commit validated write set\n\nsession-id: {session_id}",
        )

    def revert_artifact(self, artifact_path: Path) -> Finding:
        """Discard one staged path: restore it from `HEAD`, or delete a new one.

        Function 9 invariant 2: the discard never touches workspace state —
        the invalid bytes existed only in staging.
        """
        ref = Path(artifact_path).as_posix()
        if self._probe_git("cat-file", "-e", f"HEAD:{ref}"):
            self._run_git("checkout", "HEAD", "--", ref)
            return Finding(
                source=ref, rule="restored", message=f"Restored '{ref}' from HEAD."
            )
        self._run_git("rm", "--cached", "--force", "--ignore-unmatch", "--", ref)
        (self._workspace_dir / artifact_path).unlink(missing_ok=True)
        return Finding(
            source=ref, rule="deleted", message=f"Deleted newly staged '{ref}'."
        )

    def _read_schema_id(self, schema_path: Path) -> str:
        """Read one artifact schema's canonical `$id`, failing fast when absent."""
        document = self._json_loader.load_json(schema_path)
        schema_id = document.get("$id") if isinstance(document, Mapping) else None
        if not isinstance(schema_id, str):
            raise ConfigurationError(
                "artifact-schema-invalid",
                f"Artifact schema '{schema_path}' declares no string $id.",
                False,
            )
        return schema_id

    def _resolve_kind(self, ref: str) -> str:
        """Match one workspace path to the artifact kind whose path pattern binds it.

        Function 8 invariant 2 / function 9 invariant 1: the artifact's schema
        identity is resolved from the write path through the workspace layout's
        path patterns. Function 9 precondition (E): a path resolving to no
        artifact schema is `state-error` (`artifact-schema-unresolved`).
        """
        try:
            return self._workspace_layout.resolve_resource(ref, None)
        except ConfigurationError as failure:
            raise StateError(
                "artifact-schema-unresolved",
                f"No artifact schema resolves the path '{ref}': {failure.message}",
                False,
            ) from failure

    def _validate_document(
        self, ref: str, kind: str, document: str
    ) -> tuple[tuple[Finding, ...], Any]:
        """Validate one artifact's raw bytes, returning its findings and its data."""
        try:
            data = self._read_document(ref, document)
        except (json.JSONDecodeError, ValueError) as error:
            return (
                (Finding(source=ref, rule="parse", message=f"{error}"),),
                None,
            )
        records = self._validator.validate_instance(self._schema_ids[kind], data)
        findings = tuple(
            Finding(source=ref, rule="schema", message=self._render_message(record))
            for record in records
        )
        return findings, data

    def _read_document(self, ref: str, document: str) -> Any:
        """Read one artifact's bytes in the format its own file extension declares.

        A markdown artifact's schema binds its FRONTMATTER (function 9's worked
        example reports `frontmatter.status: ...`); the body is prose no contract
        describes.
        """
        if ref.endswith(_MARKDOWN_SUFFIX):
            return self._markdown_loader.parse_markdown(document).frontmatter
        return json.loads(document)

    def _render_message(self, record: ValidationErrorRecord) -> str:
        """Render one validation record as a path-prefixed failure message."""
        return f"{record.path}: {record.message}" if record.path else record.message

    def _invoke_git(
        self, args: Sequence[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        """Run one Git command in the workspace, mapping failures to `system-error`."""
        try:
            return subprocess.run(
                ("git", *args),
                cwd=self._workspace_dir,
                text=True,
                capture_output=True,
                check=check,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise SystemFailureError(
                "git-failed",
                f"git {' '.join(args)} failed: {detail}",
                True,
            ) from error
        except OSError as error:
            raise SystemFailureError(
                "git-failed",
                f"git {' '.join(args)} could not run: {error}",
                True,
            ) from error

    def _run_git(self, *args: str) -> str:
        """Run one Git command that must succeed, returning its stripped stdout."""
        return self._invoke_git(args, check=True).stdout.strip()

    def _probe_git(self, *args: str) -> bool:
        """Run one Git command whose non-zero exit is an answer, not a failure."""
        return self._invoke_git(args, check=False).returncode == 0


__all__ = ["ArtifactStore"]
