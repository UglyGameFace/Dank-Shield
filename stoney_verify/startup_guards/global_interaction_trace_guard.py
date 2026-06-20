from __future__ import annotations

"""Evidence-only interaction tracing for slash-command failures.

This guard does not fix behavior. It proves whether Discord interactions reach
this process, which command path ran, and whether errors are global or isolated.
"""

import time
import traceback
from typing import Any, Mapping

import discord

_PATCHED = False


def _trace_id(interaction: discord.Interaction) -> str:
    try:
        iid = int(getattr(interaction, "id", 0) or 0)
        return str(iid)[-8:] if iid else str(int(time.time() * 1000))[-8:]
    except Exception:
        return str(int(time.time() * 1000))[-8:]


def _interaction_names(interaction: discord.Interaction) -> list[str]:
    try:
        data = getattr(interaction, "data", None) or {}
        names: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, Mapping):
                name = node.get("name")
                if name is not None:
                    names.append(str(name))
                for child in node.get("options") or []:
                    walk(child)

        walk(data)
        return names
    except Exception:
        return []


def _response_done(interaction: discord.Interaction) -> bool:
    try:
        return bool(interaction.response.is_done())
    except Exception:
        return False


def _log(stage: str, interaction: discord.Interaction, **details: Any) -> None:
    try:
        guild_id = getattr(getattr(interaction, "guild", None), "id", 0)
        channel_id = getattr(getattr(interaction, "channel", None), "id", 0)
        user_id = getattr(getattr(interaction, "user", None), "id", 0)
        names = "/".join(_interaction_names(interaction)) or "unknown"
        detail_text = " ".join(f"{k}={v}" for k, v in details.items())

        print(
            "🧪 interaction_trace "
            f"trace={_trace_id(interaction)} "
            f"stage={stage} "
            f"type={getattr(interaction, 'type', None)} "
            f"cmd={names} "
            f"guild={guild_id} "
            f"channel={channel_id} "
            f"user={user_id} "
            f"response_done={_response_done(interaction)} "
            f"{detail_text}".strip()
        )
    except Exception:
        pass


async def _on_interaction(interaction: discord.Interaction) -> None:
    # Listener-level proof that Discord delivered the interaction to this process.
    try:
        _log("received", interaction)
    except Exception:
        pass


async def _tree_on_error(interaction: discord.Interaction, error: BaseException) -> None:
    # App-command proof when the command callback or checks throw.
    _log("tree_error", interaction, error=f"{type(error).__name__}: {str(error)[:240]}")
    try:
        print("🧪 interaction_trace traceback_start")
        print("".join(traceback.format_exception(type(error), error, error.__traceback__)))
        print("🧪 interaction_trace traceback_end")
    except Exception:
        pass

    # Try to give Discord a response if nothing else did. Evidence first; no guessing.
    try:
        msg = (
            "⚠️ This command failed before it could finish. "
            "The error was logged for staff to diagnose."
        )
        if _response_done(interaction):
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    bot = None
    try:
        from stoney_verify import client as bot  # type: ignore
    except Exception:
        try:
            from stoney_verify.globals import bot  # type: ignore
        except Exception:
            bot = None  # type: ignore

    if bot is None:
        print("⚠️ global_interaction_trace_guard waiting: bot unavailable")
        return False

    try:
        bot.add_listener(_on_interaction, "on_interaction")

        tree = getattr(bot, "tree", None)
        if tree is not None:
            existing = getattr(tree, "on_error", None)

            async def chained_on_error(interaction: discord.Interaction, error: BaseException) -> None:
                await _tree_on_error(interaction, error)
                if callable(existing) and existing is not _tree_on_error:
                    try:
                        await existing(interaction, error)
                    except TypeError:
                        pass
                    except Exception:
                        pass

            tree.on_error = chained_on_error

        _PATCHED = True
        print("🧪 global_interaction_trace_guard active; slash interaction evidence logging attached")
        return True
    except Exception as exc:
        print(f"⚠️ global_interaction_trace_guard failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
