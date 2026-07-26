from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, found {count}; expected={old!r}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


Path("ACTIVE_TASK.md").write_text(
    """# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-007 — Restore live signatures and make every editor control real

**Status:** ROOT CAUSE CONFIRMED / IMPLEMENTATION VALIDATION REQUIRED
**Branch:** `fix/profile-signature-runtime-editor`
**PR:** pending
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until configured live-signature channels post successfully and Theme, Font, Colors, Background, Layout, and Avatar Frame each produce a visibly distinct saved preview.

## Live findings

- A normal member message in the configured text channel produced no Dank Shield signature.
- The runtime always supplied `view=None` when the member had no clickable platform links. Discord rejects explicit null view payloads on this send path.
- The send exception was swallowed, so the channel looked ignored and the logs did not identify the failure.
- A card was suppressed entirely when every optional field was hidden, despite the renderer already supporting a basic avatar/name signature.
- Compact font rendering used only the broad font family and ignored the advertised effect, tracking, shear, uppercase, outline, chrome, pixel, and glow settings.
- The fallback channel command did not verify Attach Files even though compact signatures are image attachments.

## Scope

- Omit absent Discord payload fields instead of passing explicit `None` values.
- Log live-card send/state failures with guild, channel, and user context.
- Always allow a basic avatar/name signature when live cards are enabled; privacy only removes optional details.
- Render the real typography effects used by every advertised font style.
- Make avatar/profile/custom backgrounds visibly distinguishable behind the content panel.
- Validate Attach Files on every setup path and improve setup guidance.
- Add dynamic regression coverage for live posting and every appearance control.

## Validation

- [ ] Strict null-view live-send regression passes.
- [ ] Basic private signature renders with zero optional fields.
- [ ] All built-in font styles produce distinct compact output.
- [ ] Theme, colors, background, layout, and frame output tests pass.
- [ ] Changed Python modules compile.
- [ ] Full unit suite and repository audits pass.
- [ ] Branch is conflict-free with current `main`.
- [ ] Deployed Discord smoke posts a live card and visibly changes at least two fonts plus two other appearance controls.

## Cleanup

- Temporary materialization workflow/script removed before final validation.
- No monkey patch, compatibility fork, duplicate renderer, or temporary runtime path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
""",
    encoding="utf-8",
)

# Reuse the proven welcome typography effects through one public, bounded helper.
typography = Path("stoney_verify/welcome_card_typography_engine.py")
replace_once(
    typography,
    '''def _draw_sparkle(
''',
    '''def render_styled_text_tile(
    text: Any,
    *,
    style_key: Any,
    start_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    primary: tuple[int, int, int],
    secondary: tuple[int, int, int],
    role: str = "name",
    custom_font_bytes: Optional[bytes] = None,
) -> Image.Image:
    """Render one fitted typography tile using the canonical style effects."""
    style = _render_style(style_key, custom_font_bytes)
    _rendered, tile = _fitted_tile(
        str(text or ""),
        style=style,
        start_size=start_size,
        min_size=min_size,
        max_width=max_width,
        max_height=max_height,
        role=role,
        primary=primary,
        secondary=secondary,
        custom_font_bytes=custom_font_bytes,
    )
    return tile


def _draw_sparkle(
''',
    label="public styled text helper",
)
replace_once(
    typography,
    '''    "render_font_catalog",
    "render_welcome_card",
''',
    '''    "render_font_catalog",
    "render_styled_text_tile",
    "render_welcome_card",
''',
    label="typography export",
)

