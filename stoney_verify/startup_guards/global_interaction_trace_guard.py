from __future__ import annotations

"""Dank Shield Elite Error Logger.

Production-safe interaction error logger with optional deep tracing.

Safety rules:
- Does not rename internal modules, packages, workers, or legacy process names.
- Error logging is always on.
- Verbose interaction tracing is off by default.
- Deep trace can be enabled with DANK_SHIELD_INTERACTION_TRACE=true.
"""

import asyncio
import hashlib
import inspect
import os
import time
import traceback
from typing import Any, Mapping

import discord
from discord import app_commands

_PATCHED = False
_ERROR_COUNTS: dict[str, tuple[float, int]] = {}
_BUTTON_SPAM_WINDOWS: dict[str, list[float]] = {}
_BUTTON_SPAM_LAST_LOG: dict[str, float] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() not in {"0", "false", "off", "no", ""}


def _trace_enabled() -> bool:
    return _env_bool("DANK_SHIELD_INTERACTION_TRACE", False)


def _error_logger_enabled() -> bool:
    return _env_bool("DANK_SHIELD_ELITE_ERROR_LOGGER", True)


def _component_trace_enabled() -> bool:
    return _env_bool("DANK_SHIELD_COMPONENT_TRACE", False) or _trace_enabled()


def _slow_ms() -> int:
    try:
        return max(250, int(os.getenv("DANK_SHIELD_SLOW_INTERACTION_MS", "2500")))
    except Exception:
        return 2500


def _button_spam_watch_enabled() -> bool:
    return _env_bool("DANK_SHIELD_BUTTON_SPAM_WATCH", True)


def _button_spam_window_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("DANK_SHIELD_BUTTON_SPAM_WINDOW_SECONDS", "8")))
    except Exception:
        return 8.0


def _button_spam_threshold() -> int:
    try:
        return max(2, int(os.getenv("DANK_SHIELD_BUTTON_SPAM_THRESHOLD", "5")))
    except Exception:
        return 5


def _button_spam_log_cooldown_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("DANK_SHIELD_BUTTON_SPAM_LOG_COOLDOWN_SECONDS", "15")))
    except Exception:
        return 15.0


def _component_spam_key(interaction: discord.Interaction) -> str:
    guild_id = getattr(getattr(interaction, "guild", None), "id", 0)
    user_id = getattr(getattr(interaction, "user", None), "id", 0)
    custom_id = _component_custom_id(interaction) or "unknown_component"
    return f"{guild_id}:{user_id}:{custom_id}"


def _track_button_spam(interaction: discord.Interaction) -> None:
    """Evidence-only repeated button/select click detector.

    This does not block, defer, punish, or mutate behavior.
    It only logs when one user repeatedly hits the same component quickly.
    """

    if not _button_spam_watch_enabled():
        return

    try:
        key = _component_spam_key(interaction)
        now = time.monotonic()
        window_s = _button_spam_window_seconds()
        threshold = _button_spam_threshold()

        hits = [stamp for stamp in _BUTTON_SPAM_WINDOWS.get(key, []) if now - stamp <= window_s]
        hits.append(now)
        _BUTTON_SPAM_WINDOWS[key] = hits

        count = len(hits)
        if count < threshold:
            return

        last_log = _BUTTON_SPAM_LAST_LOG.get(key, 0.0)
        if now - last_log < _button_spam_log_cooldown_seconds():
            return

        _BUTTON_SPAM_LAST_LOG[key] = now

        fields = _base_fields(interaction)
        fields.update(
            {
                "custom_id": _component_custom_id(interaction),
                "component_type": _component_type(interaction),
                "count": count,
                "threshold": threshold,
                "window_s": window_s,
            }
        )
        _print_event("⚠️ dank_button_spam", fields)
    except Exception:
        pass


def _rate_limit_window_seconds() -> int:
    try:
        return max(10, int(os.getenv("DANK_SHIELD_ERROR_RATE_WINDOW_SECONDS", "60")))
    except Exception:
        return 60


