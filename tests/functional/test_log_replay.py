"""Log-replay determinism: byte-stable per log, merge-stable per instance view.

Spec (Functional testing): "log replay tests: re-running against a golden fixture set of
session logs must be byte-stable per log and merge-stable per instance view — the
determinism check."

The golden fixture is CAPTURED from a real multi-session, multi-step scenario driven
through the real command entry point, then re-seeded into a fresh workspace. One
normalization happens at capture: every entry's `timestamp` is re-stamped, in the
captured chronological order, onto a fixed canonical sequence. The harness's entry clock
is not injectable at the command entry point (`Application` wires each service's default
wall clock), so a raw capture could never be byte-compared against a second run; the
re-stamping controls the clock at the one plane the rig owns — the persisted golden —
and keeps the byte assertion exact rather than approximate.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Mapping

from functional_fixtures import (
    LOG_FILE_SUFFIX,
    FunctionalHarness,
    assert_contract_round_trip,
)

HarnessBuilder = Callable[..., FunctionalHarness]

GoldenLogs = Mapping[str, tuple[str, ...]]

CANONICAL_INSTANT = "2026-01-01T00:00:{position:02d}.000Z"
STEP_SESSION = "draft-session"

# The replayed reads: every one of them is, by the outcome rules, an invocation that
# returns a report and journals NOTHING — the C8 refusal (rule 3), `not-applicable`
# (rule 2), `session-unregistered` (rule 4), and function 11's idempotent no-op
# (invariant 2). Replaying them may not move a single byte of the golden.
REPLAYED_READS: tuple[tuple[str, dict[str, str]], ...] = (
    ("end-session", {"sessionId": STEP_SESSION}),
    ("resolve-step-instructions", {"sessionId": STEP_SESSION}),
    ("resolve-step-skills", {"sessionId": STEP_SESSION}),
    ("check-step-postconditions", {"sessionId": STEP_SESSION}),
    ("resolve-step-instructions", {"sessionId": "ghost-session"}),
    ("check-step-postconditions", {"sessionId": "ghost-session"}),
)


def _parse_instant(timestamp: str) -> datetime:
    """Read one entry's write time, whatever UTC rendering the service used."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _capture_golden(harness: FunctionalHarness) -> dict[str, tuple[str, ...]]:
    """Capture every session log as a golden fixture on a canonical clock.

    Entries keep their captured chronological order across logs — the cross-log total
    ordering key the instance view sorts on — while their wall-clock values are replaced
    by a fixed sequence, so a golden set is comparable byte for byte.
    """
    logs_dir = harness.workspace_dir / "logs"
    captured: list[tuple[datetime, str, int, dict[str, Any]]] = []
    for path in sorted(logs_dir.glob(f"*{LOG_FILE_SUFFIX}")):
        for position, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line:
                continue
            entry = json.loads(line)
            captured.append((_parse_instant(entry["timestamp"]), path.name, position, entry))
    captured.sort(key=lambda item: item[0])

    golden: dict[str, list[str]] = {}
    for position, (_, name, _, entry) in enumerate(captured):
        entry["timestamp"] = CANONICAL_INSTANT.format(position=position)
        golden.setdefault(name, []).append(_render(entry))
    return {name: tuple(lines) for name, lines in golden.items()}


def _render(entry: Mapping[str, Any]) -> str:
    """Render one entry exactly as the JSONL store persists it, byte for byte."""
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def _seed_golden(harness: FunctionalHarness, golden: GoldenLogs) -> None:
    """Write a golden fixture set into a fresh workspace's logs directory."""
    logs_dir = harness.workspace_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for name, lines in golden.items():
        (logs_dir / name).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _read_logs(harness: FunctionalHarness) -> dict[str, tuple[str, ...]]:
    """Read back every session log of one workspace, per file, as raw lines."""
    logs_dir = harness.workspace_dir / "logs"
    return {
        path.name: tuple(
            line for line in path.read_text(encoding="utf-8").splitlines() if line
        )
        for path in sorted(logs_dir.glob(f"*{LOG_FILE_SUFFIX}"))
    }


