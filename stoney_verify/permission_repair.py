from __future__ import annotations

"""Canonical Fix Access module boundary.

The stable public API stays here. Mutation/audit behavior is retained in
``permission_repair_core`` while the production UI is owned by
``permission_repair_ui`` so channel/category discovery can use Dank Shield's
dedicated browser instead of Discord's generic native entity picker.
"""

from . import permission_repair_core as _core
from .permission_repair_core import (
    PermissionRepairState,
    TargetPermissionAudit,
    TargetRepairResult,
    approved_public_permissions,
    apply_target_repair,
    audit_target,
    audit_targets,
    build_preview_embed,
    reauthorize_url,
    undo_target_repair,
)
from .permission_repair_ui import (
    TargetChannelPickerView,
    TargetPermissionRepairView,
    TargetSearchModal,
    open_target_permission_repair,
)

# These classes were never in the old __all__, but repository tests and callers
# have historically reached them through stoney_verify.permission_repair.
ExplicitDenyConfirmView = _core.ExplicitDenyConfirmView
UndoTokenModal = _core.UndoTokenModal

# Keep return points inside the unchanged core implementation on the production
# UI owner. This is module composition, not a startup guard/runtime import hook.
_core.TargetPermissionRepairView = TargetPermissionRepairView
_core.open_target_permission_repair = open_target_permission_repair

__all__ = [
    "ExplicitDenyConfirmView",
    "PermissionRepairState",
    "TargetChannelPickerView",
    "TargetPermissionAudit",
    "TargetPermissionRepairView",
    "TargetRepairResult",
    "TargetSearchModal",
    "UndoTokenModal",
    "approved_public_permissions",
    "apply_target_repair",
    "audit_target",
    "audit_targets",
    "build_preview_embed",
    "open_target_permission_repair",
    "reauthorize_url",
    "undo_target_repair",
]
