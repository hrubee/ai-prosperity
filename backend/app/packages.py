"""Package definitions — server-side source of truth for the entitlement gate.
Mirrors frontend/lib/packages.ts. Keep the two in sync.
"""
from dataclasses import dataclass
from typing import Literal

PackageId = Literal["starter", "growth", "pro"]


@dataclass(frozen=True)
class PackageRule:
    id: PackageId
    name: str
    price_inr: int
    # kind drives the entitlement gate
    kind: Literal["trades_per_week", "trading_days_per_week", "unlimited"]
    max: int  # ignored when kind == "unlimited"


PACKAGES: dict[str, PackageRule] = {
    "starter": PackageRule("starter", "Starter", 5000, "trades_per_week", 1),
    "growth": PackageRule("growth", "Growth", 10000, "trading_days_per_week", 2),
    "pro": PackageRule("pro", "Pro", 20000, "unlimited", 0),
}


def get_rule(package_id: str) -> PackageRule:
    rule = PACKAGES.get(package_id)
    if rule is None:
        raise ValueError(f"unknown package: {package_id}")
    return rule
