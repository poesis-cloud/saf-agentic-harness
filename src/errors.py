"""Harness exception types that model contract error outcomes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, Mapping


class HarnessError(Exception, ABC):
    """Abstract transport for contract `outcome.error` detail."""

    code: str
    message: str
    retryable: bool

    @property
    @abstractmethod
    def status(self) -> str:
        """Expose the report outcome status mapped by the concrete subtype."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        """Create a harness error with full contract error detail."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def render_error_detail(self) -> Mapping[str, str | bool]:
        """Render the contract error detail as read-only plain data."""
        return MappingProxyType(
            {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        )


class InquiryError(HarnessError):
    """Failure caused by an invalid or illegitimate inquiry."""

    status: ClassVar[str] = "inquiry-error"


class StateError(HarnessError):
    """Failure caused by persisted state contradicting a legitimate inquiry."""

    status: ClassVar[str] = "state-error"


class ConfigurationError(HarnessError):
    """Failure caused by invalid configuration observed at use time."""

    status: ClassVar[str] = "configuration-error"


class SystemFailureError(HarnessError):
    """Failure caused by the execution environment or filesystem."""

    status: ClassVar[str] = "system-error"


__all__ = [
    "ConfigurationError",
    "HarnessError",
    "InquiryError",
    "StateError",
    "SystemFailureError",
]
