from __future__ import annotations

"""Optional direct /dank scoreboard command for setup readiness.

The feature-level scoreboard belongs inside /dank setup health for normal public
servers. A direct /dank scoreboard child is useful for dev/admin diagnostics, but
it adds public autocomplete friction, so it is disabled in the normal public and
production command profile unless explicitly enabled.
"""

import os
from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_scoreboard_command {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_scoreboard_command {message}")
    except Exception:
        pass


def _env_true(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _deployment_mode() -> str:
    raw = _env_str("DANK_DEPLOYMENT_MODE", "").lower()
    if raw:
        return raw
    if _env_true("DANK_PRODUCTION_MODE", False):
        return "production"
    if _env_true("DANK_PUBLIC_MODE", False):
        return "public"
    return "development"


def _public_like() -> bool:
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    deployment = _deployment_mode()
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _direct_scoreboard_enabled() -> bool:
    if _env_true("DANK_EXPOSE_SETUP_SCOREBOARD_COMMAND", False):
        return True
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    return profile in {"public-admin", "dev", "full"}


def _scoreboard_value(scores: list[Any]) -> str:
    lines = [getattr(score, "line", str(score)) for score in scores]
    text = "\n".join(str(line) for line in lines if str(line).strip())
    return text[:1024] if text else "No feature checks ran."


def _fixes_value(scores: list[Any]) -> str:
    lines: list[str] = []
    for score in scores:
        status = str(getattr(score, "status", ""))
        if status not in {"blocker", "warning"}:
            continue
        for fix in tuple(getattr(score, "fixes", ()) or ()):  # type: ignore[arg-type]
            line = f"• **{getattr(score, 'name', 'Feature')}:** {fix}"
            if line not in lines:
                lines.append(line)
    return "\n".join(lines[:7])[:1024] if lines else "✅ No feature-level fixes needed."


def _actions_value(scores: list[Any]) -> str:
    lines: list[str] = []
    for score in scores:
        status = str(getattr(score, "status", ""))
        action = str(getattr(score, "action", "") or "").strip()
        if status in {"blocker", "warning"} and action:
            line = f"• **{getattr(score, 'name', 'Feature')}:** {action}"
            if line not in lines:
                lines.append(line)
    return "\n".join(lines[:6])[:1024] if lines else "✅ Test the selected live flows now."


def _readiness_text(scores: list[Any]) -> tuple[str, discord.Color]:
    blockers = [s for s in scores if str(getattr(s, "status", "")) == "blocker"]
    warnings = [s for s in scores if str(getattr(s, "status", "")) == "warning"]
    ready = [s for s in scores if str(getattr(s, "status", "")) == "ready"]
    skipped = [s for s in scores if str(getattr(s, "status", "")) == "skipped"]
    counts = f"Ready: **{len(ready)}** • Warnings: **{len(warnings)}** • Blockers: **{len(blockers)}** • Skipped: **{len(skipped)}**"
    if blockers:
        names = ", ".join(str(getattr(s, "name", "Feature")) for s in blockers[:4])
        return counts + f"\n🚫 Fix blockers first: **{names}**.", discord.Color.red()
    if warnings:
        names = ", ".join(str(getattr(s, "name", "Feature")) for s in warnings[:4])
        return counts + f"\n⚠️ Usable enough to test, but clean warnings next: **{names}**.", discord.Color.orange()
    return counts + "\n✅ All selected services look ready to test.", discord.Color.green()


def _build_embed(guild: discord.Guild, scores: list[Any]) -> discord.Embed:
    readiness, color = _readiness_text(scores)
    embed = discord.Embed(
        title="🧭 Dank Shield Setup Scoreboard",
        description="Feature-level readiness for this server. This respects selected services, so skipped features are not counted as broken.",
        color=color,
    )
    embed.add_field(name="Feature Health", value=_scoreboard_value(scores), inline=False)
    embed.add_field(name="Suggested Actions", value=_actions_value(scores), inline=False)
    embed.add_field(name="Fix Details", value=_fixes_value(scores), inline=False)
    embed.add_field(name="Product Readiness", value=readiness[:1024], inline=False)
    embed.set_footer(text=f"Guild {guild.id} • /dank setup health")
    return embed


async def _build_scores(guild: discord.Guild) -> list[Any]:
    # Look up the module function at runtime so later scoreboard extension guards
    # are visible to diagnostics too. Do not capture the function during command
    # registration.
    from stoney_verify.startup_guards import setup_feature_health_scoreboard as scoreboard

    return list(await scoreboard.build_feature_scoreboard(guild))


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    if _public_like() and not _direct_scoreboard_enabled():
        _PATCHED = True
        _log("direct /dank scoreboard disabled in public profile; use /dank setup health")
        return True

    try:
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, dank_group

        if dank_group.get_command("scoreboard") is not None:
            _PATCHED = True
            return True

        @dank_group.command(name="scoreboard", description="Show a clear setup readiness scoreboard for this server.")
        async def setup_scoreboard(interaction: discord.Interaction) -> None:
            if not await _require_setup_permission(interaction):
                return
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except Exception:
                pass
            guild = interaction.guild
            if guild is None:
                return await interaction.followup.send("❌ This must be used inside a server.", ephemeral=True)
            try:
                scores = await _build_scores(guild)
                embed = _build_embed(guild, scores)
            except Exception as e:
                embed = discord.Embed(
                    title="❌ Setup Scoreboard Failed",
                    description=f"`{type(e).__name__}: {str(e)[:350]}`",
                    color=discord.Color.red(),
                )
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        _PATCHED = True
        _log("registered /dank scoreboard")
        return True
    except Exception as e:
        _warn(f"failed to register scoreboard command: {e!r}")
        return False


apply()

__all__ = ["apply"]