def _merged_instance_view(golden: GoldenLogs) -> tuple[tuple[str, str], ...]:
    """Merge every log's entries into the instance view: timestamp-ordered, cross-log.

    Spec (Logging, Instance views): a workflow instance owns no file — its ordered,
    replayable history is the union of the entries carrying its `workflowInstanceId`
    across the session logs, sorted by `timestamp`.
    """
    entries = [
        json.loads(line) for lines in golden.values() for line in lines
    ]
    entries.sort(key=lambda entry: entry["timestamp"])
    return tuple(
        (entry["report"]["context"]["function"], entry["report"]["outcome"]["status"])
        for entry in entries
        if entry["report"]["context"]["workflowInstanceId"] is not None
    )


def _drive_scenario(
    harness: FunctionalHarness,
    primary: str,
    secondary: str,
    *,
    hand_off: bool = False,
) -> None:
    """Drive one multi-session, multi-step scenario over the real entry point.

    The first orchestrator session resolves and dispatches `draft`, whose own agent
    session loads its context and closes; the step's outcome journals; the session ends.
    A second orchestrator session then opens, carrying no instance history of its own —
    and, on a hand-off, resolves the instance's next step into ITS log, so the instance's
    history only reads correctly once merged across both logs.
    """
    harness.invoke("start-session", sessionId=primary, agent="orchestrator")
    harness.invoke("resolve-step", sessionId=primary, workflowSlug="planning")
    harness.invoke(
        "start-session",
        sessionId=STEP_SESSION,
        parentSessionId=primary,
        agent="builder",
    )
    harness.invoke(
        "resolve-step-instructions", sessionId=STEP_SESSION, parentSessionId=primary
    )
    harness.invoke("resolve-step-skills", sessionId=STEP_SESSION, parentSessionId=primary)
    harness.invoke("end-session", sessionId=STEP_SESSION)
    harness.invoke("check-step-postconditions", sessionId=primary)
    harness.invoke("end-session", sessionId=primary)
    harness.invoke("start-session", sessionId=secondary, agent="orchestrator")
    if hand_off:
        harness.invoke("resolve-step", sessionId=secondary, workflowSlug="planning")


def _replay_golden(
    build_harness: HarnessBuilder, golden: GoldenLogs
) -> FunctionalHarness:
    """Seed a fresh, otherwise empty workspace with one golden fixture set."""
    replay = build_harness()
    _seed_golden(replay, golden)
    return replay


def _instance_id(golden: GoldenLogs) -> str:
    """Read the workflow instance the golden fixture set carries."""
    for lines in golden.values():
        for line in lines:
            instance = json.loads(line)["report"]["context"]["workflowInstanceId"]
            if instance is not None:
                return instance
    raise AssertionError("the golden fixture carries no workflow instance")


