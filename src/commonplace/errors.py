"""Stable domain errors shared by every adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DomainError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def fail(code: str, message: str, **details: Any) -> None:
    raise DomainError(code, message, details, code in {"database_busy", "service_unavailable"})
