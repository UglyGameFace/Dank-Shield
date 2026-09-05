from __future__ import annotations

"""Deprecated import-only compatibility shim for Server Design Studio.

Visible command registration is owned by ``public_design_group``. Importing this
module must never register commands or replace Studio behavior.
"""

from stoney_verify.commands_ext.public_design_studio import build_design_plan
from stoney_verify.commands_ext.public_design_studio_v2 import (
    DesignHomeView,
    _home_embed,
    _load_design_options,
    _require_design_permission,
    open_design_studio,
    register_public_design_studio_command,
)


def apply() -> bool:
    """Compatibility no-op retained while old imports are removed."""

    return True


__all__ = [
    "apply",
    "register_public_design_studio_command",
    "open_design_studio",
    "build_design_plan",
    "DesignHomeView",
    "_home_embed",
    "_load_design_options",
    "_require_design_permission",
]
