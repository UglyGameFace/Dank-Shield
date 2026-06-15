from __future__ import annotations

"""Plain setup choice owner for /dank setup.

This module intentionally owns the first setup screen that normal server owners
see. It keeps the product rule simple:

- no forced forms by default
- no one-server assumptions
- no Stoney Baloney IDs/branding copied into other guilds
- setup choices use plain words
- Basic Button Verification is a first-class public option
- ID / website upload verification is allowlisted only
"""

from dataclasses import dataclass
from typing import Any, Optional

import discord

from ..globals import now_utc
from . import public_setup_recommend as recommend
from . import public_setup_recovery as recovery
from . import public_setup_solid as solid
from ..setup_engine.verification_modes import id_verify_allowed_for_guild

_PATCHED = False


@dataclass(frozen=True)
class PlainSetupChoice:
    key: str
    label: str
    emoji: str
    short: str
    member_sees: str
    needs_tickets: bool
    needs_id: bool
    needs_voice: bool
    panel_style: str


SETUP_CHOICES: tuple[PlainSetupChoice, ...] = (
    PlainSetupChoice(
        key="basic_server",
        label="Basic server",
        emoji="🏠",
        short="Simple server setup with support tickets, starter logs, and normal public-server defaults.",
        member_sees="A clean support button when they need staff help.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=False,
        panel_style="basic",
    ),
    PlainSetupChoice(
        key="basic_verify",
        label="Basic verify",
        emoji="✅",
        short="Simple Verify button flow: no ID upload, no website token, no voice check, no forced ticket.",
        member_sees="A Verify button in the verification channel that grants the configured access role and removes the waiting role.",
        needs_tickets=False,
        needs_id=False,
        needs_voice=False,
        panel_style="basic_verify",
    ),
    PlainSetupChoice(
        key="help_desk",
        label="Help desk",
        emoji="🎫",
        short="Support-ticket focused setup for help requests, reports, appeals, and staff triage.",
        member_sees="A clean ticket panel with fast support choices.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=False,
        panel_style="help_desk",
    ),
    PlainSetupChoice(
        key="voice_check",
        label="Voice check",
        emoji="🎙️",
        short="Members request staff voice verification without ID upload or website upload flow.",
        member_sees="A verification ticket with a Verify in VC option.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=True,
        panel_style="voice_check",
    ),
    PlainSetupChoice(
        key="id_check",
        label="ID check",
        emoji="🪪",
        short="Private ID upload verification for allowlisted servers only.",
        member_sees="A verification ticket with an Upload ID button.",
        needs_tickets=True,
        needs_id=True,
        needs_voice=False,
        panel_style="id_check",
    ),
    PlainSetupChoice(
        key="id_voice_check",
        label="ID + voice check",
        emoji="🔐",
        short="Private ID upload plus voice-check workflow for allowlisted servers only.",
        member_sees="Upload ID, Verify in VC, reveal link, regenerate link if enabled, and website button if configured.",
        needs_tickets=True,
        needs_id=True,
        needs_voice=True,
        panel_style="id_voice_check",
    ),
    PlainSetupChoice(
        key="custom_setup",
        label="Custom setup",
        emoji="⚙️",
        short="Pick only the parts this server actually needs.",
        member_sees="Whatever you choose in setup.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=False,
        panel_style="custom",
    ),
)


CHOICES_BY_KEY: dict[str, PlainSetupChoice] = {choice.key: choice for choice in SETUP_CHOICES}


def get_plain_setup_choice(key: Any) -> Optional[PlainSetupChoice]:
    wanted = str(key or "").strip().lower()
    return CHOICES_BY_KEY.get(wanted)


def _choices_for_guild(guild: Optional[discord.Guild]) -> tuple[PlainSetupChoice, ...]:
    choices = SETUP_CHOICES if id_verify_allowed_for_guild(guild) else tuple(choice for choice in SETUP_CHOICES if not choice.needs_id)
    return choices


def _choice_lines(guild: Optional[discord.Guild] = None) -> str:
    lines = "\n".join(f"{c.emoji} **{c.label}** — {c.short}" for c in _choices_for_guild(guild))
    if not id_verify_allowed_for_guild(guild):
        lines += "\n\n🔒 ID/web verification choices are hidden for this server. Use **Basic verify** for a simple one-button verification gate."
    return lines