class TestLogReplay:
    """The determinism check: replay moves no byte, and the merge holds an order."""

    def test_a_captured_golden_set_is_a_contract_valid_journal(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (Logging, the entry contract): every entry is the record of exactly one
        completed invocation — the captured fixture is a real journal, each of its lines
        valid against the log-entry contract."""
        source = build_harness()
        _drive_scenario(source, "alpha-orchestrator", "zulu-orchestrator")

        golden = _capture_golden(source)

        assert set(golden) == {
            "alpha-orchestrator.log.jsonl",
            "draft-session.log.jsonl",
            "zulu-orchestrator.log.jsonl",
        }
        for lines in golden.values():
            for line in lines:
                assert source.validate_log_entry(json.loads(line)) == ()
        assert _merged_instance_view(golden) == (
            ("resolve-step", "step-resolution"),
            ("resolve-step-instructions", "resolved"),
            ("resolve-step-skills", "resolved"),
            ("check-step-postconditions", "pass"),
        )

    def test_replaying_a_golden_set_leaves_every_log_byte_identical(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (Functional testing): re-running against a golden fixture set of session
        logs is byte-stable per log — the reads the harness replays at a boundary it has
        already crossed return their reports and move nothing."""
        source = build_harness()
        _drive_scenario(source, "alpha-orchestrator", "zulu-orchestrator")
        golden = _capture_golden(source)
        replay = _replay_golden(build_harness, golden)

        for function, inquiry in REPLAYED_READS:
            assert_contract_round_trip(replay, replay.invoke(function, **inquiry))

        assert _read_logs(replay) == golden

    def test_replaying_one_read_twice_answers_byte_identical_reports(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (Logging, Every other status is derived): no entity owns a stored status
        — every verdict is recomputed from the journaled entries, so a read replayed
        against unchanged logs answers the very same bytes."""
        source = build_harness()
        _drive_scenario(source, "alpha-orchestrator", "zulu-orchestrator")
        golden = _capture_golden(source)
        replay = _replay_golden(build_harness, golden)

        for function, inquiry in REPLAYED_READS:
            first = replay.invoke(function, **inquiry)
            second = replay.invoke(function, **inquiry)
            assert first.stdout == second.stdout, function
            assert first.stdout.strip() != ""

    def test_the_instance_view_merges_across_session_logs(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (Logging, Instance views): a workflow instance owns no file — its history
        is the union of the entries across the session logs, discovered by scanning
        `<workspace>/logs/`, so a session carrying no instance entry of its own still
        resolves the instance's next step from another session's journal."""
        source = build_harness()
        _drive_scenario(source, "alpha-orchestrator", "zulu-orchestrator")
        golden = _capture_golden(source)
        replay = _replay_golden(build_harness, golden)
        assert len(golden["zulu-orchestrator.log.jsonl"]) == 1

        run = replay.invoke(
            "resolve-step", sessionId="zulu-orchestrator", workflowSlug="planning"
        )

        report = assert_contract_round_trip(replay, run)
        assert report["context"]["workflowInstanceId"] == _instance_id(golden)
        assert report["step"]["slug"] == "review"

    def test_the_merged_view_is_stable_under_log_discovery_order(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (Logging, Ordering): `timestamp` is the cross-log total ordering key —
        entries sort into ONE order regardless of which session log wrote which entry, so
        swapping the session ids (and with them the scanned file order) leaves both the
        merged instance view and the resolution it drives unchanged."""
        ascending = build_harness()
        _drive_scenario(ascending, "alpha-orchestrator", "zulu-orchestrator")
        descending = build_harness()
        _drive_scenario(descending, "zulu-orchestrator", "alpha-orchestrator")
        first_golden = _capture_golden(ascending)
        second_golden = _capture_golden(descending)

        assert _merged_instance_view(first_golden) == _merged_instance_view(second_golden)

        resolutions = tuple(
            _resolve_next(build_harness, golden, secondary)
            for golden, secondary in (
                (first_golden, "zulu-orchestrator"),
                (second_golden, "alpha-orchestrator"),
            )
        )
        assert resolutions[0] == resolutions[1] == ("step-resolution", "review", True)

    def test_a_hand_off_keeps_the_in_flight_step_visible_in_both_orders(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (Logging, Ordering + function 3, invariant 9): a hand-off between driving
        sessions is just a `timestamp`-ordered continuation — the step the SECOND session
        resolved is in flight for the instance whichever way the log files scan, so a
        further resolution is refused in both orders."""
        ascending = build_harness()
        _drive_scenario(
            ascending, "alpha-orchestrator", "zulu-orchestrator", hand_off=True
        )
        descending = build_harness()
        _drive_scenario(
            descending, "zulu-orchestrator", "alpha-orchestrator", hand_off=True
        )
        first_golden = _capture_golden(ascending)
        second_golden = _capture_golden(descending)

        assert _merged_instance_view(first_golden) == _merged_instance_view(
            second_golden
        )

        for golden, secondary in (
            (first_golden, "zulu-orchestrator"),
            (second_golden, "alpha-orchestrator"),
        ):
            replay = _replay_golden(build_harness, golden)
            run = replay.invoke(
                "resolve-step", sessionId=secondary, workflowSlug="planning"
            )
            report = assert_contract_round_trip(replay, run)
            assert report["outcome"]["status"] == "state-error", secondary
            assert run.error_code == "step-in-flight", secondary


def _resolve_next(
    build_harness: HarnessBuilder, golden: GoldenLogs, session_id: str
) -> tuple[str, str | None, bool]:
    """Replay one golden set, resolving the next step of its instance from `session_id`."""
    replay = _replay_golden(build_harness, golden)
    run = replay.invoke("resolve-step", sessionId=session_id, workflowSlug="planning")
    report = assert_contract_round_trip(replay, run)
    step = report.get("step")
    return (
        report["outcome"]["status"],
        None if step is None else step["slug"],
        report["context"]["workflowInstanceId"] == _instance_id(golden),
    )
