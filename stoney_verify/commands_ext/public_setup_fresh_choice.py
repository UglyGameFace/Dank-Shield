from __future__ import annotations

"""Plain setup choice owner for /dank setup.

This module intentionally owns the first setup screen that normal server owners
see. It keeps the product rule simple:

- no forced forms by default
- no one-server assumptions
- no Stoney Baloney IDs/branding copied into other guilds
- setup choices use plain words
- the Stoney Baloney-style ID + voice verification panel is available as an
  optional choice, not the universal default
"""

from dataclasses import dataclass
from typing import Any, Optional

import discord

from ..globals import now_utc
from . import public_setup_recommend as recommend
from . import public_setup_recovery as recovery
from . import public_setup_solid as solid

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
        short="Simple server setup with basic tickets and logs.",
        member_sees="A simple support button when they need staff help.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=False,
        panel_style="basic",
    ),
    PlainSetupChoice(
        key="help_desk",
        label="Help desk",
        emoji="🎫",
        short="Best for support tickets, customers, reports, and appeals.",
        member_sees="A clean ticket panel with fast support choices.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=False,
        panel_style="help_desk",
    ),
    PlainSetupChoice(
        key="id_check",
        label="ID check",
        emoji="🪪",
        short="Members verify with a private upload link.",
        member_sees="A verification ticket with an Upload ID button.",
        needs_tickets=True,
        needs_id=True,
        needs_voice=False,
        panel_style="id_check",
    ),
    PlainSetupChoice(
        key="voice_check",
        label="Voice check",
        emoji="🎙️",
        short="Members can ask staff to verify them in voice chat.",
        member_sees="A verification ticket with a Verify in VC option.",
        needs_tickets=True,
        needs_id=False,
        needs_voice=True,
        panel_style="voice_check",
    ),
    PlainSetupChoice(
        key="id_voice_check",
        label="ID + voice check",
        emoji="🔐",
        short="Upload-link plus voice-check style like your Stoney Baloney setup.",
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


def get_plain_setup_choice(key: Any) -> Optional[PlainSetupChoice]:
    wanted = str(key or "").strip().lower()
    for choice in SETUP_CHOICES:
        if choice.key == wanted:
            return choice
    return None


def _choice_lines() -> str:
    return "\n".join(f"{c.emoji} **{c.label}** — {c.short}" for c in SETUP_CHOICES)


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
        "Tickets: fast by default\n"
        "Forms: off unless you turn them on",
        "Pick **Choose Setup Type** first if this is a new server.",
    )


def _choice_payload(choice: PlainSetupChoice) -> dict[str, Any]:
    return {
        "setup_choice": choice.key,
        "setup_choice_label": choice.label,
        "setup_choice_description": choice.short,
        "setup_choice_member_sees": choice.member_sees,
        "setup_template_version": "plain_choices_v1",
        "ticket_service_enabled": bool(choice.needs_tickets),
        "ticket_flow_style": "fast_no_forced_form",
        "ticket_form_mode": "off",
        "ticket_open_requires_modal": False,
        "ticket_open_requires_form": False,
        "verification_panel_style": choice.panel_style,
        "verification_requires_id": bool(choice.needs_id),
        "verification_allows_voice": bool(choice.needs_voice),
        "verification_style_label": choice.label,
        "stoney_baloney_style_enabled": bool(choice.key == "id_voice_check"),
        "public_branding_mode": "guild_neutral",
    }


async def _save_choice(interaction: discord.Interaction, choice: PlainSetupChoice) -> None:
    await solid._save_config(interaction, _choice_payload(choice))  # type: ignore[attr-defined]


