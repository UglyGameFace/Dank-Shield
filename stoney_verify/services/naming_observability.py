from __future__ import annotations

"""Fixed-cardinality process metrics for Search-Safe Naming.

These counters deliberately avoid guild/resource/user labels so public scale
cannot turn diagnostics into an unbounded in-memory time series.
"""

from collections import Counter
from typing import Final


_METRIC_NAMES: Final[tuple[str, ...]] = (
    "role_resolution_live",
    "role_resolution_semantic",
    "role_resolution_alias",
    "role_resolution_ambiguous",
    "state_write_success",
    "state_write_conflict",
    "state_write_failure",
    "alias_pruned",
    "resource_pruned",
    "auto_change_role",
    "auto_change_channel",
    "auto_blocked",
    "auto_failed",
    "repair_changed",
    "repair_blocked",
    "repair_failed",
)

_METRICS: Counter[str] = Counter()


def increment(name: str, amount: int = 1) -> None:
    key = str(name or "").strip()
    if key not in _METRIC_NAMES:
        return
    try:
        delta = int(amount)
    except Exception:
        return
    if delta <= 0:
        return
    _METRICS[key] += delta


def snapshot() -> dict[str, int]:
    return {name: int(_METRICS.get(name, 0)) for name in _METRIC_NAMES}


def _reset_for_tests() -> None:
    _METRICS.clear()


__all__ = ["increment", "snapshot"]
