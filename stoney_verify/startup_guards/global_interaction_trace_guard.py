from __future__ import annotations

"""Evidence-only interaction tracing for slash-command failures.

This guard does not fix behavior. It proves whether Discord interactions reach
this process, whether command callbacks start, whether they hang, and whether
errors are global or isolated.
"""

import asyncio
import inspect
import time
import traceback
from typing import Any, Mapping

import discord

_PATCHED = False
_CALLBACKS_WRAPPED = False


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


def _find_interaction(args: tuple[Any, ...], kwargs: dict[str, Any]) -> discord.Interaction | None:
    for value in args:
        if isinstance(value, discord.Interaction):
            return value
    for value in kwargs.values():
        if isinstance(value, discord.Interaction):
            return value
    return None


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
    try:
        _log("received", interaction)
    except Exception:
        pass


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


def _command_label(command: Any) -> str:
    return str(
        getattr(command, "qualified_name", None)
        or getattr(command, "name", None)
        or type(command).__name__
    )


def _wrap_one_command(command: Any) -> bool:
    callback = getattr(command, "callback", None)
    if not callable(callback):
        return False
    if getattr(command, "_dank_trace_wrapped", False):
        return False

    label = _command_label(command)

    async def traced_callback(*args: Any, **kwargs: Any) -> Any:
        interaction = _find_interaction(args, kwargs)
        started = time.monotonic()
        finished = False

        if interaction is not None:
            _log("callback_start", interaction, callback=label)

        async def watchdog() -> None:
            await asyncio.sleep(2.5)
            if not finished and interaction is not None:
                age_ms = int((time.monotonic() - started) * 1000)
                _log("callback_slow_unacked", interaction, callback=label, age_ms=age_ms)

        watchdog_task = asyncio.create_task(watchdog()) if interaction is not None else None

        try:
            result = callback(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result

            if interaction is not None:
                age_ms = int((time.monotonic() - started) * 1000)
                _log("callback_ok", interaction, callback=label, age_ms=age_ms)

            return result
        except Exception as exc:
            if interaction is not None:
                age_ms = int((time.monotonic() - started) * 1000)
                _log("callback_exception", interaction, callback=label, age_ms=age_ms, error=f"{type(exc).__name__}: {str(exc)[:240]}")
            try:
                print("🧪 interaction_trace callback_traceback_start")
                print(traceback.format_exc())
                print("🧪 interaction_trace callback_traceback_end")
            except Exception:
                pass
            raise
        finally:
            finished = True
            if watchdog_task is not None:
                watchdog_task.cancel()

    try:
        command.callback = traced_callback
        command._dank_trace_wrapped = True
        return True
    except Exception:
        return False


def _wrap_registered_commands(bot: Any, *, reason: str) -> int:
    global _CALLBACKS_WRAPPED

    tree = getattr(bot, "tree", None)
    if tree is None:
        return 0

    wrapped = 0
    try:
        commands = list(tree.walk_commands())
    except Exception:
        try:
            commands = list(tree.get_commands())
        except Exception:
            commands = []

    for command in commands:
        if _wrap_one_command(command):
            wrapped += 1

    if wrapped:
        _CALLBACKS_WRAPPED = True
        print(f"🧪 interaction_trace callback wrappers attached count={wrapped} reason={reason}")

    return wrapped


async def _on_ready_wrap_callbacks() -> None:
    try:
        bot = _get_bot()
        if bot is not None:
            _wrap_registered_commands(bot, reason="on_ready")
    except Exception as exc:
        print(f"⚠️ interaction_trace callback wrap on_ready failed: {type(exc).__name__}: {exc}")


def _get_bot() -> Any:
    try:
        from stoney_verify import client as bot  # type: ignore
        return bot
    except Exception:
        try:
            from stoney_verify.globals import bot  # type: ignore
            return bot
        except Exception:
            return None


def apply() -> bool:
    global _PATCHED

    if _PATCHED:
        return True

    bot = _get_bot()
    if bot is None:
        print("⚠️ global_interaction_trace_guard waiting: bot unavailable")
        return False

    try:
        bot.add_listener(_on_interaction, "on_interaction")
        bot.add_listener(_on_ready_wrap_callbacks, "on_ready")

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

        _wrap_registered_commands(bot, reason="apply")

        _PATCHED = True
        print("🧪 global_interaction_trace_guard active; slash interaction and callback evidence logging attached")
        return True
    except Exception as exc:
        print(f"⚠️ global_interaction_trace_guard failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
