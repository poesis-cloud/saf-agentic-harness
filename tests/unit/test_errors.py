"""Unit tests for the harness error model."""

from __future__ import annotations

import inspect

import pytest

from errors import (
    ConfigurationError,
    HarnessError,
    InquiryError,
    StateError,
    SystemFailureError,
)


class TestHarnessError:
    """Verify shared error detail behavior required by the report contract."""

    def test_defines_an_abstract_base_error(self) -> None:
        """Spec: errors.py exposes an abstract HarnessError base."""
        assert inspect.isabstract(HarnessError)

    def test_renders_contract_error_detail_as_read_only_plain_data(self) -> None:
        """Spec: HarnessError carries the contract error detail."""
        error = StateError("session-ended", "Session already ended.", retryable=False)

        detail = error.render_error_detail()

        assert dict(detail) == {
            "code": "session-ended",
            "message": "Session already ended.",
            "retryable": False,
        }
        with pytest.raises(TypeError):
            detail["code"] = "changed"  # type: ignore[index]


class TestInquiryError:
    """Verify inquiry error status mapping."""

    def test_maps_to_inquiry_error_status(self) -> None:
        """Spec: InquiryError maps to inquiry-error."""
        error = InquiryError("invalid-inquiry", "The inquiry is invalid.", retryable=False)

        assert error.status == "inquiry-error"
        assert InquiryError.status == "inquiry-error"
        assert error.code == "invalid-inquiry"
        assert error.message == "The inquiry is invalid."
        assert error.retryable is False
        assert str(error) == "The inquiry is invalid."


class TestStateError:
    """Verify state error status mapping."""

    def test_maps_to_state_error_status(self) -> None:
        """Spec: StateError maps to state-error."""
        error = StateError("session-ended", "Session already ended.", retryable=False)

        assert error.status == "state-error"
        assert StateError.status == "state-error"


class TestConfigurationError:
    """Verify configuration error status mapping."""

    def test_maps_to_configuration_error_status(self) -> None:
        """Spec: ConfigurationError maps to configuration-error."""
        error = ConfigurationError("configuration-mutated", "Configuration changed.", retryable=False)

        assert error.status == "configuration-error"
        assert ConfigurationError.status == "configuration-error"


class TestSystemFailureError:
    """Verify system failure status mapping."""

    def test_maps_to_system_error_status(self) -> None:
        """Spec: SystemFailureError maps to system-error."""
        error = SystemFailureError("disk-full", "Disk is full.", retryable=True)

        assert error.status == "system-error"
        assert SystemFailureError.status == "system-error"
        assert error.retryable is True
