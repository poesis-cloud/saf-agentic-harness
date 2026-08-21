"""Function 8's result: the write boundary's authorization report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from services.checking.authorization import Authorization
from stores.session_log_store import Report


@dataclass(frozen=True)
class AuthorizationReport(Report):
    """Report one write's allow or deny, absent on the error outcomes.

    Spec (function 8, Interface): allow, or deny with an
    `authorization.failureMessage` naming the cause.
    """

    CONTRACT_ID: ClassVar[str] = (
        "gsmarc://saf/contracts/api/check-step-authorization.output/v1"
    )

    authorization: Authorization | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the envelope, adding `authorization` only where a decision was made.

        The output contract's error branch never evaluates `authorization`, and
        forbids unevaluated properties.
        """
        rendered = super().to_dict()
        if self.authorization is not None:
            rendered["authorization"] = self.authorization.to_dict()
        return rendered


__all__ = ["AuthorizationReport"]