def _rate_limit_max() -> int:
    try:
        return max(1, int(os.getenv("DANK_SHIELD_ERROR_RATE_MAX", "8")))
    except Exception:
        return 8


def _trace_id(interaction: discord.Interaction | None = None) -> str:
    try:
        if interaction is not None:
            iid = int(getattr(interaction, "id", 0) or 0)
            if iid:
                return str(iid)[-8:]
    except Exception:
        pass
    return str(int(time.time() * 1000))[-8:]


def _interaction_names(interaction: discord.Interaction | None) -> list[str]:
    if interaction is None:
        return []
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


def _command_path(interaction: discord.Interaction | None) -> str:
    names = _interaction_names(interaction)
    if names:
        return "/".join(names)
    if interaction is None:
        return "unknown"
    try:
        data = getattr(interaction, "data", None) or {}
        custom_id = data.get("custom_id")
        if custom_id:
            return f"component:{custom_id}"
    except Exception:
        pass
    return "unknown"


def _component_custom_id(interaction: discord.Interaction | None) -> str:
    if interaction is None:
        return ""
    try:
        data = getattr(interaction, "data", None) or {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _component_type(interaction: discord.Interaction | None) -> str:
    if interaction is None:
        return ""
    try:
        data = getattr(interaction, "data", None) or {}
        return str(data.get("component_type") or "")
    except Exception:
        return ""


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


def _safe_detail(value: Any, limit: int = 220) -> str:
    try:
        text = str(value).replace("\n", "\\n").replace("\r", "\\r")
    except Exception:
        text = "<unprintable>"
    return text[:limit]


def _base_fields(interaction: discord.Interaction | None) -> dict[str, Any]:
    return {
        "trace": _trace_id(interaction),
        "cmd": _command_path(interaction),
        "guild": getattr(getattr(interaction, "guild", None), "id", 0) if interaction is not None else 0,
        "channel": getattr(getattr(interaction, "channel", None), "id", 0) if interaction is not None else 0,
        "user": getattr(getattr(interaction, "user", None), "id", 0) if interaction is not None else 0,
        "response_done": _response_done(interaction),
    }


def _print_event(prefix: str, fields: Mapping[str, Any]) -> None:
    try:
        parts = [f"{key}={_safe_detail(value)}" for key, value in fields.items()]
        print(f"{prefix} " + " ".join(parts))
    except Exception:
        pass


def _trace(stage: str, interaction: discord.Interaction | None, **details: Any) -> None:
    if not _trace_enabled():
        return
    fields = _base_fields(interaction)
    fields.update({"stage": stage})
    fields.update(details)
    _print_event("🧪 dank_interaction_trace", fields)


def _component_trace(stage: str, interaction: discord.Interaction | None, **details: Any) -> None:
    if not _component_trace_enabled():
        return
    fields = _base_fields(interaction)
    fields.update(
        {
            "stage": stage,
            "custom_id": _component_custom_id(interaction),
            "component_type": _component_type(interaction),
        }
    )
    fields.update(details)
    _print_event("🧪 dank_component_trace", fields)


def _error_id(interaction: discord.Interaction | None, error: BaseException | None = None, stage: str = "unknown") -> str:
    raw = "|".join(
        [
            stage,
            _command_path(interaction),
            str(getattr(getattr(interaction, "guild", None), "id", 0) if interaction is not None else 0),
            str(type(error).__name__ if error is not None else "NoError"),
            str(error)[:160] if error is not None else "",
            str(int(time.time() // 60)),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:8].upper()
    return f"DANK-{digest}"


def _rate_limited(error_id: str) -> bool:
    now = time.time()
    window = _rate_limit_window_seconds()
    max_count = _rate_limit_max()

    first_seen, count = _ERROR_COUNTS.get(error_id, (now, 0))
    if now - first_seen > window:
        _ERROR_COUNTS[error_id] = (now, 1)
        return False

    count += 1
    _ERROR_COUNTS[error_id] = (first_seen, count)
    return count > max_count


def _log_error(
    stage: str,
    interaction: discord.Interaction | None,
    error: BaseException,
    *,
    extra: Mapping[str, Any] | None = None,
) -> str:
    if not _error_logger_enabled():
        return _error_id(interaction, error, stage)

    eid = _error_id(interaction, error, stage)
    fields = _base_fields(interaction)
    fields.update(
        {
            "error_id": eid,
            "stage": stage,
            "error_type": type(error).__name__,
            "error": _safe_detail(error, 320),
        }
    )
    if extra:
        fields.update(extra)

    if not _rate_limited(eid):
        _print_event("🚨 dank_error", fields)
        try:
            print("🚨 dank_error traceback_start", f"error_id={eid}")
            print("".join(traceback.format_exception(type(error), error, error.__traceback__)))
            print("🚨 dank_error traceback_end", f"error_id={eid}")
        except Exception:
            pass
    else:
        _print_event("🚨 dank_error_suppressed", {"error_id": eid, "stage": stage, "cmd": _command_path(interaction)})

    return eid


async def _send_clean_failure(interaction: discord.Interaction | None, error_id: str) -> None:
    if interaction is None:
        return

    msg = f"⚠️ Dank Shield hit an error while running this action. Error ID: `{error_id}`"

    try:
        if _response_done(interaction):
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


async def _on_interaction(interaction: discord.Interaction) -> None:
    # Slash commands are covered by dispatch trace. Component events get separate IDs.
    try:
        if getattr(interaction, "type", None) == discord.InteractionType.component:
            _track_button_spam(interaction)
            _component_trace("received", interaction)
        else:
            _trace("received", interaction)
    except Exception:
        pass


async def _tree_on_error(interaction: discord.Interaction, error: BaseException) -> None:
    error_id = _log_error("tree_error", interaction, error)
    await _send_clean_failure(interaction, error_id)


def _patch_tree_call() -> bool:
    tree_cls = app_commands.CommandTree

    if getattr(tree_cls, "_dank_shield_elite_tree_call_wrapped", False):
        return False

    original = getattr(tree_cls, "_call", None)
    if original is None:
        return False

    async def traced_tree_call(self: app_commands.CommandTree, interaction: discord.Interaction) -> Any:
        started = time.monotonic()
        _trace("tree_call_start", interaction)

        async def watchdog() -> None:
            await asyncio.sleep(_slow_ms() / 1000)
            _trace("tree_call_slow_unacked", interaction, age_ms=int((time.monotonic() - started) * 1000))

        task = asyncio.create_task(watchdog())
        try:
            result = await original(self, interaction)
            _trace("tree_call_ok", interaction, age_ms=int((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            _trace("tree_call_exception", interaction, age_ms=int((time.monotonic() - started) * 1000), error=type(exc).__name__)
            _log_error("tree_call_exception", interaction, exc)
            raise
        finally:
            task.cancel()

    setattr(tree_cls, "_dank_shield_elite_original_tree_call", original)
    setattr(tree_cls, "_dank_shield_elite_tree_call_wrapped", True)
    setattr(tree_cls, "_call", traced_tree_call)
    return True


def _patch_command_method(cls: Any, method_name: str, start_stage: str, ok_stage: str, exception_stage: str) -> bool:
    if cls is None:
        return False

    wrapped_marker = f"_dank_shield_elite_{method_name}_wrapped"
    original_marker = f"_dank_shield_elite_original_{method_name}"

    if getattr(cls, wrapped_marker, False):
        return False

    original = getattr(cls, method_name, None)
    if original is None:
        return False

    async def traced_method(self: Any, *args: Any, **kwargs: Any) -> Any:
        interaction = _find_interaction(args, kwargs)
        label = _command_label(self)
        started = time.monotonic()
        _trace(start_stage, interaction, command=label)

        async def watchdog() -> None:
            await asyncio.sleep(_slow_ms() / 1000)
            _trace(f"{start_stage}_slow_unacked", interaction, command=label, age_ms=int((time.monotonic() - started) * 1000))

        task = asyncio.create_task(watchdog()) if interaction is not None else None
        try:
            result = await original(self, *args, **kwargs)
            _trace(ok_stage, interaction, command=label, age_ms=int((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            _trace(exception_stage, interaction, command=label, age_ms=int((time.monotonic() - started) * 1000), error=type(exc).__name__)
            _log_error(exception_stage, interaction, exc, extra={"command": label})
            raise
        finally:
            if task is not None:
                task.cancel()

    setattr(cls, original_marker, original)
    setattr(cls, wrapped_marker, True)
    setattr(cls, method_name, traced_method)
    return True


def _patch_component_dispatch() -> dict[str, bool]:
    patched: dict[str, bool] = {}

    try:
        view_cls = discord.ui.View
        original = getattr(view_cls, "_scheduled_task", None)
        if original is not None and not getattr(view_cls, "_dank_shield_elite_view_scheduled_wrapped", False):

            async def traced_scheduled_task(self: discord.ui.View, item: Any, interaction: discord.Interaction) -> Any:
                started = time.monotonic()
                _component_trace(
                    "view_item_start",
                    interaction,
                    item_type=type(item).__name__,
                    item_custom_id=getattr(item, "custom_id", ""),
                )

                async def watchdog() -> None:
                    await asyncio.sleep(_slow_ms() / 1000)
                    _component_trace(
                        "view_item_slow_unacked",
                        interaction,
                        item_type=type(item).__name__,
                        item_custom_id=getattr(item, "custom_id", ""),
                        age_ms=int((time.monotonic() - started) * 1000),
                    )

                task = asyncio.create_task(watchdog())
                try:
                    result = await original(self, item, interaction)
                    _component_trace(
                        "view_item_ok",
                        interaction,
                        item_type=type(item).__name__,
                        item_custom_id=getattr(item, "custom_id", ""),
                        age_ms=int((time.monotonic() - started) * 1000),
                    )
                    return result
                except Exception as exc:
                    _component_trace(
                        "view_item_exception",
                        interaction,
                        item_type=type(item).__name__,
                        item_custom_id=getattr(item, "custom_id", ""),
                        age_ms=int((time.monotonic() - started) * 1000),
                        error=type(exc).__name__,
                    )
                    _log_error(
                        "component_view_item_exception",
                        interaction,
                        exc,
                        extra={
                            "item_type": type(item).__name__,
                            "item_custom_id": getattr(item, "custom_id", ""),
                        },
                    )
                    raise
                finally:
                    task.cancel()

            setattr(view_cls, "_dank_shield_elite_original_view_scheduled", original)
            setattr(view_cls, "_dank_shield_elite_view_scheduled_wrapped", True)
            setattr(view_cls, "_scheduled_task", traced_scheduled_task)
            patched["view_scheduled_task"] = True
        else:
            patched["view_scheduled_task"] = False
    except Exception as exc:
        patched["view_scheduled_task"] = False
        print(f"⚠️ Dank Shield component dispatch trace patch failed: {type(exc).__name__}: {exc}")

    return patched


def _patch_framework_dispatch() -> dict[str, bool]:
    try:
        from discord.app_commands import commands as command_module
    except Exception:
        command_module = None

    command_cls = getattr(command_module, "Command", None)
    group_cls = getattr(command_module, "Group", None)

    patched = {
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
    patched.update(_patch_component_dispatch())
    return patched


def _get_bot() -> Any:
    # Internal package imports stay unchanged for safety.
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
        print("⚠️ Dank Shield Elite Error Logger waiting: bot unavailable")
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
        print(f"✅ Dank Shield Elite Error Logger active; trace={_trace_enabled()} component_trace={_component_trace_enabled()} patched={patched}")
        return True
    except Exception as exc:
        print(f"⚠️ Dank Shield Elite Error Logger failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
