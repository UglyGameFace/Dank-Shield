from __future__ import annotations

"""Deprecated compatibility shim for the old Server Design Studio startup guard.

The real /dank design command now lives in:
    stoney_verify.commands_ext.public_design_studio

This file must not call apply() at import time.
Remove this shim after runtime verification confirms no imports still depend on it.
"""

from typing import Any

from stoney_verify.commands_ext.public_design_studio import (
    DesignHomeView,
    _home_embed,
    _load_design_options,
    _require_design_permission,
    build_design_plan,
    open_design_studio,
    register_public_design_studio_command,
)


def apply() -> bool:
    return register_public_design_studio_command()


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
