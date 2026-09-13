from __future__ import annotations

"""Compatibility entrypoint for the AntiNuke gateway runtime.

Production boot still imports this historical module name. The implementation now
lives in ``anti_nuke_guardian_runtime`` so there is exactly one audit gateway
listener and one canonical AntiNuke policy engine.
"""

import discord

from .anti_nuke_guardian_runtime import install_anti_nuke_guardian_runtime


def install_anti_nuke_gateway_runtime(bot: discord.Client) -> bool:
    return install_anti_nuke_guardian_runtime(bot)


__all__ = ["install_anti_nuke_gateway_runtime"]
