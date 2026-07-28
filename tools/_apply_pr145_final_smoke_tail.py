from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_if_present(text: str, old: str, new: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    return text


studio_path = ROOT / "stoney_verify/profile_signature_studio.py"
studio = studio_path.read_text(encoding="utf-8")
if "MEMBER_CUSTOM_BACKGROUND_KEY," not in studio:
    studio = replace_if_present(
        studio,
        "    DEFAULT_SERVER_PROFILE_STYLE,\n",
        "    DEFAULT_SERVER_PROFILE_STYLE,\n    MEMBER_CUSTOM_BACKGROUND_KEY,\n",
    )
if "from .profile_custom_background import" not in studio:
    studio = replace_if_present(
        studio,
        "from .guild_config import get_guild_config, upsert_guild_config\n",
        "from .guild_config import get_guild_config, upsert_guild_config\n"
        "from .profile_custom_background import profile_background_guide, profile_background_requirements\n",
    )
studio = studio.replace('.get("live_cards_enabled", True)', '.get("live_cards_enabled", False)')
studio = studio.replace(
    "with its colors and background.",
    "while preserving existing custom colors and artwork.",
)
studio = studio.replace(
    'description="Apply this theme\'s colors, background, and artwork",',
    'description="Change the visual family while preserving custom colors and artwork",',
)
studio = studio.replace(
    "Pick a complete look. Its colors, background, and artwork apply immediately; you can override individual parts afterward.",
    "Pick the visual family. Custom colors and custom artwork stay active. Use **Selected Theme** under Colors or **Theme Artwork** under Background only when you want those parts reset to the theme.",
)
studio = studio.replace(
    '("Server Custom Artwork", "custom", "Use the server\'s uploaded profile artwork when available.", "📎"),',
    '("Custom Artwork", "custom", "Use your personal upload, or the server upload when no personal image exists.", "📎"),',
)
sharing_line = "            f\"**Server roles:** {'Shown' if effective_privacy.get('show_server_roles', False) else 'Hidden'}\\n\"\n"
branding_line = "            f\"**Server branding:** {'Shown' if effective_privacy.get('show_server_branding', True) else 'Hidden'}\\n\"\n"
if branding_line not in studio and sharing_line in studio:
    studio = studio.replace(sharing_line, sharing_line + branding_line, 1)
if "label=\"Custom Art Guide\"" not in studio:
    marker = '''    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.success, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _preview(interaction)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
'''
    replacement = '''    @discord.ui.button(label="Custom Art Guide", emoji="📎", style=discord.ButtonStyle.secondary, row=2)
    async def art_guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(
            interaction,
            content=(
                "## 📎 Custom Profile Background\n"
                "Use `/dank profile background-upload`, attach the image, and set **server_default** only when editing server defaults.\n\n"
                + profile_background_requirements()
            ),
            file=discord.File(BytesIO(profile_background_guide()), filename="profile-background-safe-zones.png"),
            view=ProfileAppearanceView(author_id=self.author_id, server=self.server),
        )

    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.success, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _preview(interaction)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
'''
    if marker not in studio:
        raise SystemExit("profile_signature_studio.py: appearance preview marker not found")
    studio = studio.replace(marker, replacement, 1)
studio_path.write_text(studio, encoding="utf-8")

public_path = ROOT / "stoney_verify/commands_ext/public_profile_cards.py"
public = public_path.read_text(encoding="utf-8")
if "async def profile_background_upload(" not in public:
    marker = "class ProfileSettingsView(_core.ProfileSettingsView):\n"
    addition = '''async def profile_background_upload(
    interaction: discord.Interaction,
    image: discord.Attachment,
    server_default: bool = False,
) -> None:
    from stoney_verify.profile_custom_background import (
        PROFILE_BACKGROUND_UPLOAD_MAX_BYTES,
        normalize_profile_background_upload,
        profile_background_requirements,
    )
    from stoney_verify.profile_signature_studio import _invalidate, _invalidate_guild, _preview
    from stoney_verify.profile_signature_style import (
        MEMBER_CUSTOM_BACKGROUND_KEY,
        PROFILE_CUSTOM_BACKGROUND_KEY,
        SERVER_STYLE_CONFIG_KEYS,
        encode_profile_asset,
    )
    from stoney_verify.guild_config import upsert_guild_config
    from stoney_verify.profile_card_service import upsert_profile_user_preferences

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None or interaction.guild is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    if server_default:
        from .public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
    await _defer_private(interaction)
    try:
        if int(getattr(image, "size", 0) or 0) > PROFILE_BACKGROUND_UPLOAD_MAX_BYTES:
            raise ValueError("The upload is larger than 8 MB.")
        normalized = normalize_profile_background_upload(await image.read())
        encoded = encode_profile_asset(normalized)
        if server_default:
            await upsert_guild_config(
                interaction.guild.id,
                {
                    PROFILE_CUSTOM_BACKGROUND_KEY: encoded,
                    SERVER_STYLE_CONFIG_KEYS["background_mode"]: "custom",
                },
            )
            await _invalidate_guild(interaction)
        else:
            await upsert_profile_user_preferences(
                member.id,
                {
                    MEMBER_CUSTOM_BACKGROUND_KEY: encoded,
                    "signature_background_mode": "custom",
                },
            )
            await _invalidate(interaction, all_guilds=True)
    except ValueError as exc:
        return await _safe_ephemeral(
            interaction,
            f"{exc}\n\n{profile_background_requirements()}",
            ok=False,
        )
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing changed.", ok=False)
    await _preview(
        interaction,
        member=member,
        notice="✅ Custom background uploaded. Theme, custom colors, font, layout, and frame were preserved.",
    )


async def profile_background_clear(
    interaction: discord.Interaction,
    server_default: bool = False,
) -> None:
    from stoney_verify.profile_signature_studio import _invalidate, _invalidate_guild, _preview
    from stoney_verify.profile_signature_style import (
        MEMBER_CUSTOM_BACKGROUND_KEY,
        PROFILE_CUSTOM_BACKGROUND_KEY,
        SERVER_STYLE_CONFIG_KEYS,
    )
    from stoney_verify.guild_config import upsert_guild_config
    from stoney_verify.profile_card_service import upsert_profile_user_preferences

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None or interaction.guild is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    if server_default:
        from .public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
    await _defer_private(interaction)
    if server_default:
        await upsert_guild_config(
            interaction.guild.id,
            {
                PROFILE_CUSTOM_BACKGROUND_KEY: "",
                SERVER_STYLE_CONFIG_KEYS["background_mode"]: "theme",
            },
        )
        await _invalidate_guild(interaction)
    else:
        try:
            await upsert_profile_user_preferences(
                member.id,
                {
                    MEMBER_CUSTOM_BACKGROUND_KEY: "",
                    "signature_background_mode": "theme",
                },
            )
            await _invalidate(interaction, all_guilds=True)
        except ProfileStorageUnavailable:
            return await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing changed.", ok=False)
    await _preview(interaction, member=member, notice="✅ Custom background removed. Theme artwork is active again.")


'''
    if marker not in public:
        raise SystemExit("public_profile_cards.py: settings view marker not found")
    public = public.replace(marker, addition + marker, 1)
branding_choice = '            ("Server Branding", "show_server_branding", "🏰"),\n'
accounts_choice = '            ("Accounts", "show_platforms", "🔗"),\n'
if branding_choice not in public:
    if accounts_choice not in public:
        raise SystemExit("public_profile_cards.py: account privacy marker not found")
    public = public.replace(accounts_choice, accounts_choice + branding_choice, 1)
command_marker = '        ("settings", "Open your private profile privacy and platform settings.", profile_settings),\n'
command_addition = (
    '        ("background-upload", "Upload personal or server-default profile background artwork.", profile_background_upload),\n'
    '        ("background-clear", "Remove personal or server-default profile background artwork.", profile_background_clear),\n'
)
if '"background-upload"' not in public:
    if command_marker not in public:
        raise SystemExit("public_profile_cards.py: command marker not found")
    public = public.replace(command_marker, command_marker + command_addition, 1)
public = public.replace('.get("live_cards_enabled", True)', '.get("live_cards_enabled", False)')
public_path.write_text(public, encoding="utf-8")

tests = ROOT / "tests/test_profile_signature_owner_customization.py"
tests.write_text(
    '''from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from stoney_verify.profile_card_runtime import _compact_server_role_labels
from stoney_verify.profile_card_service import DEFAULT_PROFILE_PREFERENCES, normalize_preferences
from stoney_verify.profile_custom_background import normalize_profile_background_upload
from stoney_verify.profile_signature_style import (
    MEMBER_CUSTOM_BACKGROUND_KEY,
    effective_profile_style,
    encode_profile_asset,
    theme_style_updates,
)


class FakeRole:
    def __init__(self, role_id: int, name: str, position: int) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = False

    def is_default(self) -> bool:
        return False

    def __lt__(self, other):
        return self.position < other.position


def test_live_signatures_are_opt_in_by_default() -> None:
    assert DEFAULT_PROFILE_PREFERENCES["live_cards_enabled"] is False
    assert normalize_preferences({})["live_cards_enabled"] is False
    assert normalize_preferences({"live_cards_enabled": True})["live_cards_enabled"] is True


def test_server_branding_has_an_independent_default() -> None:
    assert DEFAULT_PROFILE_PREFERENCES["show_server_branding"] is True


def test_owner_truth_precedes_real_complete_roles() -> None:
    guild = SimpleNamespace(owner_id=42)
    member = SimpleNamespace(
        id=42,
        guild=guild,
        roles=[
            FakeRole(1, "Actually Extremely Long But Complete Community Role", 20),
            FakeRole(2, "Second Real Role", 10),
        ],
    )
    assert _compact_server_role_labels(member, {}) == [
        "Server Owner",
        "Actually Extremely Long But Complete Community Role",
        "Second Real Role",
    ]


def test_theme_change_preserves_independent_overrides() -> None:
    assert theme_style_updates("purple", member=True) == {"signature_theme": "purple"}
    assert theme_style_updates("dark", member=False) == {"profile_signature_theme": "dark"}


def test_member_custom_background_wins_over_server_background() -> None:
    personal = encode_profile_asset(b"personal")
    server = encode_profile_asset(b"server")
    preferences = {
        "signature_background_mode": "custom",
        MEMBER_CUSTOM_BACKGROUND_KEY: personal,
    }
    config = {"profile_signature_custom_background_b64": server}
    assert effective_profile_style(preferences, config)["custom_background"] == b"personal"


def test_uploaded_background_is_normalized_to_exact_card_size() -> None:
    source = Image.new("RGB", (2800, 600), (10, 20, 30))
    payload = BytesIO()
    source.save(payload, format="PNG")
    normalized = normalize_profile_background_upload(payload.getvalue())
    with Image.open(BytesIO(normalized)) as opened:
        assert opened.size == (1400, 300)


def test_uploaded_background_rejects_wrong_ratio() -> None:
    source = Image.new("RGB", (1400, 1400), (10, 20, 30))
    payload = BytesIO()
    source.save(payload, format="PNG")
    try:
        normalize_profile_background_upload(payload.getvalue())
    except ValueError as exc:
        assert "14:3" in str(exc)
    else:
        raise AssertionError("wrong-ratio upload was accepted")
''',
    encoding="utf-8",
)

print("Applied resilient Studio/Public tail for PR #145.")
