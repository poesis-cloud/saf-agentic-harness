"""AuthorizationChecker — the AUTHORITY plane: every artifact write must be a granted privilege."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from models import Report
from mappers import LogRepository, SchemaRepository, Workspace
from text import frontmatter, parse_frontmatter
from .authorization_policy import AuthorizationPolicy


class AuthorizationChecker:
    """Verifies write-authority over a run log against the harness ACL. Each line names the acting
    ``actor``, an optional ``action`` (create/read/update/delete; default update), and the ``outputs``
    it wrote — each output a ``path`` (any ``#property`` suffix is ignored: authorization is plain,
    whole-resource RBAC). For every output the checker derives (action, resource) — the resource is the
    artifact's schema name (its ``*.artifact.schema`` stem), from an owner-only singleton path else the
    matching schema ``pathPatterns`` — and requires the actor to hold a covering privilege. A write
    nobody granted is the drift it rejects.

    Read-only and deterministic: it never mutates artifacts; it reports the ungranted write so the
    orchestration reverts it through a privileged author and re-runs."""

    def __init__(self, workspace: Workspace, schemas: SchemaRepository, logs: LogRepository, policy: AuthorizationPolicy) -> None:
        self.workspace = workspace
        self.schemas = schemas
        self.logs = logs
        self.policy = policy

    def resource_for(self, path: str) -> str | None:
        """Resolve a write path to its resource = the artifact's schema name (schema_id). Match the
        path against schema pathPatterns (templated tokens wildcarded). Business and enabler share one
        path; when several schemas match, disambiguate by the artifact's `type` frontmatter so the
        enabler schema (`epic-enabler`, ...) is selected. First match wins when type is silent."""
        candidate = path.replace("{product}", "*").replace("{unit_id}", "*")
        schemas = self.schemas.load_raw(Report())
        matches = []
        for schema_id, schema_dict in schemas.items():
            metadata = schema_dict.get("x-artifact", {})
            path_patterns = metadata.get("pathPatterns", [])
            if not isinstance(path_patterns, list):
                path_patterns = [path_patterns]
            for pattern in path_patterns:
                pattern_wildcard = pattern.replace("{product}", "*").replace("{unit_id}", "*")
                if fnmatch.fnmatch(candidate, pattern_wildcard) or fnmatch.fnmatch(candidate, f"*/{pattern_wildcard}"):
                    matches.append((schema_id, schema_dict))
                    break
        
        if not matches:
            return None
        if len(matches) > 1:
            file = self.workspace.portfolio_base / path
            if file.is_file():
                wanted = str(parse_frontmatter(frontmatter(self.workspace.read_text(file))).get("type") or "").strip()
                for schema_id, schema_dict in matches:
                    metadata = schema_dict.get("x-artifact", {})
                    if metadata.get("type") == wanted:
                        return schema_id
            # default to the business variant when type is silent
            for schema_id, schema_dict in matches:
                metadata = schema_dict.get("x-artifact", {})
                if metadata.get("type") == "business":
                    return schema_id
        return matches[0][0]

    def check_log(self, log_path: Path) -> Report:
        report = Report()
        label = self.workspace.label(log_path, self.workspace.framework_root)
        log = self.logs.read(log_path)
        if log is None:
            report.error(label, "run log not found or empty; nothing to authorize")
            return report
        for index, line in enumerate(log.lines, start=1):
            actor = self.policy.normalize(str(line.get("actor") or ""))
            action = str(line.get("action") or "update").strip().lower()
            outputs = line.get("outputs") or []
            if not outputs:
                continue
            if not actor:
                report.warn(f"{label}:{index}", f"line writes {outputs} with no actor; authorship cannot be verified")
                continue
            for ref in outputs:
                path = str(ref).split("#", 1)[0]  # any #property suffix is ignored — whole-resource RBAC
                resource = self.policy.singleton_kind(path) or self.resource_for(path)
                if resource is None:
                    report.warn(f"{label}:{index}", f"no resource for output {ref!r}; cannot authorize {actor!r}")
                    continue
                if not self.policy.allows(actor, action, resource):
                    report.error(f"{label}:{index}", f"{actor!r} lacks privilege {action}_{resource} (no agent grant)")
        return report

