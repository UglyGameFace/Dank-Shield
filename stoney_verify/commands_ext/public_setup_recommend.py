from __future__ import annotations

"""Plain-language public /dank setup home.

This module patches the hardened setup flow from public_setup_solid.py into a
simple first-run screen. It deliberately avoids developer/product terms.

Public language rules:
- Say Dank Shield, not Dank Shield.
- Use plain labels: Basic server, Help desk, ID check, Voice check,
  ID + voice check, Custom setup.
- No forced forms by default.
- Do not show raw role/channel IDs as public setup instructions.
"""

from typing import Any, Optional

import discord

from ..globals import now_utc
from ..guild_config import get_guild_config
from ..setup_new import (
    build_setup_template_embed,
    build_setup_template_select_options,
    get_setup_template,
    setup_template_payload,
)
from . import public_setup_solid as solid

_PATCHED = False


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = solid._cfg_value(cfg, key)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    return default


def _attr_id(cfg: Any, name: str) -> int:
    try:
        return int(_cfg_value(cfg, name, 0) or 0)
    except Exception:
        return 0


def _saved_choice_text(cfg: Any) -> str:
    label = str(_cfg_value(cfg, "setup_choice_label", "") or "").strip()
    key = str(_cfg_value(cfg, "setup_choice", "") or "").strip()
    if label:
        return f"✅ Saved setup choice: **{label}**"
    if key:
        choice = get_setup_template(key)
        if choice is not None:
            return f"✅ Saved setup choice: **{choice.label}**"
    return "⚠️ No setup choice saved yet. Press **Choose Setup Type** first."


def _plain_lines(lines: list[str], *, empty: str = "✅ Nothing here.", limit: int = 1000) -> str:
    clean = [str(line).strip() for line in lines if str(line).strip()]
    if not clean:
        return empty
    out: list[str] = []
    used = 0
    for line in clean:
        text = line if line.startswith(("•", "✅", "⚠️", "🚫")) else f"• {line}"
        if used + len(text) + 1 > limit:
            out.append(f"…and {len(clean) - len(out)} more")
            break
        out.append(text)
        used += len(text) + 1
    return "\n".join(out)[:limit] or empty


def _has_role(guild: discord.Guild, cfg: Any, key: str) -> bool:
    return guild.get_role(_attr_id(cfg, key)) is not None


def _has_channel(guild: discord.Guild, cfg: Any, *keys: str) -> bool:
    for key in keys:
        if guild.get_channel(_attr_id(cfg, key)) is not None:
            return True
    return False


def _setup_choice_label(cfg: Any) -> str:
    label = str(_cfg_value(cfg, "setup_choice_label", "") or "").strip()
    if label:
        return label
    key = str(_cfg_value(cfg, "setup_choice", "") or "").strip()
    choice = get_setup_template(key)
    return choice.label if choice is not None else "Not chosen yet"


def _needs_id_check(cfg: Any) -> bool:
    style = str(_cfg_value(cfg, "verification_panel_style", "") or "").strip()
    if style == "custom":
        return bool(_cfg_value(cfg, "id_verify_enabled", False) or _cfg_value(cfg, "web_verify_enabled", False) or _cfg_value(cfg, "id_web_verify_enabled", False) or _cfg_value(cfg, "verification_requires_id", False))
    return style in {"id_check", "id_voice_check"} or bool(_cfg_value(cfg, "verification_requires_id", False))


def _needs_voice_check(cfg: Any) -> bool:
    style = str(_cfg_value(cfg, "verification_panel_style", "") or "").strip()
    if style == "custom":
        return bool(_cfg_value(cfg, "voice_verification_enabled", False) or _cfg_value(cfg, "vc_verify_enabled", False) or _cfg_value(cfg, "voice_verify_enabled", False) or _cfg_value(cfg, "verification_allows_voice", False))
    return style in {"voice_check", "id_voice_check"} or bool(_cfg_value(cfg, "verification_allows_voice", False))


