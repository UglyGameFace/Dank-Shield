from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "stoney_verify/profile_signature_studio.py"
WORKFLOW_PATH = ROOT / ".github/workflows/profile-runtime-diagnostics.yml"
TEST_PATH = ROOT / "tests/test_profile_signature_color_mixer.py"

source = SOURCE_PATH.read_text(encoding="utf-8")

constant_marker = "}\n\n\ndef _member(interaction: discord.Interaction) -> Optional[discord.Member]:"
constant_block = '''}

_COLOR_MIX_CHOICES: tuple[tuple[str, str, str, str, str], ...] = (
    ("neon_green", "Neon Green", "#8FFF52", "🟢", "Bright 420-style green"),
    ("emerald", "Emerald", "#20CF70", "💚", "Deep clean green"),
    ("cyan", "Electric Cyan", "#2DE8CD", "🩵", "Bright teal-cyan glow"),
    ("ice_blue", "Ice Blue", "#46B1FF", "🔵", "Cold bright blue"),
    ("royal_blue", "Royal Blue", "#256CE5", "💙", "Strong gaming blue"),
    ("violet", "Violet", "#B85BFF", "🟣", "Neon purple accent"),
    ("magenta", "Magenta", "#FF3D9A", "🩷", "Hot pink-magenta glow"),
    ("red", "Ember Red", "#FF4E44", "🔴", "Competitive red"),
    ("orange", "Blaze Orange", "#FF8A3D", "🟠", "Warm energetic orange"),
    ("gold", "Premium Gold", "#FFCC52", "🟡", "Black-and-gold highlight"),
    ("white", "Clean White", "#F8FAFD", "⚪", "Bright neutral highlight"),
    ("graphite", "Graphite", "#7B8798", "⚫", "Muted steel accent"),
)
_COLOR_MIX_BY_KEY = {
    key: {"label": label, "hex": value, "emoji": emoji, "description": description}
    for key, label, value, emoji, description in _COLOR_MIX_CHOICES
}
_ADVANCED_HEX_VALUE = "__advanced_hex__"


def _member(interaction: discord.Interaction) -> Optional[discord.Member]:'''
if source.count(constant_marker) != 1:
    raise SystemExit("profile studio color constant marker changed")
source = source.replace(constant_marker, constant_block, 1)