renderer = Path("stoney_verify/profile_signature_renderer.py")
replace_once(
    renderer,
    '''from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    parse_hex_color,
)''',
    '''from .welcome_card_typography_engine import (
    BUILTIN_THEMES,
    CUSTOM_FONT_STYLE_KEY,
    FONT_STYLES,
    parse_hex_color,
    render_styled_text_tile,
)''',
    label="renderer typography import",
)
replace_once(
    renderer,
    '''_REGULAR_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
)''',
    '''_REGULAR_FONT_FAMILIES: dict[str, tuple[str, ...]] = {
    "sans": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
    ),
    "mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ),
    "serif": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    ),
}''',
    label="regular font families",
)
replace_once(
    renderer,
    '''    candidates = _REGULAR_FONTS if regular else _FONT_FAMILIES.get(style.family, _FONT_FAMILIES["sans"])''',
    '''    candidates = (
        _REGULAR_FONT_FAMILIES.get(style.family, _REGULAR_FONT_FAMILIES["sans"])
        if regular
        else _FONT_FAMILIES.get(style.family, _FONT_FAMILIES["sans"])
    )''',
    label="family-aware regular fonts",
)
replace_once(
    renderer,
    '''    panel = (18, 18, SIGNATURE_WIDTH - 18, SIGNATURE_HEIGHT - 18)
    panel_alpha = 205 if layout == "minimal" else 218
    draw.rounded_rectangle(panel, radius=28, fill=tuple(theme.panel) + (panel_alpha,), outline=primary + (125,), width=2)''',
    '''    panel = (18, 18, SIGNATURE_WIDTH - 18, SIGNATURE_HEIGHT - 18)
    background_mode = str(style.get("background_mode") or "theme")
    if background_mode in {"profile", "custom"}:
        panel_alpha = 168 if layout == "minimal" else 178
    else:
        panel_alpha = 198 if layout == "minimal" else 208
    draw.rounded_rectangle(panel, radius=28, fill=tuple(theme.panel) + (panel_alpha,), outline=primary + (125,), width=2)''',
    label="visible background modes",
)
replace_once(
    renderer,
    '''    name_font = _fit_font(
        draw,
        name_text,
        style_key=style_key,
        custom_font=custom_font,
        max_width=max(300, right_edge - content_x),
        start=metrics["name_start"],
        minimum=metrics["name_min"],
    )
    chip_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = f"MEMBER SIGNATURE  •  {_safe_text(server_name, 48).upper()}"
    draw.text((content_x, metrics["eyebrow_y"]), eyebrow, font=eyebrow_font, fill=primary + (255,))
    draw.text(
        (content_x, metrics["name_y"]),
        name_text,
        font=name_font,
        fill=text + (255,),
        stroke_width=1,
        stroke_fill=(0, 0, 0, 170),
    )''',
    '''    name_tile = render_styled_text_tile(
        name_text,
        style_key=style_key,
        start_size=metrics["name_start"],
        min_size=metrics["name_min"],
        max_width=max(300, right_edge - content_x),
        max_height=max(54, metrics["chips_y"] - metrics["name_y"] - 2),
        primary=primary,
        secondary=secondary,
        role="name",
        custom_font_bytes=custom_font,
    )
    chip_font = _font(15, style_key=style_key, custom_font=custom_font, regular=True)

    eyebrow = f"MEMBER SIGNATURE  •  {_safe_text(server_name, 48).upper()}"
    draw.text((content_x, metrics["eyebrow_y"]), eyebrow, font=eyebrow_font, fill=primary + (255,))
    image.alpha_composite(name_tile, (content_x, metrics["name_y"] - 5))
    draw = ImageDraw.Draw(image, "RGBA")''',
    label="real compact typography effects",
)

runtime = Path("stoney_verify/profile_card_runtime.py")
replace_once(
    runtime,
    '''    if not show_roles and not show_dates and not platforms:
        return None

    try:''',
    '''    # A member may hide every optional field and still keep a basic
    # avatar/name signature. Privacy removes chips; it does not disable the card.

    try:''',
    label="basic signature rendering",
)
replace_once(
    runtime,
    '''class LiveProfileCardRuntime(_core.LiveProfileCardRuntime):''',
    '''def _live_card_send_payload(rendered: LiveCardRender) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "embed": rendered.embed,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if rendered.view is not None:
        payload["view"] = rendered.view
    if rendered.file is not None:
        payload["file"] = rendered.file
    return payload


class LiveProfileCardRuntime(_core.LiveProfileCardRuntime):''',
    label="live card payload helper",
)
replace_once(
    runtime,
    '''        new_message: Optional[discord.Message] = None
        try:
            payload: dict[str, Any] = {
                "embed": rendered.embed,
                "view": rendered.view,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if rendered.file is not None:
                payload["file"] = rendered.file
            new_message = await channel.send(**payload)
            await upsert_live_card_state(
                trigger.guild_id,
                trigger.channel_id,
                message_id=new_message.id,
                user_id=trigger.user_id,
                trigger_message_id=trigger.message_id,
            )
        except Exception:
            if new_message is not None:
                await self._delete_verified_card(new_message)
            return''',
    '''        new_message: Optional[discord.Message] = None
        try:
            new_message = await channel.send(**_live_card_send_payload(rendered))
        except Exception as exc:
            print(
                "⚠️ live_profile_card send failed "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return

        try:
            await upsert_live_card_state(
                trigger.guild_id,
                trigger.channel_id,
                message_id=new_message.id,
                user_id=trigger.user_id,
                trigger_message_id=trigger.message_id,
            )
        except Exception as exc:
            await self._delete_verified_card(new_message)
            print(
                "⚠️ live_profile_card state write failed; removed new card "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return''',
    label="safe live send and diagnostics",
)

