from __future__ import annotations

"""Show optional verification idle-kick status in /dank setup health.

The feature is optional and off by default, so it should not block setup. This
adds visibility so server owners can quickly see whether their server enabled
"remove pending users who never start verification" and what timer is saved.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"⏳ setup_idle_kick_scoreboard_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_idle_kick_scoreboard_guard {message}")
    except Exception:
        pass


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return default


def _safe_int(value: Any, default: int = 60) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


async def _idle_kick_score(guild: discord.Guild, scoreboard: Any, cfg: Any, verification_selected: bool) -> Any:
    FeatureHealth = getattr(scoreboard, "FeatureHealth")
    cfg_get = getattr(scoreboard, "_cfg_get")
    role = getattr(scoreboard, "_role")
    bot_member = getattr(scoreboard, "_bot_member")

    enabled = _safe_bool(cfg_get(cfg, "verification_idle_kick_enabled", False), False)
    minutes = max(5, min(10080, _safe_int(cfg_get(cfg, "verification_idle_kick_minutes", 60), 60)))

    if not enabled:
        return FeatureHealth(
            "No-Start Auto-Remove",
            "⏳",
            "skipped",
            "Off by default. New unverified members are not auto-removed for failing to start verification.",
            (),
            "Optional: /dank setup → Verification: Channels → No-Start Auto-Remove.",
        )

    unverified = role(guild, cfg_get(cfg, "unverified_role_id", 0))
    me = bot_member(guild)
    warnings: list[str] = []
    blockers: list[str] = []

    if not verification_selected:
        warnings.append("Verification service is not selected, but no-start auto-remove is enabled.")
    if unverified is None:
        blockers.append("Select the Unverified/new-waiting role so only pending users can be targeted.")
    if me is None:
        warnings.append("Could not inspect bot member permissions yet.")
    else:
        try:
            if not bool(getattr(me.guild_permissions, "kick_members", False)):
                warnings.append("Bot needs Kick Members permission before it can remove idle pending users.")
        except Exception:
            warnings.append("Could not inspect Kick Members permission.")

    if blockers:
        return FeatureHealth(
            "No-Start Auto-Remove",
            "⏳",
            "blocker",
            f"Enabled at {minutes} minute(s), but targeting is incomplete.",
            tuple(blockers[:3]),
            "Open /dank setup → Roles: Member Access and select Unverified/new-waiting role.",
        )
    if warnings:
        return FeatureHealth(
            "No-Start Auto-Remove",
            "⏳",
            "warning",
            f"Enabled at {minutes} minute(s), but needs permission/service review.",
            tuple(warnings[:3]),
            "Review verification selection and bot permissions.",
        )
    return FeatureHealth(
        "No-Start Auto-Remove",
        "⏳",
        "ready",
        f"Enabled for this server: pending users who never start verification are removed after {minutes} minute(s).",
    )


def _insert_after(scores: list[Any], after_name: str, item: Any) -> list[Any]:
    out: list[Any] = []
    inserted = False
    for score in scores:
        out.append(score)
        if not inserted and str(getattr(score, "name", "")) == after_name:
            out.append(item)
            inserted = True
    if not inserted:
        out.append(item)
    return out


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_feature_health_scoreboard as scoreboard

        original = getattr(scoreboard, "build_feature_scoreboard", None)
        if not callable(original):
            _warn("setup_feature_health_scoreboard.build_feature_scoreboard missing")
            return False
        if getattr(original, "_idle_kick_scoreboard_wrapped", False):
            _PATCHED = True
            return True

        async def wrapped_build_feature_scoreboard(guild: discord.Guild) -> list[Any]:
            scores = list(await original(guild))
            try:
                cfg, state = await scoreboard.asyncio.gather(scoreboard._load_config(guild), scoreboard._load_service_state(guild))
                verification_selected = bool(getattr(state, "verification", False))
                idle_score = await _idle_kick_score(guild, scoreboard, cfg, verification_selected)
                return _insert_after(scores, "Verification", idle_score)
            except Exception as e:
                FeatureHealth = getattr(scoreboard, "FeatureHealth")
                _warn(f"idle kick score failed: {e!r}")
                return _insert_after(
                    scores,
                    "Verification",
                    FeatureHealth("No-Start Auto-Remove", "⏳", "warning", "Could not inspect optional idle-kick setting."),
                )

        setattr(wrapped_build_feature_scoreboard, "_idle_kick_scoreboard_wrapped", True)
        setattr(scoreboard, "build_feature_scoreboard", wrapped_build_feature_scoreboard)
        _PATCHED = True
        _log("active; optional idle-kick status appears in setup health")
        return True
    except Exception as e:
        _warn(f"failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
