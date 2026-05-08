from __future__ import annotations

"""Plain-language setup choices for Dank Shield.

This module is intentionally presentation-focused. It does not create roles,
channels, tickets, or verification records by itself. The public setup command
can use these choices to store a per-guild setup intent, preview what members
will see, and then guide the owner through only the settings that choice needs.

Product rule:
- Keep labels simple.
- Do not assume every guild wants the Stoney Baloney flow.
- Keep the Stoney Baloney-style ID + voice flow available as a choice.
- Never hardcode Stoney Baloney role IDs, channel IDs, or server branding.
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


SETUP_TEMPLATE_CHOICES: tuple[SetupTemplateChoice, ...] = (
    SetupTemplateChoice(
        key="basic_server",
        label="Basic server",
        emoji="🟢",
        short_description="Simple welcome/check-in and basic tickets.",
        member_preview="Members get a simple welcome/check-in and a quick way to ask for help.",
        staff_preview="Best for normal communities that want simple setup without extra verification steps.",
        stores={
            "setup_choice": "basic_server",
            "setup_choice_label": "Basic server",
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "simple",
            "verification_requires_id": False,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="help_desk",
        label="Help desk",
        emoji="🛠️",
        short_description="Ticket support for members or customers.",
        member_preview="Members click one button to open a help ticket. No form is required by default.",
        staff_preview="Best for support servers, stores, creators, and paid communities.",
        stores={
            "setup_choice": "help_desk",
            "setup_choice_label": "Help desk",
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "ticket_types_enabled": True,
            "verification_panel_style": "simple",
            "verification_requires_id": False,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="id_check",
        label="ID check",
        emoji="🪪",
        short_description="Users verify with a private upload link.",
        member_preview="Members open a ticket and use a private upload link for ID verification.",
        staff_preview="Best when staff needs to review uploaded verification before giving access.",
        stores={
            "setup_choice": "id_check",
            "setup_choice_label": "ID check",
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "id_check",
            "verification_requires_id": True,
            "verification_allows_voice": False,
        },
    ),
    SetupTemplateChoice(
        key="voice_check",
        label="Voice check",
        emoji="🎙️",
        short_description="Users can ask staff to verify them in voice chat.",
        member_preview="Members can request a voice check and wait for staff instructions.",
        staff_preview="Best when staff wants to talk to users before giving access.",
        stores={
            "setup_choice": "voice_check",
            "setup_choice_label": "Voice check",
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "voice_check",
            "verification_requires_id": False,
            "verification_allows_voice": True,
        },
    ),
    SetupTemplateChoice(
        key="id_voice_check",
        label="ID + voice check",
        emoji="🛡️",
        short_description="Upload link plus optional voice check, like your current setup.",
        member_preview="Members open a verification ticket, use the upload link, or ask for voice verification.",
        staff_preview="Best for servers that want the Stoney Baloney-style verification flow without hardcoded branding.",
        stores={
            "setup_choice": "id_voice_check",
            "setup_choice_label": "ID + voice check",
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
        label="Custom setup",
        emoji="⚙️",
        short_description="Choose only what this server needs.",
        member_preview="Members only see the features you turn on.",
        staff_preview="Best when you want to build your own setup step by step.",
        stores={
            "setup_choice": "custom_setup",
            "setup_choice_label": "Custom setup",
            "ticket_flow_mode": "instant",
            "ticket_form_required": False,
            "verification_panel_style": "custom",
        },
    ),
)


_TEMPLATE_BY_KEY = {choice.key: choice for choice in SETUP_TEMPLATE_CHOICES}


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


def build_setup_template_embed(*, selected_key: Optional[str] = None, guild_name: str = "this server") -> discord.Embed:
    selected = get_setup_template(selected_key or "")

    embed = discord.Embed(
        title="Choose what this server needs",
        description=(
            "Pick the closest option. You can preview it first and change it later.\n\n"
            "No option forces long forms on members by default."
        ),
        color=discord.Color.blurple(),
    )

    for choice in SETUP_TEMPLATE_CHOICES:
        marker = "✅ Selected" if selected and selected.key == choice.key else ""
        value = (
            f"{choice.short_description}\n"
            f"**Members see:** {choice.member_preview}\n"
            f"**Good for:** {choice.staff_preview}"
        )
        if marker:
            value = f"{marker}\n{value}"
        embed.add_field(name=f"{choice.emoji} {choice.label}", value=value[:1024], inline=False)

    if selected:
        embed.set_footer(text=f"Previewing {selected.label} for {guild_name}. Nothing is published until you confirm.")
    else:
        embed.set_footer(text="Use the menu below to choose. Nothing is changed until you confirm.")

    return embed


def plain_setup_choice_summary(key: str) -> str:
    choice = get_setup_template(key)
    if choice is None:
        return "Unknown setup choice."
    return f"{choice.emoji} {choice.label} — {choice.short_description}"


__all__ = [
    "SETUP_TEMPLATE_CHOICES",
    "SetupTemplateChoice",
    "build_setup_template_embed",
    "build_setup_template_select_options",
    "get_setup_template",
    "plain_setup_choice_summary",
    "setup_template_payload",
]
