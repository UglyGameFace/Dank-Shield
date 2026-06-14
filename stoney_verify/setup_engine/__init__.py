from __future__ import annotations

from .diff import build_setup_health_report, build_legacy_health_lists
from .models import (
    FindingSeverity,
    HealthFinding,
    RepairMutation,
    RepairPlan,
    SetupConfigSnapshot,
    SetupHealthReport,
)

ENGINE_VERSION = "setup-engine-v1"

__all__ = [
    "ENGINE_VERSION",
    "FindingSeverity",
    "HealthFinding",
    "RepairMutation",
    "RepairPlan",
    "SetupConfigSnapshot",
    "SetupHealthReport",
    "build_setup_health_report",
    "build_legacy_health_lists",
]