runtime_core = Path("stoney_verify/profile_card_runtime_core.py")
replace_once(
    runtime_core,
    '''    if not show_roles and not show_dates and not platforms:
        return None

    # Import lazily so the existing profile command module remains the sole base renderer.''',
    '''    # Optional privacy settings remove fields, not the basic member card.

    # Import lazily so the existing profile command module remains the sole base renderer.''',
    label="legacy basic signature rendering",
)

profile_core = Path("stoney_verify/commands_ext/public_profile_cards_core.py")
replace_once(
    profile_core,
    '''async def _send_private(
    interaction: discord.Interaction,
    **kwargs: Any,
) -> None:
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())''',
    '''async def _send_private(
    interaction: discord.Interaction,
    **kwargs: Any,
) -> None:
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())''',
    label="profile private payload sanitization",
)
replace_once(
    profile_core,
    '''        if not permissions.read_message_history:
            missing.append("Read Message History")
        if missing:''',
    '''        if not permissions.read_message_history:
            missing.append("Read Message History")
        if not permissions.attach_files:
            missing.append("Attach Files")
        if missing:''',
    label="fallback attach files permission",
)
replace_once(
    profile_core,
    '''        value=", ".join(sorted(live.allowed_fields)) if live.allowed_fields else "None",''',
    '''        value=(
            ", ".join(sorted(live.allowed_fields))
            if live.allowed_fields
            else "None • basic avatar/name signatures still post"
        ),''',
    label="live status basic card guidance",
)

setup_ui = Path("stoney_verify/profile_card_setup_ui.py")
replace_once(
    setup_ui,
    '''        value=", ".join(fields) if fields else "No optional details",''',
    '''        value=", ".join(fields) if fields else "No optional details • basic avatar/name still posts",''',
    label="setup optional details guidance",
)
replace_once(
    setup_ui,
    '''        payload: dict[str, Any] = {
            "embed": rendered.embed,
            "view": rendered.view,
            "allowed_mentions": discord.AllowedMentions.none(),
            "attachments": [rendered.file] if rendered.file is not None else [],
        }
        await interaction.edit_original_response(**payload)''',
    '''        payload: dict[str, Any] = {
            "embed": rendered.embed,
            "allowed_mentions": discord.AllowedMentions.none(),
            "attachments": [rendered.file] if rendered.file is not None else [],
        }
        if rendered.view is not None:
            payload["view"] = rendered.view
        await interaction.edit_original_response(**payload)''',
    label="setup preview optional view",
)

setup_core = Path("stoney_verify/profile_card_setup_ui_core.py")
replace_once(
    setup_core,
    '''        value=", ".join(fields) if fields else "No optional fields",''',
    '''        value=", ".join(fields) if fields else "No optional fields • basic avatar/name still posts",''',
    label="legacy setup optional guidance",
)
replace_once(
    setup_core,
    '''            notice=f"Enabled live profile cards in {len(selected)} channel(s).",''',
    '''            notice=(
                f"Enabled live profile cards in {len(selected)} channel(s). "
                "Send a normal member message there; its signature should appear after a few seconds."
            ),''',
    label="channel save smoke guidance",
)
replace_once(
    setup_core,
    '''            notice=f"{_FIELD_LABELS[self.field_key]} are now {action}. Member privacy can still hide them.",''',
    '''            notice=(
                f"{_FIELD_LABELS[self.field_key]} are now {action}. Member privacy can still hide them. "
                "A basic avatar/name signature remains available even when every optional field is hidden."
            ),''',
    label="field toggle basic guidance",
)
replace_once(
    setup_core,
    '''        await interaction.edit_original_response(
            embed=rendered.embed,
            view=rendered.view,
            allowed_mentions=discord.AllowedMentions.none(),
        )''',
    '''        payload: dict[str, Any] = {
            "embed": rendered.embed,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if rendered.view is not None:
            payload["view"] = rendered.view
        rendered_file = getattr(rendered, "file", None)
        if rendered_file is not None:
            payload["attachments"] = [rendered_file]
        await interaction.edit_original_response(**payload)''',
    label="legacy setup preview payload",
)

