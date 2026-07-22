from __future__ import annotations

"""Plain-language Quick Setup plans for Dank Shield.

This module is presentation-focused. It does not create roles, channels,
tickets, or verification records by itself. Each plan stores one clear AIO
intent that the guided setup can use to ask only for missing requirements.

Product rules:
- Keep plan labels simple and outcome-focused.
- Recommended Setup must match the live public picker defaults.
- Quick Setup should inspect what already exists instead of making owners pick
  between competing "existing" and "create" paths.
- Keep approved ID/Web verification available only as a selectable plan where
  the public setup owner allows it.
- Never hardcode private-server IDs, role names, channel IDs, or branding.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord


@dataclass(frozen=True)
class SetupTemplateChoice:
    key: str
    label: str
    emoji: str
    short_description: str
    member_preview: str
    staff_preview: str
    stores: Mapping[str, Any]

    @property
    def select_label(self) -> str:
        return self.label[:100]

    @property
    def select_description(self) -> str:
        return self.short_description[:100]


def _service_flags(
    *,
    tickets: bool,
    verification: bool,
    voice: bool,
    spamguard: bool,
    logs: bool,
) -> dict[str, bool]:
    """Return the canonical public service switches for one Quick Setup plan."""

    return {
        "tickets_enabled": bool(tickets),
        "ticket_service_enabled": bool(tickets),
        "verification_enabled": bool(verification),
        "voice_verification_enabled": bool(voice),
        "spam_guard_enabled": bool(spamguard),
        "moderation_enabled": bool(logs),
    }


SETUP_TEMPLATE_CHOICES: tuple[SetupTemplateChoice, ...] = (
    SetupTemplateChoice(
        key="basic_server",
        label="Recommended Setup",
        emoji="🏠",
        short_description=(
            "Fast AIO starter with tickets, SpamGuard, and essential logs."
        ),
        member_preview=(
            "Members get a simple way to ask staff for help while the server "
            "also gets baseline spam protection."
        ),
        staff_preview=(
            "Best for most communities that want useful protection and support "
            "without adding a verification requirement."
        ),
        stores={
            "setup_choice": "basic_server",
            "setup_choice_label": "Recommended Setup",
            **_service_flags(
                tickets=True,
                verification=False,
                voice=False,
                spamguard=True,
                logs=True,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "basic",
            "verification_requires_id": False,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="basic_verify",
        label="Simple Verify",
        emoji="✅",
        short_description=(
            "One-button member verification with SpamGuard and essential logs."
        ),
        member_preview=(
            "Members press one Verify button to receive the approved-member role."
        ),
        staff_preview=(
            "Best when the server only needs a simple access gate and does not "
            "need ID upload or voice verification."
        ),
        stores={
            "setup_choice": "basic_verify",
            "setup_choice_label": "Simple Verify",
            **_service_flags(
                tickets=False,
                verification=True,
                voice=False,
                spamguard=True,
                logs=True,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "basic_verify",
            "verification_mode": "basic_button",
            "verify_mode": "basic_button",
            "basic_verify_enabled": True,
            "basic_button_verify_enabled": True,
            "verification_requires_id": False,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="help_desk",
        label="Help Desk / Tickets",
        emoji="🎫",
        short_description=(
            "Support tickets with SpamGuard and essential staff logs."
        ),
        member_preview=(
            "Members use a ticket panel to ask for help, report problems, or "
            "contact staff privately."
        ),
        staff_preview=(
            "Best for support servers, stores, creators, and communities that "
            "want tickets without member verification."
        ),
        stores={
            "setup_choice": "help_desk",
            "setup_choice_label": "Help Desk / Tickets",
            **_service_flags(
                tickets=True,
                verification=False,
                voice=False,
                spamguard=True,
                logs=True,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "ticket_types_enabled": True,
            "verification_panel_style": "help_desk",
            "verification_requires_id": False,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="voice_check",
        label="Voice Verify",
        emoji="🎙️",
        short_description=(
            "Member verification with a staff voice-check option."
        ),
        member_preview=(
            "Members can verify and request a staff voice check when required."
        ),
        staff_preview=(
            "Best when staff wants to speak with members before granting access."
        ),
        stores={
            "setup_choice": "voice_check",
            "setup_choice_label": "Voice Verify",
            **_service_flags(
                tickets=True,
                verification=True,
                voice=True,
                spamguard=True,
                logs=True,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "voice_check",
            "verification_requires_id": False,
            "verification_allows_voice": True,
        },
    ),
    SetupTemplateChoice(
        key="id_check",
        label="ID / Web Verify",
        emoji="🪪",
        short_description="Private ID/Web verification for approved servers.",
        member_preview=(
            "Members use the private verification flow for staff review."
        ),
        staff_preview=(
            "Best for approved servers that need staff-reviewed private "
            "verification before granting access."
        ),
        stores={
            "setup_choice": "id_check",
            "setup_choice_label": "ID / Web Verify",
            **_service_flags(
                tickets=True,
                verification=True,
                voice=False,
                spamguard=True,
                logs=True,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "id_check",
            "verification_requires_id": True,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="id_voice_check",
        label="ID / Web + Voice",
        emoji="🔐",
        short_description=(
            "Private ID/Web verification plus a staff voice-check option."
        ),
        member_preview=(
            "Members can complete private verification and request a voice check."
        ),
        staff_preview=(
            "Best for approved servers that need both private review and voice "
            "verification options."
        ),
        stores={
            "setup_choice": "id_voice_check",
            "setup_choice_label": "ID / Web + Voice",
            **_service_flags(
                tickets=True,
                verification=True,
                voice=True,
                spamguard=True,
                logs=True,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "id_voice_check",
            "verification_requires_id": True,
            "verification_allows_voice": True,
            "verification_resident_role_enabled": True,
        },
    ),
    SetupTemplateChoice(
        key="custom_setup",
        label="Choose Core Features",
        emoji="⚙️",
        short_description=(
            "Choose only the core modules that need roles, channels, or permissions."
        ),
        member_preview="Members only see the core features you enable.",
        staff_preview=(
            "Best when you want manual control. Server Design, Backups, activity "
            "tools, and other AIO options remain available under Manage Setup."
        ),
        stores={
            "setup_choice": "custom_setup",
            "setup_choice_label": "Choose Core Features",
            **_service_flags(
                tickets=False,
                verification=False,
                voice=False,
                spamguard=False,
                logs=False,
            ),
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "custom",
        },
    ),
)


_TEMPLATE_BY_KEY = {
    choice.key: choice
    for choice in SETUP_TEMPLATE_CHOICES
}


def get_setup_template(key: str) -> Optional[SetupTemplateChoice]:
    return _TEMPLATE_BY_KEY.get(str(key or "").strip())


def setup_template_payload(key: str) -> dict[str, Any]:
    choice = get_setup_template(key)
    if choice is None:
        raise ValueError(f"Unknown setup template: {key!r}")
    return dict(choice.stores)


def build_setup_template_select_options() -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=choice.select_label,
            value=choice.key,
            description=choice.select_description,
            emoji=choice.emoji,
        )
        for choice in SETUP_TEMPLATE_CHOICES
    ]


def _compact_choice_line(choice: SetupTemplateChoice) -> str:
    return (
        f"{choice.emoji} **{choice.label}** — "
        f"{choice.short_description}"
    )


def build_setup_template_embed(
    *,
    selected_key: Optional[str] = None,
    guild_name: str = "this server",
) -> discord.Embed:
    selected = get_setup_template(selected_key or "")

    if selected:
        embed = discord.Embed(
            title=f"{selected.emoji} {selected.label}",
            description=(
                "✅ **Selected for preview.**\n\n"
                "Press **Use This Plan** to save this choice.\n"
                "Press **Preview** if you only wanted to look.\n\n"
                "**Nothing is changed until you confirm.**"
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="What this plan does",
            value=selected.short_description[:1024],
            inline=False,
        )
        embed.add_field(
            name="What members see",
            value=selected.member_preview[:1024],
            inline=False,
        )
        embed.add_field(
            name="Best for",
            value=selected.staff_preview[:1024],
            inline=False,
        )

        if selected.key == "custom_setup":
            embed.add_field(
                name="Next",
                value=(
                    "After you press **Use This Plan**, choose the core modules "
                    "this server needs. Quick Setup then checks only those "
                    "requirements. Other AIO tools remain under **Manage Setup**."
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Next",
                value=(
                    "After you save this plan, **Quick Setup checks what already "
                    "exists and asks only for anything still missing**. You do not "
                    "need to choose between separate existing/create setup paths."
                ),
                inline=False,
            )

        embed.set_footer(
            text=(
                f"Previewing {selected.label} for {guild_name}. "
                "Nothing changes until you confirm."
            )
        )
        return embed

    embed = discord.Embed(
        title="Choose a Quick Setup Plan",
        description=(
            "Pick the closest goal from the menu below. Dank Shield applies "
            "smart defaults, checks what your server already has, and then asks "
            "only for missing essentials."
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Quick choices",
        value="\n".join(
            _compact_choice_line(choice)
            for choice in SETUP_TEMPLATE_CHOICES
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Fastest picks",
        value=(
            "Most communities: **Recommended Setup**.\n"
            "One-button access: **Simple Verify**.\n"
            "Support-focused server: **Help Desk / Tickets**.\n"
            "Manual control: **Choose Core Features**."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Choose one plan. Nothing changes until you confirm."
    )
    return embed


def plain_setup_choice_summary(key: str) -> str:
    choice = get_setup_template(key)
    if choice is None:
        return "Unknown setup choice."
    return (
        f"{choice.emoji} {choice.label} — "
        f"{choice.short_description}"
    )


__all__ = [
    "SETUP_TEMPLATE_CHOICES",
    "SetupTemplateChoice",
    "build_setup_template_embed",
    "build_setup_template_select_options",
    "get_setup_template",
    "plain_setup_choice_summary",
    "setup_template_payload",
]