old_modal = '''class CustomProfileColorsModal(discord.ui.Modal):
    def __init__(self, *, server: bool, author_id: int) -> None:
        super().__init__(title="Advanced Signature Colors", timeout=900)
        self.server = bool(server)
        self.author_id = int(author_id)
        self.primary = discord.ui.TextInput(label="Primary color", placeholder="#22DCFF", max_length=7)
        self.secondary = discord.ui.TextInput(label="Secondary color", placeholder="#BC42FF", max_length=7)
        self.add_item(self.primary)
        self.add_item(self.secondary)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _private(interaction, content="❌ Only the person who opened this editor can submit it.")
        try:
            primary = normalize_hex_color(str(self.primary.value))
            secondary = normalize_hex_color(str(self.secondary.value))
        except ValueError as exc:
            return await _private(interaction, content=f"❌ {exc}")
        if self.server:
            await _save_server_style(
                interaction,
                {
                    SERVER_STYLE_CONFIG_KEYS["color_mode"]: "custom",
                    SERVER_STYLE_CONFIG_KEYS["custom_primary"]: primary,
                    SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: secondary,
                },
                message="Advanced server signature colors saved.",
            )
        else:
            await _save_member_style(
                interaction,
                {
                    "signature_color_mode": "custom",
                    "signature_custom_primary": primary,
                    "signature_custom_secondary": secondary,
                },
                message="Your advanced signature colors were saved.",
            )
'''
new_modal = '''def _color_mix_updates(primary_key: str, secondary_key: str, *, server: bool) -> dict[str, str]:
    primary = _COLOR_MIX_BY_KEY.get(str(primary_key or ""))
    secondary = _COLOR_MIX_BY_KEY.get(str(secondary_key or ""))
    if primary is None or secondary is None:
        raise ValueError("Choose both colors from the mixer.")
    primary_hex = normalize_hex_color(primary["hex"])
    secondary_hex = normalize_hex_color(secondary["hex"])
    if server:
        return {
            SERVER_STYLE_CONFIG_KEYS["color_mode"]: "custom",
            SERVER_STYLE_CONFIG_KEYS["custom_primary"]: primary_hex,
            SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: secondary_hex,
        }
    return {
        "signature_color_mode": "custom",
        "signature_custom_primary": primary_hex,
        "signature_custom_secondary": secondary_hex,
    }


def _color_mixer_embed(*, primary_key: str = "") -> discord.Embed:
    primary = _COLOR_MIX_BY_KEY.get(primary_key)
    if primary is None:
        return discord.Embed(
            title="🎨 Mix Your Signature Colors",
            description=(
                "**Step 1 of 2:** choose the main accent used for frames, badges, and highlights. "
                "You will choose a second accent next. No color codes are needed."
            ),
            color=discord.Color.blurple(),
        )
    return discord.Embed(
        title="🎨 Mix Your Signature Colors",
        description=(
            f"**Primary:** {primary['emoji']} **{primary['label']}**\n"
            "**Step 2 of 2:** choose a different or matching secondary accent for gradients, "
            "platform details, and supporting highlights."
        ),
        color=discord.Color.blurple(),
    )


def _color_choices(*, selected_key: str = "") -> list[Any]:
    choices = [
        make_choice(
            item["label"],
            key,
            description=item["description"],
            emoji=item["emoji"],
            default=key == selected_key,
        )
        for key, item in _COLOR_MIX_BY_KEY.items()
    ]
    choices.append(
        make_choice(
            "Advanced Hex Codes",
            _ADVANCED_HEX_VALUE,
            description="Last-resort manual entry for an exact brand color",
            emoji="⌨️",
        )
    )
    return choices


async def _open_appearance_from_mixer(
    interaction: discord.Interaction,
    *,
    author_id: int,
    server: bool,
) -> None:
    await _edit_private(
        interaction,
        embed=discord.Embed(
            title="🎨 Signature Appearance" if not server else "🎨 Server Signature Appearance",
            description=(
                "Choose a theme, font, colors, background, layout, and avatar frame. "
                "Every saved change immediately generates a fresh preview."
            ),
            color=discord.Color.blurple(),
        ),
        view=ProfileAppearanceView(author_id=author_id, server=server),
    )


async def _open_color_mixer(
    interaction: discord.Interaction,
    *,
    server: bool,
    author_id: int,
    primary_key: str = "",
    replace: bool = False,
) -> None:
    member = _member(interaction)
    if member is None or int(member.id) != int(author_id):
        return await _private(interaction, content="❌ Open your own signature appearance settings first.")

    async def go_home(component: discord.Interaction) -> None:
        if primary_key:
            await _open_color_mixer(
                component,
                server=server,
                author_id=author_id,
                replace=True,
            )
        else:
            await _open_appearance_from_mixer(
                component,
                author_id=author_id,
                server=server,
            )

    async def picked(component: discord.Interaction, value: str) -> None:
        if value == _ADVANCED_HEX_VALUE:
            primary_default = str(_COLOR_MIX_BY_KEY.get(primary_key, {}).get("hex", ""))
            await component.response.send_modal(
                CustomProfileColorsModal(
                    server=server,
                    author_id=author_id,
                    primary_default=primary_default,
                )
            )
            return
        if value not in _COLOR_MIX_BY_KEY:
            return await _private(component, content="❌ That color is no longer available.")
        if not primary_key:
            await _open_color_mixer(
                component,
                server=server,
                author_id=author_id,
                primary_key=value,
                replace=True,
            )
            return

        updates = _color_mix_updates(primary_key, value, server=server)
        primary = _COLOR_MIX_BY_KEY[primary_key]
        secondary = _COLOR_MIX_BY_KEY[value]
        message = f"Color mix saved: **{primary['label']} + {secondary['label']}**."
        saver = _save_server_style if server else _save_member_style
        await saver(component, updates, message=message)

    payload = {
        "content": (
            "Choose visually from named colors. **Advanced Hex Codes** is intentionally last "
            "for exact colors that are not in the mixer."
        ),
        "embed": _color_mixer_embed(primary_key=primary_key),
        "view": DankPickerView(
            author_id=author_id,
            choices=_color_choices(),
            on_pick=picked,
            custom_id=(
                f"dank:profile:color_mix:{'secondary' if primary_key else 'primary'}:"
                f"{'server' if server else 'member'}:{author_id}"
            ),
            placeholder=(
                "Choose the secondary color…" if primary_key else "Choose the primary color…"
            ),
            title="Profile Signature Color Mixer",
            home_label="Back",
            on_home=go_home,
        ),
    }
    if replace:
        await _edit_private(interaction, **payload)
    else:
        await _private(interaction, **payload)


class CustomProfileColorsModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        server: bool,
        author_id: int,
        primary_default: str = "",
        secondary_default: str = "",
    ) -> None:
        super().__init__(title="Advanced Hex Colors", timeout=900)
        self.server = bool(server)
        self.author_id = int(author_id)
        self.primary = discord.ui.TextInput(
            label="Exact primary hex",
            placeholder="#22DCFF",
            default=str(primary_default or "")[:7] or None,
            max_length=7,
        )
        self.secondary = discord.ui.TextInput(
            label="Exact secondary hex",
            placeholder="#BC42FF",
            default=str(secondary_default or "")[:7] or None,
            max_length=7,
        )
        self.add_item(self.primary)
        self.add_item(self.secondary)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.author_id:
            return await _private(interaction, content="❌ Only the person who opened this editor can submit it.")
        try:
            primary = normalize_hex_color(str(self.primary.value))
            secondary = normalize_hex_color(str(self.secondary.value))
        except ValueError as exc:
            return await _private(interaction, content=f"❌ {exc}")
        if self.server:
            await _save_server_style(
                interaction,
                {
                    SERVER_STYLE_CONFIG_KEYS["color_mode"]: "custom",
                    SERVER_STYLE_CONFIG_KEYS["custom_primary"]: primary,
                    SERVER_STYLE_CONFIG_KEYS["custom_secondary"]: secondary,
                },
                message="Advanced server hex colors saved.",
            )
        else:
            await _save_member_style(
                interaction,
                {
                    "signature_color_mode": "custom",
                    "signature_custom_primary": primary,
                    "signature_custom_secondary": secondary,
                },
                message="Your advanced hex colors were saved.",
            )
'''
if source.count(old_modal) != 1:
    raise SystemExit("profile studio custom color modal marker changed")