# Strengthen the existing runtime fake so explicit null views fail like Discord.
runtime_tests = Path("tests/test_live_profile_card_runtime.py")
replace_once(
    runtime_tests,
    '''import asyncio
from datetime import datetime, timezone''',
    '''import asyncio
from io import BytesIO
from datetime import datetime, timezone''',
    label="runtime test BytesIO import",
)
replace_once(
    runtime_tests,
    '''        self.fetch_messages = {}
        self.fail_send = False''',
    '''        self.fetch_messages = {}
        self.fail_send = False
        self.reject_none_view = False
        self.sent_payloads = []''',
    label="strict fake channel state",
)
replace_once(
    runtime_tests,
    '''    async def send(self, *, embed, view, allowed_mentions):
        if self.fail_send:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="send failed"), "send failed")
        message = FakeSentMessage(1000 + len(self.sent), self.guild.bot_user, [embed])
        self.sent.append(message)
        self.fetch_messages[message.id] = message
        return message''',
    '''    async def send(self, **payload):
        if self.fail_send:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="send failed"), "send failed")
        if self.reject_none_view and "view" in payload and payload["view"] is None:
            raise TypeError("expected view parameter to be of type View or LayoutView, not NoneType")
        self.sent_payloads.append(dict(payload))
        message = FakeSentMessage(1000 + len(self.sent), self.guild.bot_user, [payload["embed"]])
        self.sent.append(message)
        self.fetch_messages[message.id] = message
        return message''',
    label="strict fake channel send",
)
runtime_tests.write_text(
    runtime_tests.read_text(encoding="utf-8")
    + '''


def test_live_send_omits_none_view_and_keeps_attachment(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(81, bot.user)
        channel = guild.add_channel(810)
        channel.reject_none_view = True
        member = guild.add_member(811)
        states = []

        async def get_state(_guild_id, _channel_id):
            return None

        async def save_state(guild_id, channel_id, **payload):
            states.append((guild_id, channel_id, payload))

        async def renderer(member, allowed, *, trigger_message_id, require_live_enabled=True):
            embed = discord.Embed(title=f"Profile {member.id}")
            embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
            return LiveCardRender(
                embed=embed,
                view=None,
                file=discord.File(BytesIO(b"image-bytes"), filename="profile.png"),
            )

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        monkeypatch.setattr(runtime_module, "upsert_live_card_state", save_state)
        runtime = LiveProfileCardRuntime(bot, renderer=renderer, sleep=asyncio.sleep)
        trigger = PendingTrigger(guild.id, channel.id, member.id, 91)
        await runtime._replace_card(
            FakeIncomingMessage(91, guild, channel, member),
            parse_live_card_config(_config(channel.id)),
            trigger,
        )

        assert len(channel.sent) == 1
        assert "view" not in channel.sent_payloads[0]
        assert channel.sent_payloads[0]["file"].filename == "profile.png"
        assert states and states[0][2]["user_id"] == member.id

    asyncio.run(scenario())


def test_basic_signature_renders_when_every_optional_field_is_hidden(monkeypatch):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(82, bot.user)
        member = guild.add_member(821)
        seen = []

        async def settings(_guild_id, _user_id):
            return {
                "preferences": {
                    "live_cards_enabled": True,
                    "show_roles": False,
                    "show_account_dates": False,
                    "show_platforms": False,
                },
                "platforms": {},
            }

        async def config(_guild_id):
            return {}

        async def render_image(_member, *, style, role_labels, date_labels, platform_labels):
            seen.append((style, role_labels, date_labels, platform_labels))
            return b"image-bytes"

        monkeypatch.setattr(runtime_module, "get_effective_profile_settings", settings)
        monkeypatch.setattr(runtime_module, "get_guild_config", config)
        monkeypatch.setattr(runtime_module, "render_member_profile_signature", render_image)
        rendered = await runtime_module.render_live_profile_card(
            member,
            set(),
            trigger_message_id=92,
            require_live_enabled=False,
        )

        assert rendered is not None
        assert rendered.file is not None
        assert seen and seen[0][1:] == ([], [], [])

    asyncio.run(scenario())


def test_live_send_failure_is_visible_in_logs(monkeypatch, capsys):
    async def scenario():
        _patch_discord_types(monkeypatch)
        bot = FakeBot()
        guild = FakeGuild(83, bot.user)
        channel = guild.add_channel(830)
        channel.fail_send = True
        member = guild.add_member(831)

        async def get_state(_guild_id, _channel_id):
            return None

        monkeypatch.setattr(runtime_module, "get_live_card_state", get_state)
        runtime = LiveProfileCardRuntime(bot, renderer=_fake_renderer([]), sleep=asyncio.sleep)
        trigger = PendingTrigger(guild.id, channel.id, member.id, 93)
        await runtime._replace_card(
            FakeIncomingMessage(93, guild, channel, member),
            parse_live_card_config(_config(channel.id)),
            trigger,
        )

    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert "live_profile_card send failed" in output
    assert "guild=83" in output
    assert "channel=830" in output
''',
    encoding="utf-8",
)

