"""The package entitlement gate.

With the migration to time-based plans (1, 3, 6, 12 months), all active plans
have unlimited trading entitlement. This module now just passes through, but is
kept to preserve the existing F&O engine interfaces.
"""
from __future__ import annotations

from sqlalchemy.orm import Session


def allows(db: Session, user_id: str, package_id: str, side: str) -> tuple[bool, str]:
    """Return (allowed, reason). All time-based plans are unlimited."""
    return True, "unlimited"


def consume(db: Session, user_id: str, package_id: str, side: str) -> None:
    """Record a successful entry against the weekly counters (no-op)."""
    pass
