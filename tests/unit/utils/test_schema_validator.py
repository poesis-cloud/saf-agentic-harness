"""Unit tests for JSON Schema 2020-12 validation mechanics."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import SchemaValidator, ValidationErrorRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = REPO_ROOT / "contracts"


class TestValidationErrorRecord:
    """Verify returned validation error data shape."""

    def test_carries_json_pointer_path_and_message_as_frozen_data(self) -> None:
        """Spec: validation errors are plain records with path and message."""
        record = ValidationErrorRecord(path="/sessionId", message="is invalid")

        assert record.path == "/sessionId"
        assert record.message == "is invalid"
        with pytest.raises(Exception):
            record.message = "changed"  # type: ignore[misc]


class TestSchemaValidator:
    """Verify contract compilation and raw validation reports."""

    def test_validates_real_inquiry_contract_instances(self) -> None:
        """Spec: SchemaValidator validates instances against real JSON Schema contracts."""
        validator = SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))

        errors = validator.validate_instance(
            "gsmarc://saf/contracts/inquiry/v1",
            {"sessionId": "session-1", "parentSessionId": None},
        )

        assert errors == ()

    def test_returns_error_records_without_raising_for_invalid_instances(self) -> None:
        """Spec: validate_instance returns validation reports and does not raise on invalid data."""
        validator = SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))

        errors = validator.validate_instance(
            "gsmarc://saf/contracts/inquiry/v1",
            {"sessionId": "Bad/Session"},
        )

        assert all(isinstance(error, ValidationErrorRecord) for error in errors)
        assert any(error.path == "/sessionId" for error in errors)
        assert any("does not match" in error.message for error in errors)

    def test_resolves_cross_file_refs_by_gsmarc_uri(self) -> None:
        """Spec: cross-file $refs resolve by Archetype URI, never filesystem path."""
        validator = SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))
        entry = {
            "timestamp": "2026-07-08T14:32:07Z",
            "report": {
                "context": {"function": "end-session", "sessionId": "session-1"},
                "outcome": {"status": "ended"},
            },
        }

        errors = validator.validate_instance("gsmarc://saf/contracts/log-entry/v1", entry)

        assert errors == ()
        with pytest.raises(KeyError):
            validator.validate_instance(str(CONTRACTS_ROOT / "log-entry.schema.json"), entry)

    def test_validates_cross_file_slug_refs_in_api_contracts(self) -> None:
        """Spec: API contracts reuse slugs through gsmarc:// cross-file refs."""
        validator = SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))

        errors = validator.validate_instance(
            "gsmarc://saf/contracts/api/start-session.input/v1",
            {"sessionId": "session-1", "agent": "not a slug"},
        )

        assert any(error.path == "/agent" for error in errors)