Path("tests/test_profile_signature_appearance_controls.py").write_text(
    '''from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image

from stoney_verify.profile_signature_renderer import render_profile_signature
from stoney_verify.welcome_card_typography_engine import FONT_STYLES


def _png_bytes(left=(235, 30, 70), right=(20, 90, 240)) -> bytes:
    image = Image.new("RGB", (160, 160), left)
    for x in range(80, 160):
        for y in range(160):
            image.putpixel((x, y), right)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


AVATAR = _png_bytes()
CUSTOM_BACKGROUND = _png_bytes((240, 170, 20), (15, 190, 130))


def _render(**updates) -> bytes:
    style = {
        "theme": "420_lobby",
        "font": "clean",
        "color_mode": "theme",
        "background_mode": "theme",
        "layout": "classic",
        "avatar_frame": "glow",
    }
    style.update(updates)
    return render_profile_signature(
        avatar_bytes=AVATAR,
        display_name="UglyGameFace",
        server_name="The 420 Lobby",
        role_labels=["Pronouns: he/him", "Interests: gaming • music"],
        date_labels=["Joined Jun 2026"],
        platform_labels=["Xbox: UglyGameFace"],
        style=style,
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_every_advertised_font_style_changes_compact_output():
    rendered = {_digest(_render(font=key)) for key in FONT_STYLES}
    assert len(rendered) == len(FONT_STYLES)


def test_layout_controls_are_visibly_distinct():
    rendered = {_digest(_render(layout=key)) for key in ("classic", "minimal", "spotlight")}
    assert len(rendered) == 3


def test_avatar_frame_controls_are_visibly_distinct():
    rendered = {_digest(_render(avatar_frame=key)) for key in ("glow", "ring", "none")}
    assert len(rendered) == 3


def test_background_controls_are_visibly_distinct():
    rendered = {
        _digest(_render(background_mode="theme")),
        _digest(_render(background_mode="profile")),
        _digest(_render(background_mode="custom", custom_background=CUSTOM_BACKGROUND)),
    }
    assert len(rendered) == 3


def test_color_controls_are_visibly_distinct():
    rendered = {
        _digest(_render(color_mode="theme")),
        _digest(_render(color_mode="profile")),
        _digest(
            _render(
                color_mode="custom",
                custom_primary="#5AFF2D",
                custom_secondary="#AE4BFF",
            )
        ),
    }
    assert len(rendered) == 3
''',
    encoding="utf-8",
)

followup_tests = Path("tests/test_ui_private_followup_payloads.py")
replace_once(
    followup_tests,
    '''from stoney_verify.commands_ext import public_command_hub
from stoney_verify import welcome_setup_ui''',
    '''from stoney_verify.commands_ext import public_command_hub, public_profile_cards_core
from stoney_verify import welcome_setup_ui''',
    label="profile card sender test import",
)
replace_once(
    followup_tests,
    '''    for sender in (public_command_hub._private, welcome_setup_ui._send_private):''',
    '''    for sender in (
        public_command_hub._private,
        public_profile_cards_core._send_private,
        welcome_setup_ui._send_private,
    ):''',
    label="profile card sender null test",
)

Path("tests/test_profile_signature_runtime_editor_static.py").write_text(
    '''from pathlib import Path


def test_profile_channel_fallback_requires_image_attachment_permission():
    source = Path("stoney_verify/commands_ext/public_profile_cards_core.py").read_text(encoding="utf-8")
    assert 'missing.append("Attach Files")' in source


def test_live_runtime_does_not_send_explicit_null_view():
    source = Path("stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
    assert 'if rendered.view is not None:' in source
    assert 'payload["view"] = rendered.view' in source
    assert '"view": rendered.view' not in source


def test_live_runtime_logs_send_failures_instead_of_swallowing_them():
    source = Path("stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
    assert "live_profile_card send failed" in source
    assert "live_profile_card state write failed" in source
''',
    encoding="utf-8",
)

print("materialized profile signature runtime/editor correction")