async def _build_plain_setup_health_embed(guild: discord.Guild) -> discord.Embed:
    """Plain setup health screen for normal server owners.

    This avoids raw IDs and treats old single-server settings as optional unless
    the chosen setup type actually needs them.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    passing: list[str] = []

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        embed = discord.Embed(
            title="🩺 Setup Check",
            description="🚫 I could not read this server's saved setup yet.",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        embed.add_field(name="How to fix", value=f"Check Supabase/config first. Error: `{type(e).__name__}`", inline=False)
        return embed

    setup_choice = str(_cfg_value(cfg, "setup_choice", "") or "").strip()
    choice_label = _setup_choice_label(cfg)
    needs_id = _needs_id_check(cfg)
    needs_voice = _needs_voice_check(cfg)

    if setup_choice:
        passing.append(f"Setup type chosen: **{choice_label}**")
    else:
        blockers.append("Choose a setup type first. Press **Choose Setup Type**.")

    bot_member = getattr(guild, "me", None)
    bot_perms = getattr(bot_member, "guild_permissions", None)
    if bot_perms and bot_perms.manage_channels and bot_perms.manage_roles and bot_perms.send_messages:
        passing.append("Bot has the basic server permissions it needs.")
    else:
        blockers.append("Give the bot **Manage Channels**, **Manage Roles**, **Send Messages**, **Embed Links**, and **Attach Files**.")

    if _has_role(guild, cfg, "staff_role_id"):
        passing.append("Ticket staff role is chosen.")
    else:
        blockers.append("Choose the role that can answer tickets.")

    if _has_channel(guild, cfg, "ticket_category_id"):
        passing.append("Open ticket folder is chosen.")
    else:
        blockers.append("Choose where new tickets should open.")

    if _has_channel(guild, cfg, "ticket_archive_category_id", "archive_category_id"):
        passing.append("Closed ticket folder is chosen.")
    else:
        warnings.append("Closed ticket folder is not chosen yet. Closed tickets may stay in the open ticket folder.")

    if _has_channel(guild, cfg, "transcripts_channel_id"):
        passing.append("Transcript channel is chosen.")
    else:
        warnings.append("Transcript channel is not chosen yet. Pick one if you want ticket history saved to a channel.")

    if _has_channel(guild, cfg, "ticket_panel_channel_id", "support_channel_id"):
        passing.append("Public ticket panel channel is chosen.")
    else:
        warnings.append("Public ticket panel channel is not chosen yet. Pick where members should click to open tickets.")

    if needs_id or needs_voice:
        if _has_channel(guild, cfg, "verify_channel_id"):
            passing.append("Verify text channel is chosen.")
        else:
            blockers.append("Choose the text channel where members start verification.")

        if _has_role(guild, cfg, "verified_role_id"):
            passing.append("Approved role is chosen.")
        else:
            blockers.append("Choose the role members get after they are approved.")

        if _has_role(guild, cfg, "unverified_role_id"):
            passing.append("New/waiting role is chosen.")
        else:
            warnings.append("New/waiting role is not chosen. This is useful if new members should wait before full access.")

    if needs_voice:
        if _has_channel(guild, cfg, "vc_verify_channel_id"):
            passing.append("Voice check channel is chosen.")
        else:
            blockers.append("Choose the voice channel used for voice checks.")

        if _has_channel(guild, cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id"):
            passing.append("Voice check request channel is chosen.")
        else:
            warnings.append("Voice check request channel is not chosen. Staff may miss voice-check requests.")

    if _has_channel(guild, cfg, "modlog_channel_id", "raidlog_channel_id"):
        passing.append("Log channel is chosen.")
    else:
        warnings.append("Log channel is not chosen. This is optional, but recommended.")

    control_role_keys = ("server_control_role_id", "control_role_id", "perm_role_id")
    has_saved_control_id = any(_attr_id(cfg, key) > 0 for key in control_role_keys)
    has_control_role = any(guild.get_role(_attr_id(cfg, key)) is not None for key in control_role_keys)
    if has_saved_control_id and not has_control_role:
        warnings.append("An old owner/admin role choice is saved but no longer exists. You can pick a new one later or ignore it if your server does not use that feature.")
    elif has_control_role:
        passing.append("Optional owner/admin role is chosen.")

    try:
        category_load = await solid._category_load(guild)
        if category_load.error:
            blockers.append("Ticket menu options could not be checked. Press **Ticket Menu Options** and create recommended options.")
        elif category_load.rows:
            passing.append(f"Ticket menu has {len(category_load.rows)} option(s).")
        else:
            blockers.append("Create at least one ticket menu option.")
    except Exception:
        warnings.append("Ticket menu options could not be checked right now.")

    ready = not blockers
    embed = discord.Embed(
        title="🩺 Setup Check",
        description=(
            "✅ **Ready to test.** Open one test ticket and try the member flow."
            if ready
            else "🚫 **A few things still need fixing before this setup is ready.**"
        ),
        color=discord.Color.green() if ready else discord.Color.red(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Setup Type", value=f"**{choice_label}**", inline=False)
    embed.add_field(name="Needs Fixing", value=_plain_lines(blockers, empty="✅ Nothing required is missing."), inline=False)
    embed.add_field(name="Looks Good", value=_plain_lines(passing, empty="No passing checks yet."), inline=False)
    embed.add_field(name="Optional Later", value=_plain_lines(warnings, empty="✅ No optional warnings."), inline=False)
    embed.add_field(
        name="How to fix this",
        value=(
            "Press **Use My Existing Server** to pick roles/channels you already have.\n"
            "Press **Create Missing Items** if you want Dank Shield to create missing basics.\n"
            "Press **Help / FAQ** if you are unsure what something means."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup • no raw IDs shown")
    return embed


def _build_setup_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Shield Setup Help",
        description="Simple answers for the setup screen. No technical terms needed.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="What should I press first?", value="Press **Choose Setup Type**. Pick the option closest to your server. You can change it later.", inline=False)
    embed.add_field(name="What if I already made my roles/channels?", value="Press **Use My Existing Server**. Then pick your existing roles and channels from Discord menus.", inline=False)
    embed.add_field(name="What if I do not have roles/channels yet?", value="Press **Create Missing Items**. Dank Shield creates missing basics only. It does not delete your server setup.", inline=False)
    embed.add_field(name="What is ID + voice check?", value="That is the upload-link plus voice-check style like your current legacy single-server setup, but without hardcoded server names, role IDs, or channel IDs.", inline=False)
    embed.add_field(name="What if setup says owner/admin role is missing?", value="That is optional. It came from older server-specific setup. Pick a new owner/admin role only if you want that feature.", inline=False)
    embed.add_field(name="Will this force forms on members?", value="No. Ticket flow stays fast by default. Forms are optional only.", inline=False)
    embed.add_field(name="Will this copy legacy single-server settings to other servers?", value="No. Every server saves its own setup. No legacy single-server IDs or branding should be used for other guilds.", inline=False)
    return embed


async def _setup_progress(guild: discord.Guild) -> tuple[str, int, int, str]:
    """Return only the original 4-value progress tuple.

    Compatibility note:
    public_setup_recommend originally returned exactly 4 values from this
    helper. Other setup paths can still call it directly, so do not add cfg or
    other extra return values here.
    """
    done = 0
    total = 0
    lines: list[str] = []
    next_step = "Choose the setup type that best matches this server."

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return (
            f"🚫 Saved setup could not load: `{type(e).__name__}: {str(e)[:180]}`",
            0,
            1,
            "Fix Supabase/config loading first.",
        )

    def check(label: str, ok: bool, fail_hint: str) -> None:
        nonlocal done, total, next_step
        total += 1
        if ok:
            done += 1
            lines.append(f"✅ {label}")
        else:
            lines.append(f"⚠️ {label}: {fail_hint}")
            if next_step == "Choose the setup type that best matches this server.":
                next_step = fail_hint

    choice_saved = bool(str(_cfg_value(cfg, "setup_choice", "") or "").strip())
    check("Setup type", choice_saved, "Press Choose Setup Type.")

    bot_member = getattr(guild, "me", None)
    bot_perms = getattr(bot_member, "guild_permissions", None)
    check(
        "Bot permissions",
        bool(bot_perms and bot_perms.manage_channels and bot_perms.manage_roles and bot_perms.send_messages),
        "Give the bot Manage Channels, Manage Roles, Send Messages, Embed Links, and Attach Files.",
    )

    check("Ticket staff role", guild.get_role(_attr_id(cfg, "staff_role_id")) is not None, "Use My Existing Server → Ticket Basics → Ticket staff role.")
    check("Open ticket folder", guild.get_channel(_attr_id(cfg, "ticket_category_id")) is not None, "Use My Existing Server → Ticket Basics → Open ticket folder.")
    check("Closed ticket folder", guild.get_channel(_attr_id(cfg, "ticket_archive_category_id")) is not None, "Use My Existing Server → Ticket Basics → Closed ticket folder.")
    check("Transcript channel", guild.get_channel(_attr_id(cfg, "transcripts_channel_id")) is not None, "Use My Existing Server → Ticket Basics → Transcript text channel.")

    style = str(_cfg_value(cfg, "verification_panel_style", "") or "").strip()
    needs_verify = style in {"id_check", "voice_check", "id_voice_check", "custom"}
    if needs_verify:
        check("Verify text channel", guild.get_channel(_attr_id(cfg, "verify_channel_id")) is not None, "Use My Existing Server → Verification Channels → Verify text channel.")
        check("Approved role", guild.get_role(_attr_id(cfg, "verified_role_id")) is not None, "Use My Existing Server → Access Roles → Approved role.")
        check("New/waiting role", guild.get_role(_attr_id(cfg, "unverified_role_id")) is not None, "Use My Existing Server → Access Roles → New/waiting role.")

    check("Modlog channel", guild.get_channel(_attr_id(cfg, "modlog_channel_id")) is not None, "Use My Existing Server → Logs + Status → Modlog channel.")

    try:
        category_load = await solid._category_load(guild)
        total += 1
        if category_load.error:
            lines.append(f"🚫 Ticket menu: database error — {category_load.error[:120]}")
            next_step = "Fix the ticket_categories table/Supabase connection, then press Health Check."
        elif category_load.rows:
            done += 1
            lines.append(f"✅ Ticket menu: {len(category_load.rows)} option(s) configured")
        else:
            lines.append("⚠️ Ticket menu: no options yet")
            if next_step == "Choose the setup type that best matches this server.":
                next_step = "Press Ticket Menu Options → Create Recommended Ticket Menu."
    except Exception:
        lines.append("⚠️ Ticket menu: could not check ticket options")

    if done == total:
        next_step = "Post your ticket panel, then open a test ticket."

    return "\n".join(lines)[:1024], done, total, next_step


async def _product_main_setup_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress(guild)
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    saved_choice = _saved_choice_text(cfg) if cfg is not None else "⚠️ Saved setup could not be read."
    ready = bool(total and done >= total)
    started = bool(cfg is not None and str(_cfg_value(cfg, "setup_choice", "") or "").strip())

    issues = [
        line.strip()
        for line in str(progress_text or "").splitlines()
        if line.strip().startswith(("⚠️", "🚫", "❌"))
    ][:3]

    if not started:
        status = "Not started"
        recommended = "Press **Start / Continue Setup** and choose the setup type."
    elif ready:
        status = "Ready to test"
        recommended = "Press **Test / Launch** and test with an alt account."
    else:
        status = "Needs setup work"
        recommended = str(next_step or "Press Start / Continue Setup.")[:350]

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description="One screen. One next step. Everything else is tucked under Manage Setup.",
        color=discord.Color.green() if ready else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Status",
        value=(
            f"**{status}**\n"
            f"{saved_choice}\n"
            f"`{done}/{total}` setup checks complete"
        )[:1024],
        inline=False,
    )
    embed.add_field(name="Recommended Next Step", value=recommended[:1024], inline=False)
    embed.add_field(
        name="Needs Attention",
        value="\n".join(issues)[:900] if issues else "✅ No required setup problem shown here. Run **Setup Check** for the full truth check.",
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup • simple home")
    return embed, ProductSetupHomeView(ready=ready, started=started)

class SetupChoiceSelect(discord.ui.Select):
    def __init__(self, selected_key: Optional[str] = None) -> None:
        super().__init__(
            placeholder="Choose what this server needs…",
            min_values=1,
            max_values=1,
            options=build_setup_template_select_options(),
            row=0,
        )
        if selected_key:
            for option in self.options:
                option.default = option.value == selected_key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        selected = str(self.values[0]) if self.values else ""
        view = self.view
        if isinstance(view, SetupChoiceView):
            view.selected_key = selected
        guild_name = getattr(getattr(interaction, "guild", None), "name", "this server")
        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild_name or "this server"))
        embed.add_field(
            name="Next",
            value="Press **Use This Setup** to save this choice, or pick another option from the menu.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class SetupChoiceView(solid.BackToSetupView):
    def __init__(self, *, selected_key: Optional[str] = None) -> None:
        super().__init__()
        self.selected_key = selected_key
        self.add_item(SetupChoiceSelect(selected_key=selected_key))

    @discord.ui.button(label="Use This Setup", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:publish", row=1)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        selected = str(self.selected_key or "").strip()
        choice = get_setup_template(selected)
        if choice is None:
            return await interaction.response.send_message("Pick a setup type from the menu first.", ephemeral=True)

        await solid._safe_defer_update(interaction)
        payload = setup_template_payload(selected)
        payload.update(
            {
                "setup_choice_selected_at": solid._utc_iso(),
                "setup_choice_selected_by_id": str(interaction.user.id),
                "setup_choice_selected_by_name": str(interaction.user),
            }
        )
        await solid._save_config(interaction, payload)

        if selected == "custom_setup":
            try:
                from . import public_setup_fresh_choice
                return await public_setup_fresh_choice._open_custom_service_picker(
                    interaction,
                    saved_message=(
                        "Saved **Custom setup**. Now turn each service on/off below. "
                        "This is the actual manual editor."
                    ),
                )
            except Exception as e:
                embed = discord.Embed(
                    title="✅ Custom Setup Saved",
                    description=(
                        "Saved **Custom setup**, but the manual service editor did not open.\n\n"
                        f"Error: `{type(e).__name__}: {str(e)[:220]}`\n\n"
                        "Nothing else was changed. Use **Use My Existing Server** while this is repaired."
                    ),
                    color=discord.Color.orange(),
                    timestamp=now_utc(),
                )
                return await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild.name))
        embed.title = "✅ Setup Choice Saved"
        embed.description = (
            f"Saved **{choice.label}** for this server.\n\n"
            "Next, choose your existing roles/channels or create missing basics."
        )
        embed.add_field(
            name="Next step",
            value=(
                "• Press **Use My Existing Server** if your roles/channels already exist.\n"
                "• Press **Create Missing Items** if you want Dank Shield to create missing basics.\n"
                "• Press **Health Check** when you think setup is ready."
            ),
            inline=False,
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

    @discord.ui.button(label="Preview Only", emoji="👀", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:preview", row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        selected = str(self.selected_key or "").strip()
        guild_name = getattr(getattr(interaction, "guild", None), "name", "this server")
        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild_name or "this server"))
        embed.add_field(name="Preview only", value="Nothing has been saved yet.", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)


class SetupHealthHelpView(solid.BackToSetupView):
    @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.primary, custom_id="dank_setup:help_from_health", row=0)
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(embed=_build_setup_help_embed(), view=solid.BackToSetupView())


async def _home_edit(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed, view = await _product_main_setup_payload(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_choose_setup_type(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    guild_name = str(getattr(guild, "name", "this server") or "this server")
    selected_key: Optional[str] = None
    try:
        if guild is not None:
            cfg = await get_guild_config(guild.id, refresh=True)
            selected_key = str(_cfg_value(cfg, "setup_choice", "") or "") or None
    except Exception:
        selected_key = None

    embed = build_setup_template_embed(selected_key=selected_key, guild_name=guild_name)
    embed.add_field(
        name="What happens next",
        value=(
            "Pick a setup type, then press **Use This Setup**.\n"
            "If you pick **Custom setup**, the service switches open immediately."
        ),
        inline=False,
    )
    await interaction.response.edit_message(embed=embed, view=SetupChoiceView(selected_key=selected_key))


async def _open_existing_server(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="🧭 Use Existing Roles / Channels",
        description="Map the roles, channels, and folders your server already has. Names do not matter; Dank Shield saves Discord IDs.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Recommended order",
        value="1. Ticket Basics\n2. Access Roles\n3. Verification Channels\n4. Logs + Status\n5. Behavior Settings",
        inline=False,
    )
    await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())


async def _open_create_missing(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    try:
        from . import public_setup_defaults

        await public_setup_defaults._setup_defaults_callback(interaction)
        if interaction.guild is not None:
            created, skipped, error = await solid._seed_recommended_categories(interaction.guild)
            msg = "✅ Missing default items were handled.\n\nNext: run **Setup Check**, then use **Test / Launch**."
            if error:
                msg += f"\n\n⚠️ Ticket menu options could not be checked: `{error}`"
            elif created:
                msg += f"\n\nCreated ticket menu options: {', '.join(f'`{x}`' for x in created)}"
            elif skipped:
                msg += "\n\nTicket menu options already existed."
            await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        msg = f"❌ Create Missing Items failed: `{type(e).__name__}: {str(e)[:250]}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def _open_ticket_menu(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed, view = await solid._build_category_manager_payload(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_services(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    await solid._safe_defer_update(interaction)
    try:
        from . import public_setup_fresh_choice
        return await public_setup_fresh_choice._open_custom_service_picker(
            interaction,
            saved_message="Service switches opened. Turn each feature ON/OFF here.",
        )
    except Exception as e:
        embed = discord.Embed(
            title="Service Switches Did Not Open",
            description=f"Error: `{type(e).__name__}: {str(e)[:220]}`",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ManageSetupView())


async def _open_health_check(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed = await _build_plain_setup_health_embed(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=SetupHealthHelpView())


async def _open_recovery_center(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    try:
        from . import public_setup_cleanup
        from . import public_setup_recovery

        embed_builder = getattr(public_setup_cleanup, "patched_recovery_embed", None) or getattr(public_setup_recovery, "_build_recovery_embed")
        view_cls = getattr(public_setup_cleanup, "PatchedRecoveryCenterView", None) or getattr(public_setup_recovery, "RecoveryCenterView")
        embed = await embed_builder(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view_cls())
    except Exception as e:
        embed = discord.Embed(
            title="Recovery Center Did Not Open",
            description=f"Error: `{type(e).__name__}: {str(e)[:220]}`",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ManageSetupView())


async def _open_manage_setup(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="⚙️ Manage Setup",
        description="Use this only after the main setup path. Nothing here should be needed for a normal first setup unless you want to edit details.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Tools",
        value=(
            "🧩 **Service Switches** — turn features ON/OFF.\n"
            "🧭 **Saved Roles/Channels** — change mapped roles/channels.\n"
            "🎨 **Server Design** — fonts, separators, category frames, emojis, preview/apply, rollback.\n"
            "🧾 **Ticket Menu Options** — edit ticket choices.\n"
            "🧯 **Recovery / Start Over** — reset/undo setup safely.\n"
            "❓ **Help / FAQ** — plain-language help."
        ),
        inline=False,
    )
    await interaction.response.edit_message(embed=embed, view=ManageSetupView())


def _setup_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return bool(default)


async def _launch_state(guild: discord.Guild) -> dict[str, bool]:
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    return {
        "tickets": _setup_bool(_cfg_value(cfg, "tickets_enabled", True), True),
        "basic_verify": _setup_bool(
            _cfg_value(cfg, "basic_verify_enabled", _cfg_value(cfg, "basic_button_verify_enabled", _cfg_value(cfg, "verification_enabled", False))),
            False,
        ),
        "voice_verify": _setup_bool(
            _cfg_value(cfg, "voice_verification_enabled", _cfg_value(cfg, "vc_verify_enabled", _cfg_value(cfg, "verification_allows_voice", False))),
            False,
        ),
        "id_verify": _setup_bool(
            _cfg_value(cfg, "id_verify_enabled", _cfg_value(cfg, "web_verify_enabled", _cfg_value(cfg, "id_web_verify_enabled", _cfg_value(cfg, "verification_requires_id", False)))),
            False,
        ),
        "logs": _setup_bool(_cfg_value(cfg, "logs_enabled", _cfg_value(cfg, "moderation_enabled", False)), False),
    }


def _launch_state_text(state: dict[str, bool]) -> str:
    return (
        f"🎫 Tickets: **{'ON ✅' if state.get('tickets') else 'OFF ⬜'}**\n"
        f"✅ Basic Verify: **{'ON ✅' if state.get('basic_verify') else 'OFF ⬜'}**\n"
        f"🎙️ Voice Verify: **{'ON ✅' if state.get('voice_verify') else 'OFF ⬜'}**\n"
        f"🪪 ID/Web Verify: **{'ON ✅' if state.get('id_verify') else 'OFF ⬜'}**\n"
        f"🧾 Logs: **{'ON ✅' if state.get('logs') else 'OFF ⬜'}**"
    )


async def _open_test_launch(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    state = await _launch_state(guild)

    embed = discord.Embed(
        title="🧪 Test / Launch",
        description=(
            "This is where you post the panels and run the real test. "
            "Use an alt account before real members."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Selected Services", value=_launch_state_text(state), inline=False)

    actions: list[str] = []
    if state.get("tickets"):
        actions.append("1. Press **Post Ticket Panel**.")
    if state.get("basic_verify"):
        actions.append("2. Press **Post Basic Verify Panel**.")
    if state.get("voice_verify"):
        actions.append("3. Join the saved voice verify channel with an alt and request staff verification.")
    if state.get("id_verify"):
        actions.append("4. ID/Web verify is ON. Only use this for allowlisted/private servers.")
    actions.append("5. Join with an alt, click the public panel(s), and confirm roles/logs.")

    embed.add_field(name="Launch Actions", value="\n".join(actions)[:1024], inline=False)
    embed.add_field(
        name="Expected Result",
        value="Ticket panel opens a ticket. Basic Verify grants the approved role. No ID/Voice flow appears unless those switches are ON.",
        inline=False,
    )

    await interaction.response.edit_message(embed=embed, view=LaunchTestView(state))


class ProductSetupHomeView(discord.ui.View):
    def __init__(self, *, ready: bool = False, started: bool = False) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        self.started = bool(started)

    @discord.ui.button(label="Start / Continue Setup", emoji="▶️", style=discord.ButtonStyle.success, custom_id="dank_setup_home:continue", row=0)
    async def continue_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="▶️ Continue Setup",
            description="Work through these in order. Custom setup lives under **Choose Setup Type** and opens service switches there.",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Setup path",
            value="1. Choose Setup Type\n2. Service Switches if using Custom\n3. Use Existing Roles/Channels\n4. Create Missing Items only if needed\n5. Setup Check",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ContinueSetupView())

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_home:health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(label="Test / Launch", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id="dank_setup_home:launch", row=1)
    async def launch(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_test_launch(interaction)

    @discord.ui.button(label="Manage Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_home:manage", row=1)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)


class ContinueSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Setup Type", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_continue:choose", row=0)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_choose_setup_type(interaction)

    @discord.ui.button(label="Service Switches", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="dank_setup_continue:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_services(interaction)

    @discord.ui.button(label="Use Existing Roles/Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_continue:existing", row=1)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server(interaction)

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_continue:create", row=1)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_create_missing(interaction)

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_continue:health", row=2)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_continue:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class ManageSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Service Switches", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_manage:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_services(interaction)

    @discord.ui.button(label="Saved Roles/Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_manage:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server(interaction)

    @discord.ui.button(label="Ticket Menu Options", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="dank_setup_manage:ticket_menu", row=1)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_ticket_menu(interaction)

    @discord.ui.button(label="Server Design", emoji="🎨", style=discord.ButtonStyle.primary, custom_id="dank_setup_manage:design", row=2)
    async def server_design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        from . import public_design_bridge
        await public_design_bridge.open_design_studio_from_setup(interaction)

    @discord.ui.button(label="Recovery / Start Over", emoji="🧯", style=discord.ButtonStyle.danger, custom_id="dank_setup_manage:recovery", row=1)
    async def recovery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_recovery_center(interaction)

    @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_setup_manage:help", row=2)
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(embed=_build_setup_help_embed(), view=ManageSetupView())

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_manage:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class LaunchTestView(discord.ui.View):
    def __init__(self, state: Optional[dict[str, bool]] = None) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})

    @discord.ui.button(label="Post Ticket Panel", emoji="🎫", style=discord.ButtonStyle.success, custom_id="dank_setup_launch:post_ticket_panel", row=0)
    async def post_ticket_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("tickets"):
            return await interaction.response.send_message("🎫 Tickets are OFF in Custom Setup. Turn Tickets ON first.", ephemeral=True)
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback
            return await post_ticket_panel_callback(interaction)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Could not post ticket panel: `{type(e).__name__}: {str(e)[:220]}`", ephemeral=True)

    @discord.ui.button(label="Post Basic Verify Panel", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_launch:post_basic_verify", row=0)
    async def post_basic_verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("basic_verify"):
            return await interaction.response.send_message("✅ Basic Verify is OFF in Custom Setup. Turn Basic Verify ON first.", ephemeral=True)
        try:
            from .public_verify_basic_panel import verify_panel
            return await verify_panel(interaction)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Could not post Basic Verify panel: `{type(e).__name__}: {str(e)[:220]}`", ephemeral=True)

    @discord.ui.button(label="Run Setup Check", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_launch:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(label="View Current Setup", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="dank_setup_launch:current", row=1)
    async def current(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_current_setup_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=LaunchTestView(await _launch_state(guild)))

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_launch:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)

def _patch() -> None:
    global _PATCHED
    solid._build_main_setup_payload = _product_main_setup_payload
    _PATCHED = True


_patch()


def register_public_setup_recommend_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_recommend: plain-language /dank setup choices active")


__all__ = ["register_public_setup_recommend_commands"]