def _plain_saved_choice_from_cfg(cfg: Any) -> str:
    try:
        label = str(getattr(cfg, "setup_choice_label", "") or "").strip()
        if label:
            return label
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            label = str(cfg.get("setup_choice_label") or "").strip()
            if label:
                return label
            key = str(cfg.get("setup_choice") or "").strip()
            choice = get_plain_setup_choice(key)
            if choice:
                return choice.label
    except Exception:
        pass

    try:
        key = str(getattr(cfg, "setup_choice", "") or "").strip()
        choice = get_plain_setup_choice(key)
        if choice:
            return choice.label
    except Exception:
        pass

    return "Not chosen yet"


def _bool_icon(value: bool) -> str:
    return "✅" if value else "—"


async def _setup_progress_for_home(guild: discord.Guild) -> tuple[str, int, int, str]:
    try:
        return await recommend._setup_progress(guild)  # type: ignore[attr-defined]
    except Exception:
        return "Run **Setup Check** to see what is ready.", 0, 1, "Choose Setup Type"


async def _service_summary_for_home(guild: discord.Guild) -> tuple[str, str]:
    try:
        cfg = await solid.get_guild_config(guild.id, refresh=True)  # type: ignore[attr-defined]
    except Exception:
        cfg = None

    chosen = _plain_saved_choice_from_cfg(cfg)
    return (
        f"**Chosen:** {chosen}\n"
        "Tickets: fast when enabled\n"
        "Basic verify: available for every server\n"
        "Forms: off unless you turn them on",
        "Pick **Choose Setup Type** first if this is a new server.",
    )


def _choice_payload(choice: PlainSetupChoice) -> dict[str, Any]:
    basic_verify = choice.key == "basic_verify"
    return {
        "setup_choice": choice.key,
        "setup_choice_label": choice.label,
        "setup_choice_description": choice.short,
        "setup_choice_member_sees": choice.member_sees,
        "setup_template_version": "plain_choices_v2_basic_verify",
        "ticket_service_enabled": bool(choice.needs_tickets),
        "ticket_flow_style": "fast_no_forced_form",
        "ticket_form_mode": "off",
        "ticket_open_requires_modal": False,
        "ticket_open_requires_form": False,
        "verification_panel_style": choice.panel_style,
        "verification_mode": "basic_button" if basic_verify else choice.panel_style,
        "verify_mode": "basic_button" if basic_verify else choice.panel_style,
        "basic_verify_enabled": bool(basic_verify),
        "basic_button_verify_enabled": bool(basic_verify),
        "verification_requires_id": bool(choice.needs_id),
        "verification_allows_voice": bool(choice.needs_voice),
        "verification_style_label": choice.label,
        "stoney_baloney_style_enabled": bool(choice.key == "id_voice_check"),
        "public_branding_mode": "guild_neutral",
    }


async def _save_choice(interaction: discord.Interaction, choice: PlainSetupChoice) -> None:
    await solid._save_config(interaction, _choice_payload(choice))  # type: ignore[attr-defined]


