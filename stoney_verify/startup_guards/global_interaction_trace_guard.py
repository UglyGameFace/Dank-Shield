from __future__ import annotations

"""Dank Shield evidence-only interaction tracing.

This guard does not fix behavior. It proves whether Discord interactions reach
this process, whether Discord.py dispatch starts, whether command callbacks run,
and whether failures are global, group-level, or command-specific.
"""

import asyncio
import os
import time
import traceback
from typing import Any, Mapping

import discord
from discord import app_commands

_PATCHED = False


def _trace_enabled() -> bool:
    value = os.getenv("DANK_SHIELD_INTERACTION_TRACE", "true").strip().lower()
    return value not in {"0", "false", "off", "no"}


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


def _response_done(interaction: discord.Interaction | None) -> bool:
    if interaction is None:
        return False
    try:
        return bool(interaction.response.is_done())
    except Exception:
        return False


def _find_interaction(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> discord.Interaction | None:
    for value in args:
        if isinstance(value, discord.Interaction):
            return value
    for value in kwargs.values():
        if isinstance(value, discord.Interaction):
            return value
    return None


def _command_label(command: Any) -> str:
    return str(
        getattr(command, "qualified_name", None)
        or getattr(command, "name", None)
        or type(command).__name__
    )


def _log(stage: str, interaction: discord.Interaction | None, **details: Any) -> None:
    if not _trace_enabled():
        return

    try:
        guild_id = getattr(getattr(interaction, "guild", None), "id", 0) if interaction is not None else 0
        channel_id = getattr(getattr(interaction, "channel", None), "id", 0) if interaction is not None else 0
        user_id = getattr(getattr(interaction, "user", None), "id", 0) if interaction is not None else 0
        names = "/".join(_interaction_names(interaction)) if interaction is not None else "unknown"
        names = names or "unknown"
        trace = _trace_id(interaction) if interaction is not None else str(int(time.time() * 1000))[-8:]
        detail_text = " ".join(f"{k}={v}" for k, v in details.items())

        print(
            "🧪 interaction_trace "
            f"trace={trace} "
            f"stage={stage} "
            f"type={getattr(interaction, 'type', None) if interaction is not None else None} "
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
    _log("received", interaction)


async def _tree_on_error(interaction: discord.Interaction, error: BaseException) -> None:
    _log("tree_error", interaction, error=f"{type(error).__name__}: {str(error)[:240]}")
    try:
        print("🧪 interaction_trace traceback_start")
        print("".join(traceback.format_exception(type(error), error, error.__traceback__)))
        print("🧪 interaction_trace traceback_end")
    except Exception:
        pass

    try:
        msg = "⚠️ This command failed before it could finish. The error was logged for staff to diagnose."
        if _response_done(interaction):
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


def _patch_tree_call() -> bool:
    tree_cls = app_commands.CommandTree

    if getattr(tree_cls, "_dank_shield_trace_tree_call_wrapped", False):
        return False

    original = getattr(tree_cls, "_call", None)
    if original is None:
        return False

    async def traced_tree_call(self: app_commands.CommandTree, interaction: discord.Interaction) -> Any:
        started = time.monotonic()
        _log("tree_call_start", interaction)

        async def watchdog() -> None:
            await asyncio.sleep(2.5)
            _log("tree_call_slow_unacked", interaction, age_ms=int((time.monotonic() - started) * 1000))

        task = asyncio.create_task(watchdog())
        try:
            result = await original(self, interaction)
            _log("tree_call_ok", interaction, age_ms=int((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            _log(
                "tree_call_exception",
                interaction,
                age_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
            try:
                print("🧪 interaction_trace tree_call_traceback_start")
                print(traceback.format_exc())
                print("🧪 interaction_trace tree_call_traceback_end")
            except Exception:
                pass
            raise
        finally:
            task.cancel()

    setattr(tree_cls, "_dank_shield_trace_original_tree_call", original)
    setattr(tree_cls, "_dank_shield_trace_tree_call_wrapped", True)
    setattr(tree_cls, "_call", traced_tree_call)
    return True


def _patch_command_method(cls: Any, method_name: str, start_stage: str, ok_stage: str, exception_stage: str) -> bool:
    if cls is None:
        return False

    wrapped_marker = f"_dank_shield_trace_{method_name}_wrapped"
    original_marker = f"_dank_shield_trace_original_{method_name}"

    if getattr(cls, wrapped_marker, False):
        return False

    original = getattr(cls, method_name, None)
    if original is None:
        return False

    async def traced_method(self: Any, *args: Any, **kwargs: Any) -> Any:
        interaction = _find_interaction(args, kwargs)
        label = _command_label(self)
        started = time.monotonic()
        _log(start_stage, interaction, command=label)

        async def watchdog() -> None:
            await asyncio.sleep(2.5)
            _log(f"{start_stage}_slow_unacked", interaction, command=label, age_ms=int((time.monotonic() - started) * 1000))

        task = asyncio.create_task(watchdog()) if interaction is not None else None
        try:
            result = await original(self, *args, **kwargs)
            _log(ok_stage, interaction, command=label, age_ms=int((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            _log(
                exception_stage,
                interaction,
                command=label,
                age_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )
            try:
                print(f"🧪 interaction_trace {method_name}_traceback_start")
                print(traceback.format_exc())
                print(f"🧪 interaction_trace {method_name}_traceback_end")
            except Exception:
                pass
            raise
        finally:
            if task is not None:
                task.cancel()

    setattr(cls, original_marker, original)
    setattr(cls, wrapped_marker, True)
    setattr(cls, method_name, traced_method)
    return True


def _patch_framework_dispatch() -> dict[str, bool]:
    try:
        from discord.app_commands import commands as command_module
    except Exception:
        command_module = None

    command_cls = getattr(command_module, "Command", None)
    group_cls = getattr(command_module, "Group", None)

    results = {
        "tree_call": _patch_tree_call(),
        "command_invoke": _patch_command_method(
            command_cls,
            "_invoke_with_namespace",
            "command_invoke_start",
            "command_invoke_ok",
            "command_invoke_exception",
        ),
        "command_do_call": _patch_command_method(
            command_cls,
            "_do_call",
            "command_do_call_start",
            "command_do_call_ok",
            "command_do_call_exception",
        ),
        "group_invoke": _patch_command_method(
            group_cls,
            "_invoke_with_namespace",
            "group_invoke_start",
            "group_invoke_ok",
            "group_invoke_exception",
        ),
    }
    return results


def _get_bot() -> Any:
    try:
        from stoney_verify import client as bot  # internal legacy package name
        return bot
    except Exception:
        try:
            from stoney_verify.globals import bot  # internal legacy package name
            return bot
        except Exception:
            return None


def apply() -> bool:
    global _PATCHED

    if _PATCHED:
        return True

    bot = _get_bot()
    if bot is None:
        print("⚠️ Dank Shield interaction trace waiting: bot unavailable")
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

        patched = _patch_framework_dispatch()

        _PATCHED = True
        print(f"✅ Dank Shield interaction evidence logger active; patched={patched}")
        return True
    except Exception as exc:
        print(f"⚠️ Dank Shield interaction evidence logger failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
