from __future__ import annotations

"""Late setup-only presentation compatibility.

Dank Design used to be mutated from this setup compatibility module after its
canonical V2 owner loaded. That split runtime ownership made the live UI differ
from the reviewed source and allowed impossible controls such as a protected
items button when the preview contained no protected rows.

Server Design now owns its pagination, icon customization, issue review, and
preview controls natively. This module is intentionally limited to the compact
setup-menu cleanup that still needs a late compatibility hook.
"""

from stoney_verify.setup_ui import public_setup_compact as compact_ui

_PATCHED = False
_ORIGINAL_COMPACT_ADVANCED_VIEW = compact_ui.CompactAdvancedView


class CompactAdvancedWithoutDuplicateFeatures(_ORIGINAL_COMPACT_ADVANCED_VIEW):
    def __init__(self) -> None:
        super().__init__()
        for child in list(self.children):
            if str(getattr(child, "custom_id", "") or "") == "dank_setup_advanced:features":
                self.remove_item(child)


def apply_runtime_ux_repairs() -> None:
    global _PATCHED
    if _PATCHED:
        return

    compact_ui.CompactAdvancedView = CompactAdvancedWithoutDuplicateFeatures
    _PATCHED = True
    print("✅ public_runtime_ux_repairs active; duplicate setup feature entry removed")


__all__ = [
    "CompactAdvancedWithoutDuplicateFeatures",
    "apply_runtime_ux_repairs",
]
