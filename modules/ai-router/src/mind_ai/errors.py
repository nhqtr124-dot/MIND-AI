from __future__ import annotations

from typing import Literal

ErrorKind = Literal["auth", "permission", "rate_limit", "invalid_request", "not_found", "unavailable", "timeout", "network", "unsupported", "unknown"]


class ProviderError(Exception):
    """A failed provider call. ``message`` is safe to show users (no secrets)."""

    def __init__(self, kind: ErrorKind, message: str, status: int | None = None, provider: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.provider = provider

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limit", "unavailable", "timeout", "network")

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "message": self.message, "status": self.status, "provider": self.provider, "retryable": self.retryable}


def kind_for_status(status: int) -> ErrorKind:
    if status == 401:
        return "auth"
    if status == 403:
        return "permission"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limit"
    if status in (400, 413, 422):
        return "invalid_request"
    if status in (408, 504):
        return "timeout"
    if status >= 500:
        return "unavailable"
    return "unknown"
