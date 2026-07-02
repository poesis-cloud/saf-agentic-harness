"""Run-journal TEST — the enveloped journal validates per command and replays by step.

DESIGN-TIME test for Stage D (logging → run journal): every journal entry is one base ENVELOPE
wrapping a per-command typed PAYLOAD (a discriminated union, ``oneOf`` on ``command``); the
``check-step`` / ``hook`` / ``orchestrate`` payloads each validate against their schema, a malformed
payload is rejected, and a run REPLAYS from the journal — grouping the entries by ``step``
reconstructs each step and the ordered entries give the run's step sequence. Run via ``make verify``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jsonschema

from mappers import LogRepository, SchemaRepository, Workspace


def _validator() -> jsonschema.Draft7Validator:
    """The envelope validator with a $ref resolver over the per-command payload schemas."""
    ws = Workspace.detect()
    schemas = SchemaRepository(ws)
    envelope = schemas.journal_schema()
    assert envelope is not None, "journal.schema.json must exist"
    store = schemas.journal_payload_store()
    store[envelope["$id"]] = envelope
    resolver = jsonschema.RefResolver.from_schema(envelope, store=store)
    return jsonschema.Draft7Validator(envelope, resolver=resolver)


def _errors(validator: jsonschema.Draft7Validator, entry: dict) -> list[str]:
    return [e.message for e in validator.iter_errors(entry)]


# --- sample entries (one per command) ---------------------------------------
_CHECK_STEP = {
    "command": "check-step", "trigger": "agent", "orchestration": "lpm",
    "step": "capture-epic", "unit": "E-01", "actor": "value-management-officier", "status": "completed",
    "payload": {
        "conditions": [{"kind": "postcondition", "type": "output", "expression": "ref", "value": "epic", "result": "pass"}],
        "outputs": ["portfolio/epics/E-01.md"], "counts": {"pass": 1, "fail": 0, "skipped": 0},
    },
}
_HOOK = {
    "command": "hook", "trigger": "host", "session": "abc123", "actor": "product-manager", "status": "allow",
    "payload": {"event": "preToolUse", "phase": "precondition", "permission": "allow", "reason": "", "outputs": ["portfolio/p/features/F-01.md"]},
}
_ORCHESTRATE = {
    "command": "orchestrate", "trigger": "agent", "run": "R-01", "orchestration": "lpm",
    "step": "capture-epic", "unit": "E-01", "status": "dispatch",
    "payload": {"action": "dispatch", "workflow": "lpm", "step": "capture-epic", "actor": "@value-management-officier", "model": "GPT-5.4 (copilot)", "unit": "E-01", "output": "epic"},
}


def main() -> int:
    validator = _validator()
    failures: list[str] = []

    # 1. Each per-command entry validates against the envelope + its payload schema (oneOf on command).
    for name, entry in (("check-step", _CHECK_STEP), ("hook", _HOOK), ("orchestrate", _ORCHESTRATE)):
        errs = _errors(validator, entry)
        if errs:
            failures.append(f"{name} entry should validate, got: {errs}")

    # 2. A malformed payload is rejected (check-step missing its required `conditions`).
    bad = {"command": "check-step", "step": "x", "status": "completed", "payload": {"outputs": []}}
    if not _errors(validator, bad):
        failures.append("a check-step entry with no conditions must be rejected")

    # 3. The wrong payload under a command is rejected (hook payload carried as a check-step).
    mismatched = {"command": "check-step", "step": "x", "payload": _HOOK["payload"]}
    if not _errors(validator, mismatched):
        failures.append("a check-step entry carrying a hook payload must be rejected")

    # 4. Replay: append a run's entries to a journal, read it back, and reconstruct the steps.
    ws = Workspace.detect()
    logs = LogRepository(ws)
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "R-01.jsonl"
        # the run's step sequence (draft -> review -> commit), each bracketed by several commands
        logs.append_entry(journal, command="orchestrate", payload={"action": "dispatch", "step": "draft"}, run="R-01", step="draft", status="dispatch")
        logs.append_entry(journal, command="check-step", payload={"conditions": []}, step="draft", status="completed")
        logs.append_entry(journal, command="orchestrate", payload={"action": "dispatch", "step": "review"}, run="R-01", step="review", status="dispatch")
        logs.append_entry(journal, command="check-step", payload={"conditions": []}, step="review", status="completed")
        logs.append_entry(journal, command="orchestrate", payload={"action": "dispatch", "step": "commit"}, run="R-01", step="commit", status="dispatch")
        logs.append_entry(journal, command="hook", payload={"event": "stop", "phase": "session-close", "permission": "allow"}, session="abc123", status="allow")

        log = logs.read(journal)
        assert log is not None
        if log.replay_steps() != ["draft", "review", "commit"]:
            failures.append(f"replay should reconstruct the step sequence, got {log.replay_steps()}")
        groups = log.by_step()
        if {step: len(entries) for step, entries in groups.items()} != {"draft": 2, "review": 2, "commit": 1}:
            failures.append(f"grouping by step should reconstruct each step's command entries, got { {s: len(e) for s, e in groups.items()} }")
        if [e.command for e in groups.get("draft", [])] != ["orchestrate", "check-step"]:
            failures.append("a step's entries should preserve command order (orchestrate then check-step)")
        if log.executed_steps() != ["draft", "review"]:
            failures.append(f"executed_steps should be the completed steps, got {log.executed_steps()}")

    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(f"\n{len(failures)} journal violation(s)")
        return 1
    print("pass  check-step / hook / orchestrate entries validate (envelope + payload, oneOf on command)")
    print("pass  malformed + mismatched payloads rejected")
    print("pass  run replays from the journal — grouping by step reconstructs each step")
    print("\npass: 0 journal violation(s)")
    return 0


def test_journal_envelope_and_replay() -> None:
    """pytest entry: the run-journal suite must report zero violations."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
