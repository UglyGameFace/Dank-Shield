from __future__ import annotations

"""Simplify Protection Center invite setup into a guided flow.

This guard only reshapes the older Protection Center invite setup surface. The
central invite policy engine and /dank protection refresh/view behavior now live
in their native modules, so this file must not load the retired invite policy
runtime compatibility guard.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_PC_INIT: Any = None
_ORIGINAL_SCOPE_CALLBACK: Any = None

_KEEP_MAIN = {
    "dank_protection:safe",
    "dank_protection:strict",
    "dank_protection:off",
    "dank_protection:edit_spamguard",
    "dank_protection:invite_scope",
    "dank_protection:add_filter",
    "dank_protection:block_links",
    "dank_protection:refresh",
    "dank_protection:close",
}


def _set_button(child: Any, *, label: str, emoji: str, style: discord.ButtonStyle, row: int) -> None:
    try:
        child.label = label
        child.emoji = emoji
        child.style = style
        child.row = row
    except Exception:
        pass


def _clean_main_view(view: Any) -> None:
    children = []
    for child in list(getattr(view, "children", []) or []):
        cid = str(getattr(child, "custom_id", "") or "")
        if cid not in _KEEP_MAIN:
            continue
        if cid == "dank_protection:safe":
            _set_button(child, label="Safe Defaults", emoji="🟢", style=discord.ButtonStyle.success, row=0)
        elif cid == "dank_protection:strict":
            _set_button(child, label="Strict Mode", emoji="🔒", style=discord.ButtonStyle.primary, row=0)
        elif cid == "dank_protection:off":
            _set_button(child, label="Turn Off", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
        elif cid == "dank_protection:edit_spamguard":
            _set_button(child, label="Spam Guard", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
        elif cid == "dank_protection:invite_scope":
            _set_button(child, label="Invite Shield", emoji="🚫", style=discord.ButtonStyle.primary, row=1)
        elif cid == "dank_protection:add_filter":
            _set_button(child, label="Bad Word Filter", emoji="🧼", style=discord.ButtonStyle.primary, row=2)
        elif cid == "dank_protection:block_links":
            _set_button(
                child,
                label=getattr(child, "label", "Link Shield: OFF"),
                emoji="🔗",
                style=getattr(child, "style", discord.ButtonStyle.secondary),
                row=2,
            )
        elif cid == "dank_protection:refresh":
            _set_button(child, label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)
        elif cid == "dank_protection:close":
            _set_button(child, label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)
        children.append(child)
    try:
        view.clear_items()
        order = {
            cid: idx
            for idx, cid in enumerate(
                [
                    "dank_protection:safe",
                    "dank_protection:strict",
                    "dank_protection:off",
                    "dank_protection:edit_spamguard",
                    "dank_protection:invite_scope",
                    "dank_protection:add_filter",
                    "dank_protection:block_links",
                    "dank_protection:refresh",
                    "dank_protection:close",
                ]
            )
        }
        for child in sorted(children, key=lambda c: order.get(str(getattr(c, "custom_id", "") or ""), 999)):
            view.add_item(child)
    except Exception:
        pass


def _as_ids(policy: Any, value: Any) -> list[str]:
    try:
        return list(policy._parse_ids(value))
    except Exception:
        return []


async def _turn_on_invite_shield(guild: discord.Guild) -> None:
    try:
        from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config

        await upsert_guild_config(
            int(guild.id),
            {
                "automod_enabled": True,
                "automod_block_invites": True,
                "automod_block_links": False,
                "automod_link_policy": "invite_shield",
            },
        )
        invalidate_guild_config(int(guild.id))
        try:
            from stoney_verify import invite_policy_engine

            invite_policy_engine.invalidate_invite_policy(int(guild.id))
        except Exception:
            pass
    except Exception:
        pass


async def _save(guild: discord.Guild, actor: discord.abc.User, patch: dict[str, Any]) -> dict[str, Any]:
    from stoney_verify import spam_guard

    await _turn_on_invite_shield(guild)
    settings, _persisted = await spam_guard.save_spam_settings(
        int(guild.id),
        patch,
        updated_by=actor if isinstance(actor, discord.Member) else None,
    )
    return dict(settings or {})


def _status(policy: Any, settings: dict[str, Any]) -> str:
    try:
        all_bots = bool(policy._safe_bool(settings.get("invite_hard_block_target_all_bots"), False))
    except Exception:
        all_bots = False
    bot_ids = _as_ids(policy, settings.get("invite_hard_block_target_bot_ids"))
    channel_ids = _as_ids(policy, settings.get("invite_hard_block_target_channel_ids"))
    bots = "Every bot" if all_bots else (f"{len(bot_ids)} selected ID(s)" if bot_ids else "No selected bots yet")
    channels = f"{len(channel_ids)} selected channel(s)" if channel_ids else "All channels"
    return f"**Bot posts watched:** {bots}\n**Channels watched:** {channels}\n**Normal links:** still allowed"


def _embed(policy: Any, guild: discord.Guild, settings: dict[str, Any]) -> discord.Embed:
    e = discord.Embed(
        title="🚫 Invite Shield Setup",
        description=(
            "Use this for OneBump and other server-listing bots.\n\n"
            "Press **Fix This Channel** for the normal setup: bot can post here, but server invite links are handled by Dank Shield."
        ),
        color=discord.Color.blurple(),
    )
    e.add_field(name="Current setup", value=_status(policy, settings), inline=False)
    e.add_field(
        name="What to press",
        value=(
            "**Fix This Channel** — best choice for your current bump-bot channel.\n"
            "**Watch Every Bot** — watch bot posts server-wide.\n"
            "**All Channels** — watch every message channel.\n"
            "**Paste IDs** — advanced manual setup."
        ),
        inline=False,
    )
    return e


class InviteShieldIdsModal(discord.ui.Modal, title="Paste Invite Shield IDs"):
    def __init__(self, *, guild: discord.Guild, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        self.guild = guild
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_ids = discord.ui.TextInput(
            label="Bot/user IDs",
            placeholder="Comma, space, or new line separated. Blank clears selected IDs.",
            default=policy._ids_text(settings.get("invite_hard_block_target_bot_ids")),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1200,
        )
        self.channel_ids = discord.ui.TextInput(
            label="Channel IDs",
            placeholder="Blank means all message channels.",
            default=policy._ids_text(settings.get("invite_hard_block_target_channel_ids")),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1200,
        )
        self.add_item(self.bot_ids)
        self.add_item(self.channel_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        settings = await _save(
            self.guild,
            interaction.user,
            {
                "invite_hard_block_target_bot_ids": policy._parse_ids(self.bot_ids.value),
                "invite_hard_block_target_channel_ids": policy._parse_ids(self.channel_ids.value),
            },
        )
        await interaction.response.edit_message(
            embed=_embed(policy, self.guild, settings),
            view=InviteShieldView(guild=self.guild, channel_id=self.channel_id, message_id=int(self.message_id), settings=settings),
        )


class InviteShieldView(discord.ui.View):
    def __init__(self, *, guild: discord.Guild, channel_id: int, message_id: int, settings: dict[str, Any], **_: Any) -> None:
        super().__init__(timeout=900)
        self.guild = guild
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.settings = dict(settings or {})

    async def _redraw(self, interaction: discord.Interaction, settings: dict[str, Any]) -> None:
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        await interaction.response.edit_message(
            embed=_embed(policy, self.guild, settings),
            view=InviteShieldView(guild=self.guild, channel_id=self.channel_id, message_id=int(self.message_id), settings=settings),
        )

    @discord.ui.button(label="Fix This Channel", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def fix_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        settings = await _save(
            self.guild,
            interaction.user,
            {
                "invite_hard_block_target_all_bots": True,
                "invite_hard_block_target_channel_ids": [str(self.channel_id)],
                "invite_protected_poster_rule_enabled": True,
            },
        )
        await self._redraw(interaction, settings)

    @discord.ui.button(label="Watch Every Bot", emoji="🤖", style=discord.ButtonStyle.primary, row=1)
    async def all_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        settings = await _save(
            self.guild,
            interaction.user,
            {
                "invite_hard_block_target_all_bots": True,
                "invite_protected_poster_rule_enabled": True,
            },
        )
        await self._redraw(interaction, settings)

    @discord.ui.button(label="All Channels", emoji="🌐", style=discord.ButtonStyle.primary, row=1)
    async def all_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        settings = await _save(self.guild, interaction.user, {"invite_hard_block_target_channel_ids": []})
        await self._redraw(interaction, settings)

    @discord.ui.button(label="Only This Channel", emoji="#️⃣", style=discord.ButtonStyle.secondary, row=2)
    async def this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        settings = await _save(self.guild, interaction.user, {"invite_hard_block_target_channel_ids": [str(self.channel_id)]})
        await self._redraw(interaction, settings)

    @discord.ui.button(label="Paste IDs", emoji="✍️", style=discord.ButtonStyle.secondary, row=2)
    async def paste_ids(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            InviteShieldIdsModal(guild=self.guild, channel_id=self.channel_id, message_id=int(self.message_id), settings=self.settings)
        )

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=3)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Invite Shield setup closed.", embed=None, view=None)


def _author_id_from(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
    try:
        return int(kwargs.get("author_id") if kwargs.get("author_id") is not None else args[0])
    except Exception:
        return 0


def _chain_extra_guards() -> None:
    for name in (
        "protection_center_embed_refresh_guard",
        "protection_center_filter_list_guard",
        "vc_verified_health_check_guard",
        "modlog_center_tracking_guard",
        "live_guild_name_footer_guard",
        "protection_invite_toggle_cleanup_guard",
    ):
        try:
            module = __import__(f"stoney_verify.startup_guards.{name}", fromlist=["apply"])
            apply_fn = getattr(module, "apply", None)
            if callable(apply_fn):
                apply_fn()
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_PC_INIT, _ORIGINAL_SCOPE_CALLBACK
    if _PATCHED:
        _chain_extra_guards()
        return True
    try:
        from stoney_verify.commands_ext import public_protection_center as center
        from stoney_verify.startup_guards import protection_center_invite_controls_guard as invite_controls
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        try:
            invite_controls.apply()
            policy.apply()
        except Exception:
            pass

        _ORIGINAL_PC_INIT = center.ProtectionCenterView.__init__
        _ORIGINAL_SCOPE_CALLBACK = invite_controls.ProtectionInviteScopeButton.callback

        def patched_pc_init(self: Any, *args: Any, **kwargs: Any) -> None:
            try:
                _ORIGINAL_PC_INIT(self, *args, **kwargs)
            except TypeError as exc:
                text = str(exc)
                if "cfg" not in text and "spam" not in text and "unexpected keyword" not in text:
                    raise
                _ORIGINAL_PC_INIT(self, author_id=_author_id_from(args, kwargs))
            _clean_main_view(self)

        async def patched_scope_callback(self: Any, interaction: discord.Interaction) -> None:
            center_mod, spam_guard, _policy = invite_controls._patch_helpers()
            if not await center_mod._require_setup_permission(interaction):
                return
            guild = interaction.guild
            message = interaction.message
            if guild is None or message is None:
                return await center_mod._send_ephemeral(interaction, "❌ Invalid Protection Center context.")
            settings = await spam_guard.get_spam_settings(int(guild.id))
            channel_id = int(getattr(message.channel, "id", 0) or 0)
            await interaction.response.send_message(
                embed=_embed(policy, guild, settings),
                view=InviteShieldView(guild=guild, channel_id=channel_id, message_id=int(message.id), settings=settings),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        center.ProtectionCenterView.__init__ = patched_pc_init
        invite_controls.ProtectionInviteScopeButton.callback = patched_scope_callback
        invite_controls.InviteScopeEditorView = InviteShieldView
        _PATCHED = True
        _chain_extra_guards()
        print("✅ protection_center_invite_simple_flow_guard active; Invite Shield uses a guided setup")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_invite_simple_flow_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
