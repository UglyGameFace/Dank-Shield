from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import profile_signature_studio
from stoney_verify.commands_ext import public_profile_cards
from stoney_verify.commands_ext.public_profile_cards_core import _settings_embed


class _Avatar:
    url = "https://cdn.example/avatar.png"


class _Member:
    id = 42
    display_avatar = _Avatar()


class _Response:
    def __init__(self) -> None:
        self.edited = None

    def is_done(self) -> bool:
        return False

    async def edit_message(self, **payload):
        self.edited = payload


class _Interaction:
    def __init__(self) -> None:
        self.response = _Response()


async def _noop(*_args, **_kwargs):
    return None


def test_privacy_panel_has_obvious_account_management_and_navigation():
    view = public_profile_cards.ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={},
        guild_settings={},
    )
    labels = {str(child.label) for child in view.children if isinstance(child, discord.ui.Button)}
    assert "Manage Accounts" in labels
    assert "Preview Signature" in labels
    assert "Back to Profile" in labels


def test_every_platform_detail_uses_explicit_public_private_language():
    for platform in profile_signature_studio.PLATFORM_SPECS:
        private_view = profile_signature_studio.PlatformDetailView(
            author_id=42,
            platform=platform,
            entry={"username": "player", "shared": False},
        )
        public_view = profile_signature_studio.PlatformDetailView(
            author_id=42,
            platform=platform,
            entry={"username": "player", "shared": True},
        )
        private_button = next(child for child in private_view.children if child.label == "Make Public")
        public_button = next(child for child in public_view.children if child.label == "Make Private")
        assert private_button.label == "Make Public"
        assert private_button.disabled is False
        assert public_button.label == "Make Private"
        assert public_button.disabled is False


def test_unsaved_platform_cannot_be_published_before_username_exists():
    view = profile_signature_studio.PlatformDetailView(author_id=42, platform="steam", entry={})
    button = next(child for child in view.children if child.label == "Make Public")
    assert button.label == "Make Public"
    assert button.disabled is True


def test_privacy_embed_points_to_manage_accounts_and_marks_visibility():
    embed = _settings_embed(
        _Member(),
        {
            "preferences": {},
            "platforms": {
                "steam": {"username": "UglyGameFace", "shared": False, "url": "https://steamcommunity.com/id/UglyGameFace"},
                "xbox": {"username": "UglyGameFace", "shared": True, "url": ""},
            },
        },
        {"settings": {}},
        {"preferences": {}},
    )
    assert embed.title == "🔐 Profile Privacy"
    field = next(field for field in embed.fields if field.name == "Gaming & social accounts")
    assert "Steam" in field.value and "🔒 Private" in field.value
    assert "Xbox" in field.value and "🌐 Public" in field.value
    assert "Manage Accounts" in field.value


def test_edit_private_completes_component_response_without_followup():
    interaction = _Interaction()
    asyncio.run(profile_signature_studio._edit_private(interaction, content="done"))
    assert interaction.response.edited is not None
    assert interaction.response.edited["content"] == "done"
    assert interaction.response.edited["attachments"] == []


def test_preview_paths_edit_the_original_response_instead_of_leaving_loading_followup():
    public_source = open("stoney_verify/commands_ext/public_profile_cards.py", encoding="utf-8").read()
    studio_source = open("stoney_verify/profile_signature_studio.py", encoding="utf-8").read()
    assert "await interaction.edit_original_response(**payload)" in public_source
    assert "await _defer_private(interaction, component_update=True)" in public_source
    assert 'await _preview(interaction, member=member, notice=f"✅ {message}")' in studio_source
    assert "class SignaturePreviewView" in studio_source
