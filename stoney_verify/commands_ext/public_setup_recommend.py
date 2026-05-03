from __future__ import annotations

"""Product polish for the public /stoney setup experience.

This module patches the hardened setup flow into a plain-English first-run
wizard. The goal is simple: a normal server owner should understand what to do
without learning the internal command map first.
"""

from typing import Any, Optional

import discord

from ..globals import now_utc
from ..guild_config import get_guild_config
from . import public_setup_solid as solid

try:
    from ..startup_guards.setup_category_modal_compat import install_setup_category_modal_compat

    install_setup_category_modal_compat()
except Exception:
    pass

_PATCHED = False

_RECOMMENDED_PURPOSES = {
    "support": "General help requests. This is the default fallback when nothing else fits.",
    "verification": "Users stuck verifying, missing roles, or needing VC/text verification help.",
    "appeal": "Ban, timeout, mute, blacklist, or denied-access appeals.",
    "report": "Reports about users, scams, harassment, spam, or rule breaking.",
    "question": "Simple questions that staff can answer in a ticket.",
    "bug": "Broken buttons, setup issues, missing panels, or workflow bugs.",
    "custom": "Catch-all option so users are never stuck picking the wrong thing.",
}


def _short(value: Any, limit: int = 88) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _installed_slugs(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("slug") or "").strip().lower() for row in rows if str(row.get("slug") or "").strip()}


def _recommended_line(item: dict[str, Any], installed: set[str]) -> str:
    slug = str(item.get("slug") or "").strip().lower()
    marker = "✅" if slug in installed else "➕"
    default = " ⭐ default" if bool(item.get("is_default")) else ""
    purpose = _RECOMMENDED_PURPOSES.get(slug) or item.get("description") or "Recommended ticket menu option."
    return f"{marker} **{item.get('name', slug)}** — `{slug}`{default}\n  ↳ {_short(purpose, 96)}"


def _recommended_text(rows: list[dict[str, Any]]) -> str:
    installed = _installed_slugs(rows)
    return "\n".join(_recommended_line(item, installed) for item in solid.RECOMMENDED_CATEGORIES)[:1024]


def _missing_text(rows: list[dict[str, Any]]) -> str:
    installed = _installed_slugs(rows)
    missing = [item for item in solid.RECOMMENDED_CATEGORIES if str(item.get("slug") or "").lower() not in installed]
    if not missing:
        return "✅ All recommended ticket menu options already exist. You can still rename, reorder, edit, or delete them."
    return "\n".join(_recommended_line(item, installed) for item in missing)[:1024]


def _mention_or_missing(obj: Any, label: str) -> str:
    if obj is None:
        return f"⚠️ {label}: not set"
    mention = getattr(obj, "mention", None)
    return f"✅ {label}: {mention or getattr(obj, 'name', obj)}"


def _attr_id(cfg: Any, name: str) -> int:
    try:
        return int(getattr(cfg, name, 0) or 0)
    except Exception:
        return 0