def _choice_preview_embed(guild: discord.Guild, choice: PlainSetupChoice) -> discord.Embed:
    basic_verify = choice.key == "basic_verify"
    embed = discord.Embed(
        title=f"{choice.emoji} {choice.label}",
        description=choice.short,
        color=discord.Color.green() if basic_verify else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="What members will see", value=choice.member_sees[:1024], inline=False)
    embed.add_field(
        name="What this turns on",
        value=(
            f"{_bool_icon(choice.needs_tickets)} Tickets\n"
            f"{_bool_icon(basic_verify)} Basic Verify button\n"
            f"{_bool_icon(choice.needs_id)} ID upload link\n"
            f"{_bool_icon(choice.needs_voice)} Voice check\n"
            "✅ Fast ticket opening when tickets are enabled\n"
            "✅ Forms off by default"
        ),
        inline=False,
    )
    if basic_verify:
        embed.add_field(
            name="Important",
            value=(
                "This matches the simple verification style used in your servers: users press **Verify**, "
                "Dank Shield grants the configured Verified/full-access role, and removes the waiting role. "
                "No ID upload, website token, or VC check is required."
            ),
            inline=False,
        )
    elif choice.needs_id:
        embed.add_field(
            name="Allowlisted ID/Web Verification",
            value=(
                "This is restricted to allowlisted guild IDs so public servers do not accidentally inherit the old ID upload flow. "
                "This server still picks its own roles, channels, and branding."
            ),
            inline=False,
        )
    embed.add_field(
        name="Next",
        value=(
            "Press **Use My Existing Server** if your roles/channels already exist.\n"
            "Press **Create Missing Items** if you want Dank Shield to create the basic missing pieces.\n"
            "Press **Setup Check** when you are done."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • choice saved per server")
    return embed


async def _edit_setup_message(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    """Edit the current setup message without assuming response state."""
    if interaction.response.is_done():
        await solid._edit_or_followup(interaction, embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


async def _open_existing_server_setup(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="🧩 Use My Existing Server",
        description=(
            "Pick the roles/channels/folders your server already uses. Names do not matter. "
            "Dank Shield saves Discord IDs per server."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Sections",
        value=(
            "🎫 **Ticket Basics** — ticket folders, staff role, transcripts\n"
            "🎭 **Access Roles** — waiting role, approved role, member role\n"
            "🎙️ **Verification Channels** — basic verify, ID, or voice check channels\n"
            "🧾 **Logs + Status** — modlog, join log, status channel\n"
            "⚙️ **Behavior Settings** — ticket prefix, kick timer, verification style"
        ),
        inline=False,
    )
    await _edit_setup_message(interaction, embed=embed, view=solid.ChooseExistingView())


async def _open_create_missing_items(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="✨ Create Missing Items",
        description=(
            "Dank Shield can create missing starter roles/channels/folders. "
            "It does **not** delete your server setup."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Before it creates anything",
        value="Review this screen. Press **Create Basic Missing Items** only if you want the starter layout.",
        inline=False,
    )
    await _edit_setup_message(interaction, embed=embed, view=CreateMissingItemsView())


async def _open_ticket_menu_options(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    try:
        embed, view = await recommend._better_category_manager_payload(guild, title="🧾 Ticket Menu Options")  # type: ignore[attr-defined]
    except Exception:
        embed, view = await solid._build_category_manager_payload(guild)  # type: ignore[attr-defined]
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_plain_health(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    try:
        embed = await recommend._build_plain_setup_health_embed(guild)  # type: ignore[attr-defined]
        view: Optional[discord.ui.View] = getattr(recommend, "SetupHealthHelpView", solid.BackToSetupView)()
    except Exception:
        embed = await solid._build_health_embed(guild)
        view = solid.BackToSetupView()
    await solid._edit_or_followup(interaction, embed=embed, view=view)


def _build_setup_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Shield Setup Help",
        description="Simple answers for the setup screen.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="What should I press first?",
        value="Press **Choose Setup Type**. Pick the closest match. You can change it later.",
        inline=False,
    )
    embed.add_field(
        name="What is Basic verify?",
        value="A public-safe Verify button. Members click it, get the configured Verified/full-access role, and lose the waiting role. No ID upload, website token, voice check, or forced ticket.",
        inline=False,
    )
    embed.add_field(
        name="What if my server already has roles/channels?",
        value="Press **Use My Existing Server** and pick what you already use from Discord menus.",
        inline=False,
    )
    embed.add_field(
        name="What if I want ID upload verification?",
        value="ID/web upload choices are allowlisted only. Public servers should use **Basic verify**, **Voice check**, **Help desk**, or **Custom setup**.",
        inline=False,
    )
    embed.add_field(
        name="Will members be forced into forms?",
        value="No. Tickets are fast by default. Forms stay off unless you turn them on later.",
        inline=False,
    )
    embed.add_field(
        name="Will this mess with other servers?",
        value="No. Every setup choice is saved per guild. No cross-server IDs are reused.",
        inline=False,
    )
    return embed


async def _plain_choice_main_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress_for_home(guild)
    service_summary, service_hint = await _service_summary_for_home(guild)

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            "Pick what this server actually needs. You do **not** have to use the same setup as another server.\n\n"
            "Start with **Choose Setup Type**. Then map your roles/channels or let Dank Shield create missing basics."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Setup Choices", value=_choice_lines(guild)[:1024], inline=False)
    embed.add_field(name="Current Choice", value=service_summary[:1024], inline=False)
    embed.add_field(name="Health Check Focus", value=service_hint[:1024], inline=False)
    embed.add_field(name=f"Setup Progress: {done}/{total} complete", value=progress_text or "No setup checks ran.", inline=False)
    embed.add_field(name="Recommended Next Step", value=str(next_step or "Choose Setup Type")[:1024], inline=False)
    embed.add_field(
        name="Product Rule",
        value="Basic verify is public-safe. Tickets open fast. Forms are optional only. Setup stays per-server.",
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup")
    return embed, PlainSetupHomeView()


class PlainSetupHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Setup Type", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain:choose", row=0)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧭 Choose Setup Type",
            description="Pick the closest match. This only saves the style this server wants. You can change it later.",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        choices = _choices_for_guild(interaction.guild)
        for choice in choices:
            embed.add_field(
                name=f"{choice.emoji} {choice.label}",
                value=f"{choice.short}\nMembers see: {choice.member_sees}",
                inline=False,
            )
        if not id_verify_allowed_for_guild(interaction.guild):
            embed.add_field(
                name="🔒 ID/web verification hidden",
                value="Use **Basic verify** for simple one-button verification. ID/web upload verification is only available for allowlisted guild IDs.",
                inline=False,
            )
        await interaction.response.edit_message(embed=embed, view=PlainSetupChoiceView(guild=interaction.guild))

    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server_setup(interaction)

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_plain:create_missing", row=0)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_create_missing_items(interaction)

    @discord.ui.button(label="Ticket Menu Options", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain:ticket_menu", row=1)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_ticket_menu_options(interaction)

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_plain_health(interaction)

    @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain:help", row=1)
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(embed=_build_setup_help_embed(), view=solid.BackToSetupView())


class PlainSetupChoiceView(solid.BackToSetupView):
    def __init__(self, *, guild: Optional[discord.Guild] = None) -> None:
        super().__init__()
        if not id_verify_allowed_for_guild(guild):
            for child in list(getattr(self, "children", []) or []):
                if str(getattr(child, "custom_id", "") or "") in {"dank_setup_choice:id", "dank_setup_choice:id_voice"}:
                    try:
                        self.remove_item(child)
                    except Exception:
                        pass

    async def _save_and_show(self, interaction: discord.Interaction, choice: PlainSetupChoice) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        if choice.needs_id and not id_verify_allowed_for_guild(guild):
            return await interaction.response.send_message(
                "🔒 This server uses Basic Button Verification. ID/web verification is only available for allowlisted guild IDs.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        await solid._safe_defer_update(interaction)
        await _save_choice(interaction, choice)
        embed = _choice_preview_embed(guild, choice)
        await solid._edit_or_followup(interaction, embed=embed, view=AfterChoiceView())

    @discord.ui.button(label="Basic server", emoji="🏠", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:basic", row=0)
    async def basic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["basic_server"])

    @discord.ui.button(label="Basic verify", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:basic_verify", row=0)
    async def basic_verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["basic_verify"])

    @discord.ui.button(label="Help desk", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:helpdesk", row=1)
    async def helpdesk(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["help_desk"])

    @discord.ui.button(label="Voice check", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:voice", row=1)
    async def voice_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["voice_check"])

    @discord.ui.button(label="ID check", emoji="🪪", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:id", row=2)
    async def id_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["id_check"])

    @discord.ui.button(label="ID + voice check", emoji="🔐", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:id_voice", row=2)
    async def id_voice_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["id_voice_check"])

    @discord.ui.button(label="Custom setup", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:custom", row=3)
    async def custom(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["custom_setup"])


class AfterChoiceView(solid.BackToSetupView):
    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_after_choice:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server_setup(interaction)

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_after_choice:create", row=0)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_create_missing_items(interaction)

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup_after_choice:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_plain_health(interaction)


class CreateMissingItemsView(solid.BackToSetupView):
    @discord.ui.button(label="Create Basic Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_plain:confirm_create_missing", row=0)
    async def confirm_create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                try:
                    created, skipped, error = await solid._seed_recommended_categories(interaction.guild)
                except Exception as e:
                    created, skipped, error = [], [], f"{type(e).__name__}: {str(e)[:220]}"

                msg = (
                    "✅ Missing starter items were handled.\n\n"
                    "**Next:** run `/dank setup`, press **Setup Check**, then post the panel you need: `/verify panel` for Basic Verify or `/ticket-panel post` for tickets."
                )
                if error:
                    msg += f"\n\n⚠️ Ticket menu options could not be checked: `{error}`"
                elif created:
                    msg += f"\n\nCreated ticket menu options: {', '.join(f'`{x}`' for x in created)}"
                elif skipped:
                    msg += "\n\nTicket menu options already existed."

                await interaction.followup.send(
                    msg,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception as e:
            msg = f"❌ Create Missing Items failed: `{type(e).__name__}: {str(e)[:250]}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


FreshChoiceHomeView = PlainSetupHomeView
FreshServerChoiceView = PlainSetupHomeView


def _patch() -> None:
    global _PATCHED
    try:
        recovery._ORIGINAL_BUILD_MAIN = _plain_choice_main_payload
        solid._build_main_setup_payload = recovery._build_main_with_recovery
    except Exception:
        solid._build_main_setup_payload = _plain_choice_main_payload
    _PATCHED = True


_patch()


def register_public_setup_fresh_choice_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_fresh_choice: plain setup choices active")


__all__ = [
    "register_public_setup_fresh_choice_commands",
    "get_plain_setup_choice",
    "PlainSetupChoice",
    "SETUP_CHOICES",
    "CHOICES_BY_KEY",
]
