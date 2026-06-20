from __future__ import annotations

"""Final invite/protection compatibility guard.

Runs after older Protection Center guards so the final view accepts the native
``cfg`` and ``spam`` keyword arguments.  It also routes invite scans through the
central invite policy engine.
"""

from typing import Any

import discord

from stoney_verify import invite_policy_engine as policy

_PATCHED = False
_ORIGINAL_INIT: Any = None
_ORIGINAL_EMBED: Any = None
_ORIGINAL_REFRESH: Any = None


def _log(message: str) -> None:
    try:
        print(f"✅ invite_policy_runtime_compat_guard {message}")
    except Exception:
        pass


def _replace_truth_field(embed: discord.Embed, *, cfg: Any, spam: dict[str, Any]) -> discord.Embed:
    value = policy.policy_snapshot_text(cfg, spam or {})
    try:
        existing = list(embed.fields)
        embed.clear_fields()
        inserted = False
        skip_names = {
            "invite enforcement truth",
            "invite & link controls",
            "invite protection",
            "invite protection snapshot",
            "link shield — bad server spam",
        }
        for field in existing:
            name = str(getattr(field, "name", "") or "")
            if name.strip().lower() in skip_names:
                if not inserted:
                    embed.add_field(name="Invite Enforcement Truth", value=value, inline=False)
                    inserted = True
                continue
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if not inserted:
            embed.add_field(name="Invite Enforcement Truth", value=value, inline=False)
    except Exception:
        try:
            embed.add_field(name="Invite Enforcement Truth", value=value, inline=False)
        except Exception:
            pass
    return embed


def _coerce_author_id(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
    raw = kwargs.get("author_id")
    if raw is None and args:
        raw = args[0]
    try:
        return int(raw)
    except Exception:
        return 0


def _patch_view_and_embed(center: Any) -> None:
    global _ORIGINAL_INIT, _ORIGINAL_EMBED, _ORIGINAL_REFRESH

    if _ORIGINAL_INIT is None:
        _ORIGINAL_INIT = center.ProtectionCenterView.__init__
    if _ORIGINAL_EMBED is None:
        _ORIGINAL_EMBED = center._protection_embed
    if _ORIGINAL_REFRESH is None:
        _ORIGINAL_REFRESH = center._refresh_panel

    def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
        embed = _ORIGINAL_EMBED(guild, cfg, spam, spam_source)
        return _replace_truth_field(embed, cfg=cfg, spam=dict(spam or {}))

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        cfg = kwargs.get("cfg")
        spam = kwargs.get("spam")
        try:
            _ORIGINAL_INIT(self, *args, **kwargs)
        except TypeError as exc:
            text = str(exc)
            if "cfg" not in text and "spam" not in text and "unexpected keyword" not in text:
                raise
            _ORIGINAL_INIT(self, author_id=_coerce_author_id(args, kwargs))
            try:
                if cfg is not None and hasattr(center, "_decorate_quick_mode_buttons"):
                    center._decorate_quick_mode_buttons(self, cfg, dict(spam or {}))
            except Exception:
                pass

    async def patched_refresh(interaction: discord.Interaction, *, content: str | None = None) -> None:
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        cfg = await center.get_guild_config(int(guild.id), refresh=True)
        spam, source = await center._load_spam_settings(int(guild.id))
        embed = center._protection_embed(guild, cfg, spam, source)
        view = center.ProtectionCenterView(author_id=int(interaction.user.id), cfg=cfg, spam=spam)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=content, embed=embed, view=view)
            else:
                await interaction.response.edit_message(content=content, embed=embed, view=view)
            return
        except Exception:
            pass
        try:
            await interaction.followup.send(
                content=content or None,
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    center._protection_embed = patched_embed
    center._refresh_panel = patched_refresh
    center.ProtectionCenterView.__init__ = patched_init


def _patch_scanners() -> None:
    try:
        from stoney_verify.startup_guards import protection_invite_toggle_cleanup_guard as cleanup

        async def patched_clean_existing_invites(channel: Any, *, limit: int = 100, repost_mixed: bool = False) -> dict[str, Any]:
            return await policy.scan_channel_invites(
                channel,
                limit=limit,
                repost_mixed=repost_mixed,
                source="protection-center-scan",
            )

        cleanup._clean_existing_invites = patched_clean_existing_invites
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_protection_center as center

        _patch_view_and_embed(center)
        _patch_scanners()

        _PATCHED = True
        _log("active; Protection Center view kwargs and invite scans use central policy")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ invite_policy_runtime_compat_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