async def _setup_progress(guild: discord.Guild) -> tuple[str, int, int, str]:
    """Return progress text, done count, total count, and recommended next step."""
    done = 0
    total = 0
    lines: list[str] = []
    next_step = "Run Health Check after making changes."

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return f"🚫 Saved config could not load: `{type(e).__name__}: {str(e)[:180]}`", 0, 1, "Fix Supabase/config loading first."

    def check(label: str, ok: bool, fail_hint: str) -> None:
        nonlocal done, total, next_step
        total += 1
        if ok:
            done += 1
            lines.append(f"✅ {label}")
        else:
            lines.append(f"⚠️ {label}: {fail_hint}")
            if next_step == "Run Health Check after making changes.":
                next_step = fail_hint

    bot_member = getattr(guild, "me", None)
    bot_perms = getattr(bot_member, "guild_permissions", None)
    check("Bot permissions", bool(bot_perms and bot_perms.manage_channels and bot_perms.manage_roles and bot_perms.send_messages), "Give the bot Manage Channels, Manage Roles, Send Messages, Embed Links, Attach Files.")

    check("Ticket staff role", guild.get_role(_attr_id(cfg, "staff_role_id")) is not None, "Choose Existing Items → Ticket Basics → Ticket staff role.")
    check("Open ticket Discord category", guild.get_channel(_attr_id(cfg, "ticket_category_id")) is not None, "Choose Existing Items → Ticket Basics → Open ticket Discord category.")
    check("Archive ticket Discord category", guild.get_channel(_attr_id(cfg, "ticket_archive_category_id")) is not None, "Choose Existing Items → Ticket Basics → Archive/closed Discord category.")
    check("Transcript channel", guild.get_channel(_attr_id(cfg, "transcripts_channel_id")) is not None, "Choose Existing Items → Ticket Basics → Transcript text channel.")
    check("Verify text channel", guild.get_channel(_attr_id(cfg, "verify_channel_id")) is not None, "Choose Existing Items → Verification Channels → Verify text channel.")
    check("Verified role", guild.get_role(_attr_id(cfg, "verified_role_id")) is not None, "Choose Existing Items → Verification Roles → Verified role.")
    check("Unverified role", guild.get_role(_attr_id(cfg, "unverified_role_id")) is not None, "Choose Existing Items → Verification Roles → Unverified role.")
    check("Modlog channel", guild.get_channel(_attr_id(cfg, "modlog_channel_id")) is not None, "Choose Existing Items → Logs + Status → Modlog channel.")

    category_load = await solid._category_load(guild)
    total += 1
    if category_load.error:
        lines.append(f"🚫 Ticket menu options: database error — {category_load.error[:120]}")
        next_step = "Fix the ticket_categories table/Supabase connection, then press Refresh."
    elif category_load.rows:
        done += 1
        lines.append(f"✅ Ticket menu options: {len(category_load.rows)} configured")
    else:
        lines.append("⚠️ Ticket menu options: none created yet")
        if next_step == "Run Health Check after making changes.":
            next_step = "Open Advanced Setup → Ticket Menu Options → Create Recommended Ticket Menu."

    if done == total:
        next_step = "Post your ticket panel with `/ticket-panel post`, then open a test ticket."

    return "\n".join(lines)[:1024], done, total, next_step