def _choice_preview_embed(guild: discord.Guild, choice: PlainSetupChoice) -> discord.Embed:
    embed = discord.Embed(
        title=f"{choice.emoji} {choice.label}",
        description=choice.short,
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="What members will see", value=choice.member_sees[:1024], inline=False)
    embed.add_field(
        name="What this turns on",
        value=(
            f"{_bool_icon(choice.needs_tickets)} Tickets\n"
            f"{_bool_icon(choice.needs_id)} ID upload link\n"
            f"{_bool_icon(choice.needs_voice)} Voice check\n"
            "✅ Fast ticket opening\n"
            "✅ Forms off by default"
        ),
        inline=False,
    )
    if choice.key == "id_voice_check":
        embed.add_field(
            name="Important",
            value=(
                "This is the same style as the Stoney Baloney verification flow, "
                "but it does **not** copy Stoney Baloney channel IDs, role IDs, or branding. "
                "This server still picks its own roles/channels."
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
        name="What if my server already has roles/channels?",
        value="Press **Use My Existing Server** and pick what you already use from Discord menus.",
        inline=False,
    )
    embed.add_field(
        name="What if I want your Stoney Baloney-style verification?",
        value="Pick **ID + voice check**. It gives the same kind of Upload ID + Verify in VC panel, but this guild keeps its own roles, channels, and branding.",
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
    embed.add_field(name="Setup Choices", value=_choice_lines()[:1024], inline=False)
    embed.add_field(name="Current Choice", value=service_summary[:1024], inline=False)
    embed.add_field(name="Health Check Focus", value=service_hint[:1024], inline=False)
    embed.add_field(name=f"Setup Progress: {done}/{total} complete", value=progress_text or "No setup checks ran.", inline=False)
    embed.add_field(name="Recommended Next Step", value=str(next_step or "Choose Setup Type")[:1024], inline=False)
    embed.add_field(
        name="Product Rule",
        value="Tickets open fast. Forms are optional only. Setup stays per-server.",
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
        for choice in SETUP_CHOICES:
            embed.add_field(
                name=f"{choice.emoji} {choice.label}",
                value=f"{choice.short}\nMembers see: {choice.member_sees}",
                inline=False,
            )
        await interaction.response.edit_message(embed=embed, view=PlainSetupChoiceView())

    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
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
                "🎙️ **Verification Channels** — ID/voice check channels\n"
                "🧾 **Logs + Status** — modlog, join log, status channel\n"
                "⚙️ **Behavior Settings** — ticket prefix, kick timer, verification style"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_plain:create_missing", row=0)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
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
        await interaction.response.edit_message(embed=embed, view=CreateMissingItemsView())

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
    async def _save_and_show(self, interaction: discord.Interaction, choice: PlainSetupChoice) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await solid._safe_defer_update(interaction)
        await _save_choice(interaction, choice)
        embed = _choice_preview_embed(interaction.guild, choice)  # type: ignore[arg-type]
        await solid._edit_or_followup(interaction, embed=embed, view=AfterChoiceView())

    @discord.ui.button(label="Basic server", emoji="🏠", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:basic", row=0)
    async def basic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, SETUP_CHOICES[0])

    @discord.ui.button(label="Help desk", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:helpdesk", row=0)
    async def helpdesk(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, SETUP_CHOICES[1])

    @discord.ui.button(label="ID check", emoji="🪪", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:id", row=1)
    async def id_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, SETUP_CHOICES[2])

    @discord.ui.button(label="Voice check", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:voice", row=1)
    async def voice_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, SETUP_CHOICES[3])

    @discord.ui.button(label="ID + voice check", emoji="🔐", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:id_voice", row=2)
    async def id_voice_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, SETUP_CHOICES[4])

    @discord.ui.button(label="Custom setup", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:custom", row=2)
    async def custom(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, SETUP_CHOICES[5])


class AfterChoiceView(solid.BackToSetupView):
    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_after_choice:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await PlainSetupHomeView.existing.callback(PlainSetupHomeView(), interaction, button)  # type: ignore[misc]

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_after_choice:create", row=0)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await PlainSetupHomeView.create_missing.callback(PlainSetupHomeView(), interaction, button)  # type: ignore[misc]

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
                    "**Next:** run `/dank setup`, press **Setup Check**, then post the ticket panel with `/ticket-panel post`."
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
]
