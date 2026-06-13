from __future__ import annotations

"""Startup alias for guild role order enforcement."""

from importlib import import_module

_mod = import_module("stoney_verify.startup_guards.role_hierarchy_action_guard")
apply = getattr(_mod, "apply")
apply()

__all__ = ["apply"]
