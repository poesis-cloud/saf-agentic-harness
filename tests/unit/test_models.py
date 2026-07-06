"""Unit tests — the workspace domain entities: Artifact, Report, Finding, Log/LogEntry,
Section (one test class per src class)."""

from __future__ import annotations

from pathlib import Path

from models import Artifact, Finding, Log, LogEntry, Report, Section


def _artifact(fields=None, frontmatter="", **kwargs) -> Artifact:
    return Artifact("epic", Path("/tmp/x/sample-epic.md"), fields or {}, frontmatter, **kwargs)


class TestArtifact:
    def test_artifact_id_is_the_slug_or_the_path_stem(self):
        assert _artifact({"slug": "epic-one"}).artifact_id == "epic-one"
        assert _artifact({}).artifact_id == "sample-epic"
        # the removed `id` field no longer participates in identity
        assert _artifact({"id": "E-1"}).artifact_id == "sample-epic"

    def test_scalar_accessors_normalize_empty_to_none(self):
        art = _artifact({"status": "done", "slug": ""})
        assert art.status == "done"
        assert art.slug is None

    def test_field_accessors(self):
        art = _artifact({"flag": "true", "items": ["a", "b"], "n": 3})
        assert art.field("n") == 3
        assert art.bool_field("flag") is True
        assert art.list_field("items") == ["a", "b"]

    def test_blocking_open_items_detected_in_frontmatter_block(self):
        fm = "open_items:\n  - id: oi-1\n    blocking: true\n    status: open\nstatus: draft"
        assert _artifact({}, fm).has_blocking_open_items() is True

    def test_non_blocking_or_empty_open_items(self):
        assert _artifact({}, "open_items: []").has_blocking_open_items() is False
        fm = "open_items:\n  - id: oi-1\n    blocking: false\n    status: open"
        assert _artifact({}, fm).has_blocking_open_items() is False

    def test_field_value_reads_raw_frontmatter(self):
        art = _artifact({}, "status: done # trailing comment\nempty: null")
        assert art.field_value("status") == "done"
        assert art.field_value("empty") is None
        assert art.field_value("ghost") is None

    def test_block_extracts_one_top_level_key(self):
        fm = "depends_on:\n  - E-1\n  - E-2\nstatus: done"
        block = _artifact({}, fm).block("depends_on")
        assert block[0].startswith("depends_on:")
        assert "status: done" not in block

    def test_dependency_ids_union_depends_on_and_relations(self):
        fm = (
            "depends_on:\n  - E-2\n  - E-1\n"
            "work_item_relations:\n  depends_on:\n    - E-3\n"
        )
        assert _artifact({}, fm).dependency_ids() == ["E-1", "E-2", "E-3"]

    def test_to_markdown_round_trip_shape(self):
        art = _artifact(
            {"status": "done"},
            "status: done",
            sections=[Section(level=2, name="Body", body="\ntext", children=[])],
            heading="# Sample",
        )
        rendered = art.to_markdown()
        assert rendered.startswith("---\nstatus: done\n---")
        assert "# Sample" in rendered
        assert "## Body" in rendered
        assert rendered.endswith("\n")

    def test_section_lookup(self):
        child = Section(level=3, name="Notes", body="", children=[])
        top = Section(level=2, name="Body", body="", children=[child])
        art = _artifact(sections=[top])
        assert art.section_by_name("Body") is top
        assert art.section_by_name("ghost") is None
        assert art.all_sections_flat() == [top, child]


class TestReport:
    def test_error_and_warn_accumulate_findings(self):
        report = Report()
        report.error("a.md", "broken")
        report.warn("b.md", "iffy")
        assert [f.severity for f in report.findings] == ["error", "warning"]
        assert report.has_errors()

    def test_extend_merges_reports(self):
        first, second = Report(), Report()
        first.error("a", "x")
        second.warn("b", "y")
        first.extend(second)
        assert len(first.findings) == 2

    def test_print_returns_exit_code(self, capsys):
        clean = Report()
        assert clean.print(strict=False) == 0
        failing = Report()
        failing.error("a", "x")
        assert failing.print(strict=False) == 1

    def test_strict_promotes_warnings_to_failure(self):
        report = Report()
        report.warn("a", "just a warning")
        assert report.print(strict=False) == 0
        assert report.print(strict=True) == 1

    def test_print_json_emits_compact_findings(self, capsys):
        report = Report()
        report.error("a.md", "broken")
        assert report.print_json(strict=False) == 1
        out = capsys.readouterr().out
        assert '"severity": "error"' in out
        assert "condition_id" not in out  # null fields are omitted


class TestFinding:
    def test_defaults_are_none(self):
        finding = Finding("error", "a.md", "boom")
        assert finding.condition_id is None
        assert finding.expected is None
        assert finding.actual is None
        assert finding.suggestion is None


class TestLogEntry:
    def test_envelope_fields_win_over_payload(self):
        entry = LogEntry({"command": "hook", "actor": "@a", "payload": {"actor": "@b", "unit": "u-1"}})
        assert entry.command == "hook"
        assert entry.actor == "@a"
        assert entry.unit == "u-1"  # falls back to payload

    def test_legacy_unit_id_key_is_read(self):
        assert LogEntry({"unit_id": "u-9"}).unit == "u-9"

    def test_outputs_default_to_empty_list(self):
        assert LogEntry({}).outputs == []
        assert LogEntry({"outputs": ["a.md"]}).outputs == ["a.md"]


class TestLog:
    LINES = [
        {"step": "draft", "status": "completed"},
        {"step": "review", "status": "completed"},
        {"step": "draft", "status": "reopened"},
        {"status": "no-step-line"},
    ]

    def test_executed_steps_latest_wins(self):
        # draft's LATEST line is 'reopened' -> only review counts as completed.
        assert Log(self.LINES).executed_steps() == ["review"]

    def test_by_step_groups_in_file_order(self):
        groups = Log(self.LINES).by_step()
        assert list(groups) == ["draft", "review"]
        assert len(groups["draft"]) == 2

    def test_replay_steps_first_seen_order(self):
        assert Log(self.LINES).replay_steps() == ["draft", "review"]


class TestSection:
    def test_to_markdown_renders_heading_and_body(self):
        section = Section(level=2, name="Goal", body="\nShip it.", children=[])
        assert section.to_markdown() == "## Goal\n\nShip it."

    def test_bodyless_heading_abuts_first_child(self):
        child = Section(level=3, name="Sub", body="\ntext", children=[])
        section = Section(level=2, name="Top", body="", children=[child])
        assert section.to_markdown() == "## Top\n### Sub\n\ntext"

    def test_flatten_is_depth_first(self):
        leaf = Section(level=4, name="Leaf", body="", children=[])
        mid = Section(level=3, name="Mid", body="", children=[leaf])
        top = Section(level=2, name="Top", body="", children=[mid])
        assert [s.name for s in top.flatten()] == ["Top", "Mid", "Leaf"]

    def test_by_name_searches_descendants(self):
        leaf = Section(level=3, name="Leaf", body="", children=[])
        top = Section(level=2, name="Top", body="", children=[leaf])
        assert top.by_name("Leaf") is leaf
        assert top.by_name("ghost") is None
