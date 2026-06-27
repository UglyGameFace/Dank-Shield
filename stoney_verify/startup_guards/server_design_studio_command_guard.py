from __future__ import annotations

"""Compatibility shim for the old Server Design Studio startup guard.

The real /dank design command now lives in:
    stoney_verify.commands_ext.public_design_studio

This shim intentionally re-exports the full implementation so older imports and
runtime tests that still reference startup_guards.server_design_studio_command_guard
continue to work while the product code remains in commands_ext.
"""

from stoney_verify.commands_ext import public_design_studio as _impl


def apply() -> bool:
    return _impl.register_public_design_studio_command()


for _name in dir(_impl):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_impl, _name)

globals()["apply"] = apply

__all__ = sorted(name for name in globals() if not name.startswith("__"))