source = source.replace(old_modal, new_modal, 1)

old_button = '''    @discord.ui.button(label="Custom Colors", emoji="🖌️", style=discord.ButtonStyle.secondary, row=0)
    async def custom_colors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            CustomProfileColorsModal(server=self.server, author_id=self.author_id)
        )
'''
new_button = '''    @discord.ui.button(label="Mix Colors", emoji="🖌️", style=discord.ButtonStyle.secondary, row=0)
    async def custom_colors(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_color_mixer(
            interaction,
            server=self.server,
            author_id=self.author_id,
        )
'''
if source.count(old_button) != 1:
    raise SystemExit("profile studio custom color button marker changed")
source = source.replace(old_button, new_button, 1)
SOURCE_PATH.write_text(source, encoding="utf-8")

workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
old_workflow = "            tests/test_profile_signature_reference_visuals.py\n"
new_workflow = (
    "            tests/test_profile_signature_reference_visuals.py \\\n"
    "            tests/test_profile_signature_color_mixer.py\n"
)
if workflow.count(old_workflow) != 1:
    raise SystemExit("profile diagnostics test-list marker changed")
WORKFLOW_PATH.write_text(workflow.replace(old_workflow, new_workflow, 1), encoding="utf-8")

TEST_PATH.write_text(
    '''from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify import profile_signature_studio as studio


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "stoney_verify/profile_signature_studio.py").read_text(encoding="utf-8")


def test_appearance_menu_exposes_every_real_control() -> None:
    view = studio.ProfileAppearanceView(author_id=42)
    labels = {str(child.label) for child in view.children if isinstance(child, discord.ui.Button)}
    assert labels == {
        "Theme",
        "Font",
        "Colors",
        "Mix Colors",
        "Background",
        "Layout",
        "Avatar Frame",
        "Preview",
        "Back",
    }


def test_mix_colors_is_button_first_and_hex_is_advanced_only() -> None:
    block = SOURCE.split('label="Mix Colors"', 1)[1].split('label="Background"', 1)[0]
    assert "_open_color_mixer" in block
    assert "send_modal" not in block
    assert 'title="Advanced Hex Colors"' in SOURCE
    assert '"Advanced Hex Codes"' in SOURCE
    assert "Last-resort manual entry" in SOURCE


def test_visual_mixer_has_distinct_named_colors() -> None:
    choices = studio._COLOR_MIX_CHOICES
    assert len(choices) >= 12
    assert len({key for key, *_rest in choices}) == len(choices)
    assert len({value for _key, _label, value, _emoji, _description in choices}) == len(choices)
    assert all(value.startswith("#") and len(value) == 7 for _key, _label, value, _emoji, _description in choices)


def test_member_color_mix_uses_both_selected_colors() -> None:
    updates = studio._color_mix_updates("neon_green", "violet", server=False)
    assert updates == {
        "signature_color_mode": "custom",
        "signature_custom_primary": "#8FFF52",
        "signature_custom_secondary": "#B85BFF",
    }


def test_server_color_mix_uses_server_style_keys() -> None:
    updates = studio._color_mix_updates("gold", "graphite", server=True)
    assert updates[studio.SERVER_STYLE_CONFIG_KEYS["color_mode"]] == "custom"
    assert updates[studio.SERVER_STYLE_CONFIG_KEYS["custom_primary"]] == "#FFCC52"
    assert updates[studio.SERVER_STYLE_CONFIG_KEYS["custom_secondary"]] == "#7B8798"


def test_color_mix_save_path_always_previews_fresh_result() -> None:
    assert 'saver = _save_server_style if server else _save_member_style' in SOURCE
    assert "await saver(component, updates, message=message)" in SOURCE
    assert 'await _preview(interaction, member=member, notice=f"✅ {message}")' in SOURCE
''',
    encoding="utf-8",
)

print("Applied native PR #145 visual color mixer and regression coverage.")
