"""The Console's operator-facing error vocabulary. One table, one resolver."""
from __future__ import annotations

from dataclasses import dataclass

TIERS = ("committed", "recovery", "integrity", "refusal", "unknown")
SEVERITIES = frozenset({"refusal", "attention"})
RETRIES = frozenset({"retry", "reload", "recreate", "stop", "none"})
COMMITTED = frozenset({"no", "yes", "unknown"})
PAGE_STATUSES = frozenset({404, 409, 422, 500})


@dataclass(frozen=True)
class ConsoleError:
    code: str
    tier: str
    severity: str
    message: str
    retry: str
    committed: str
    page_status: int

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError("tier is not a permitted value")
        if self.severity not in SEVERITIES:
            raise ValueError("severity is not a permitted value")
        if self.retry not in RETRIES:
            raise ValueError("retry is not a permitted value")
        if self.committed not in COMMITTED:
            raise ValueError("committed is not a permitted value")
        if self.page_status not in PAGE_STATUSES:
            raise ValueError("page status is not a permitted value")
        if self.severity == "refusal" and self.committed != "no":
            raise ValueError("a refusal cannot report a commit")
        if self.tier == "committed" and (self.committed != "yes" or self.retry != "stop"):
            raise ValueError("a committed outcome must stop and report yes")
        if self.tier == "recovery" and (self.committed != "unknown" or self.retry != "stop"):
            raise ValueError("a recovery outcome must stop and report unknown")