async def _product_main_setup_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress(guild)
    embed = discord.Embed(
        title="🚀 Stoney Setup",
        description=(
            "Pick **one path**. You do not need to know any setup commands.\n\n"
            "🟢 **Fresh Server** — Stoney creates the missing recommended roles/channels/categories.\n"
            "🔵 **Existing Server** — use dropdowns to map your current roles/channels.\n"
            "⚙️ **Advanced Setup** — fine-tune ticket menu options, logs, status, and checks."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name=f"Setup Progress: {done}/{total} complete", value=progress_text or "No setup checks ran.", inline=False)
    embed.add_field(name="Recommended Next Step", value=next_step[:1024], inline=False)
    embed.set_footer(text=f"Guild {guild.id} • start here every time: /stoney setup")
    return embed, ProductSetupHomeView()


class ProductSetupHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Fresh Server", emoji="🟢", style=discord.ButtonStyle.success, custom_id="stoney_product:fresh", row=0)
    async def fresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🟢 Fresh Server Setup",
            description=(
                "Use this when the server does **not** already have a Stoney layout.\n\n"
                "Stoney will create only what is missing: recommended roles, ticket folders, log/status channels, verification channels, and the recommended ticket menu options."
            ),
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="What happens next",
            value=(
                "1. Press **Create Missing Defaults Now**.\n"
                "2. Run **Health Check**.\n"
                "3. Post the ticket panel with `/ticket-panel post`.\n"
                "4. Open a test ticket."
            ),
            inline=False,
        )
        embed.add_field(name="Safety", value="It will not delete old channels, old tickets, or existing roles.", inline=False)
        await interaction.response.edit_message(embed=embed, view=FreshServerConfirmView())

    @discord.ui.button(label="Existing Server", emoji="🔵", style=discord.ButtonStyle.primary, custom_id="stoney_product:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🔵 Existing Server Setup",
            description=(
                "Use this when you already have channels/roles and want Stoney to use them.\n\n"
                "Pick each section below. The bot validates permissions before saving."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Recommended order",
            value=(
                "1. **Ticket Basics**\n"
                "2. **Verification Roles**\n"
                "3. **Verification Channels**\n"
                "4. **Logs + Status**\n"
                "5. Back → Health Check"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())

    @discord.ui.button(label="Advanced Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="stoney_product:advanced", row=1)
    async def advanced(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="⚙️ Advanced Setup",
            description="Use these only when you want to fine-tune the default setup.",
            color=discord.Color.gold(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Plain-English map",
            value=(
                "🎫 **Ticket Basics** = actual Discord category folders/channels/roles.\n"
                "🧾 **Ticket Menu Options** = current/internal ticket routing menu. If your public panel still uses the basic form, users see the form first, not this list.\n"
                "📌 **Status Channel** = where the bot posts heartbeat/setup status."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=AdvancedSetupView())

    @discord.ui.button(label="Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_product:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_health_embed(guild)
        embed.add_field(name="What happens next", value="Fix the first blocker listed, then press Back to Setup and run Health Check again.", inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=solid.BackToSetupView())


class FreshServerConfirmView(solid.BackToSetupView):
    @discord.ui.button(label="Create Missing Defaults Now", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_product:create_defaults", row=0)
    async def create_defaults(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                created, skipped, error = await solid._seed_recommended_categories(interaction.guild)
                msg = "✅ Fresh-server defaults were handled.\n\n**Next:** run `/stoney setup`, press **Health Check**, then post the ticket panel with `/ticket-panel post`."
                if error:
                    msg += f"\n\n⚠️ Ticket menu options could not be checked: `{error}`"
                elif created:
                    msg += f"\n\nCreated ticket menu options: {', '.join(f'`{x}`' for x in created)}"
                elif skipped:
                    msg += "\n\nTicket menu options already existed."
                await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            msg = f"❌ Fresh-server setup failed: `{type(e).__name__}: {str(e)[:250]}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


class AdvancedSetupView(solid.BackToSetupView):
    @discord.ui.button(label="Ticket Basics", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="stoney_product:ticket_basics", row=0)
    async def ticket_basics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎫 Ticket Basics",
            description="These are actual Discord roles/channels/categories the ticket system uses.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="You will pick",
            value="Open ticket Discord category, archive Discord category, ticket staff role, and transcript channel.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=solid.TicketBasicsPickerView())

    @discord.ui.button(label="Ticket Menu Options", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="stoney_product:ticket_menu", row=0)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await _better_category_manager_payload(guild, title="🧾 Ticket Menu Options")
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Use This Channel for Status", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="stoney_product:status", row=1)
    async def status_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Use this in the text channel you want as the bot status channel.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        await solid._save_config(interaction, {"status_channel_id": solid._snowflake(interaction.channel), "bot_status_channel_id": solid._snowflake(interaction.channel)})
        embed, view = await _product_main_setup_payload(interaction.guild)  # type: ignore[arg-type]
        embed.add_field(name="Saved", value=f"Status channel set to {interaction.channel.mention}.", inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_product:advanced_health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_health_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=solid.BackToSetupView())


async def _better_category_manager_payload(guild: discord.Guild, *, title: str = "🧾 Ticket Menu Options"):
    load = await solid._category_load(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "These are ticket routing/menu records. They control how Stoney classifies or presents support choices when that menu flow is enabled.\n"
            "They are **not** Discord channel categories/folders. Use **Ticket Basics** for the real Discord categories."
        ),
        color=discord.Color.blurple() if not load.error else discord.Color.red(),
        timestamp=now_utc(),
    )

    if load.error:
        embed.add_field(name="Database Problem", value=load.error[:1024], inline=False)
        embed.add_field(name="Fix", value="Make sure the `ticket_categories` table exists and Supabase is reachable, then press Refresh.", inline=False)
        return embed, ProductCategoryManagerView(rows=load.rows, db_error=load.error)

    embed.add_field(name="Current Ticket Routing/Menu Records", value=solid._category_list_text(load.rows), inline=False)
    embed.add_field(name="Stoney's Recommended Records", value=_recommended_text(load.rows), inline=False)
    embed.add_field(name="Missing Recommended Records", value=_missing_text(load.rows), inline=False)
    embed.add_field(
        name="What the green button does",
        value=(
            "Creates only the missing recommended ticket routing/menu records. It does **not** create Discord channels, "
            "delete tickets, delete categories, or lock you into Stoney's names. You can edit everything after."
        ),
        inline=False,
    )
    embed.add_field(name="Safety", value=solid._category_governance_text(load.rows), inline=False)
    embed.set_footer(text="Use Ticket Basics for actual Discord open/archive categories.")
    return embed, ProductCategoryManagerView(rows=load.rows, db_error=load.error)


class ProductCategoryManagerView(solid.BackToSetupView):
    def __init__(self, *, rows: list[dict[str, Any]], db_error: str = "") -> None:
        super().__init__()
        self.rows = rows
        self.db_error = db_error
        if db_error:
            for child in self.children:
                if getattr(child, "custom_id", "") not in {"stoney_product:cat_refresh", "stoney_solid:back"}:
                    child.disabled = True
        elif not rows:
            self.edit_option.disabled = True
            self.set_default.disabled = True
            self.delete_option.disabled = True

    @discord.ui.button(label="Create Recommended Ticket Records", emoji="🧱", style=discord.ButtonStyle.success, custom_id="stoney_product:cat_seed", row=0)
    async def seed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        created, skipped, error = await solid._seed_recommended_categories(guild)
        embed, view = await _better_category_manager_payload(guild, title="🧱 Recommended Ticket Records Applied")
        if error:
            embed.add_field(name="Result", value=f"🚫 {error}", inline=False)
            embed.color = discord.Color.red()
        elif created:
            embed.add_field(name="Created", value=", ".join(f"`{x}`" for x in created), inline=False)
            embed.add_field(name="Next Step", value="Back to Setup → Health Check. Then post the ticket panel with `/ticket-panel post`.", inline=False)
        else:
            embed.add_field(name="Result", value="✅ Nothing new needed. Recommended records already exist.", inline=False)
        if skipped:
            embed.add_field(name="Already existed", value=", ".join(f"`{x}`" for x in skipped[:20]), inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Add Custom Routing Record", emoji="➕", style=discord.ButtonStyle.primary, custom_id="stoney_product:cat_add", row=0)
    async def add_option(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        modal_cls = getattr(solid, "AddTicketCategoryModal", None)
        if modal_cls is None:
            try:
                from ..startup_guards.setup_category_modal_compat import install_setup_category_modal_compat

                install_setup_category_modal_compat()
                modal_cls = getattr(solid, "AddTicketCategoryModal", None)
            except Exception:
                modal_cls = None
        if modal_cls is None:
            return await interaction.response.send_message(
                "❌ The ticket routing record editor is unavailable. Redeploy/restart the latest build and try again.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await interaction.response.send_modal(modal_cls(existing_count=len(self.rows)))

    @discord.ui.button(label="Edit Record", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_product:cat_edit", row=1)
    async def edit_option(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="edit")

    @discord.ui.button(label="Set Default Record", emoji="⭐", style=discord.ButtonStyle.secondary, custom_id="stoney_product:cat_default", row=1)
    async def set_default(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="default")

    @discord.ui.button(label="Delete Record", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_product:cat_delete", row=2)
    async def delete_option(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="delete")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_product:cat_refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await _better_category_manager_payload(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    async def _open_select(self, interaction: discord.Interaction, *, action: str) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        load = await solid._category_load(guild)
        if load.error or not load.rows:
            embed, view = await _better_category_manager_payload(guild)
            return await solid._edit_or_followup(interaction, embed=embed, view=view)
        label = {"edit": "Edit a ticket routing/menu record", "default": "Choose the default ticket record", "delete": "Delete a ticket record"}.get(action, "Choose a ticket record")
        embed = discord.Embed(title=f"🧾 {label}", description="Pick an option from the dropdown below.", color=discord.Color.blurple())
        embed.add_field(name="Current Ticket Records", value=solid._category_list_text(load.rows), inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=solid.CategorySelectActionView(rows=load.rows, action=action))


def _patch() -> None:
    global _PATCHED
    solid._build_main_setup_payload = _product_main_setup_payload
    solid._build_category_manager_payload = _better_category_manager_payload
    _PATCHED = True


_patch()


def register_public_setup_recommend_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_recommend: self-explanatory setup UX active")


__all__ = ["register_public_setup_recommend_commands"]
