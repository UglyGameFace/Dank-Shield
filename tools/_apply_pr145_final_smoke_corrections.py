from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one exact match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{path}: expected one regex match, found {count}")
    path.write_text(updated, encoding="utf-8")


service = ROOT / "stoney_verify/profile_card_service.py"
replace_once(
    service,
    '''DEFAULT_PROFILE_PREFERENCES: dict[str, bool] = {
    "live_cards_enabled": True,
    # Actual server roles are opt-in because they may reveal staff/access structure.
    "show_server_roles": False,
    # Existing "show_roles" behavior represented member-selected profile tags.
    "show_profile_tags": True,
    "show_account_dates": True,
    "show_platforms": True,
}''',
    '''DEFAULT_PROFILE_PREFERENCES: dict[str, bool] = {
    # Signatures are opt-in. Missing/legacy rows remain off until the member enables them.
    "live_cards_enabled": False,
    # Actual server roles are opt-in because they may reveal staff/access structure.
    "show_server_roles": False,
    # Existing "show_roles" behavior represented member-selected profile tags.
    "show_profile_tags": True,
    "show_account_dates": True,
    "show_platforms": True,
    # The server icon/name panel is independently optional.
    "show_server_branding": True,
}''',
)

style = ROOT / "stoney_verify/profile_signature_style.py"
replace_once(
    style,
    'PROFILE_CUSTOM_BACKGROUND_KEY = "profile_signature_custom_background_b64"\n',
    'PROFILE_CUSTOM_BACKGROUND_KEY = "profile_signature_custom_background_b64"\n'
    'MEMBER_CUSTOM_BACKGROUND_KEY = "signature_custom_background_b64"\n',
)
replace_once(
    style,
    '''    "signature_custom_highlight": "",
    "signature_background_mode": PROFILE_BACKGROUND_INHERIT,
''',
    '''    "signature_custom_highlight": "",
    MEMBER_CUSTOM_BACKGROUND_KEY: "",
    "signature_background_mode": PROFILE_BACKGROUND_INHERIT,
''',
)
replace_once(
    style,
    '''        "signature_custom_highlight": _clean_hex(raw.get("signature_custom_highlight")),
        "signature_background_mode": _clean_choice(
''',
    '''        "signature_custom_highlight": _clean_hex(raw.get("signature_custom_highlight")),
        MEMBER_CUSTOM_BACKGROUND_KEY: str(raw.get(MEMBER_CUSTOM_BACKGROUND_KEY) or "")[:2_500_000],
        "signature_background_mode": _clean_choice(
''',
)
replace_once(
    style,
    '''        "custom_background": decode_profile_asset(_value(config, PROFILE_CUSTOM_BACKGROUND_KEY, "")),
''',
    '''        "custom_background": (
            decode_profile_asset(member.get(MEMBER_CUSTOM_BACKGROUND_KEY, ""))
            if member["signature_background_mode"] == "custom" and member.get(MEMBER_CUSTOM_BACKGROUND_KEY)
            else decode_profile_asset(_value(config, PROFILE_CUSTOM_BACKGROUND_KEY, ""))
        ),
''',
)
replace_regex(
    style,
    r'''def theme_style_updates\(theme_key: str, \*, member: bool\) -> dict\[str, str\]:
.*?
    return \{
        SERVER_STYLE_CONFIG_KEYS\["theme"\]: clean,
        SERVER_STYLE_CONFIG_KEYS\["color_mode"\]: "theme",
        SERVER_STYLE_CONFIG_KEYS\["background_mode"\]: "theme",
    \}
''',
    '''def theme_style_updates(theme_key: str, *, member: bool) -> dict[str, str]:
    # Theme selection changes only the visual family. Color/background controls are independent.
    clean = str(theme_key or "").strip().lower().replace("-", "_")
    if member and clean == PROFILE_THEME_INHERIT:
        return {"signature_theme": PROFILE_THEME_INHERIT}
    if clean not in PROFILE_THEME_KEYS:
        raise ValueError("That profile-signature theme is no longer available.")
    if member:
        return {"signature_theme": clean}
    return {SERVER_STYLE_CONFIG_KEYS["theme"]: clean}
''',
)
replace_once(
    style,
    '''    "PROFILE_CUSTOM_BACKGROUND_KEY",
''',
    '''    "PROFILE_CUSTOM_BACKGROUND_KEY",
    "MEMBER_CUSTOM_BACKGROUND_KEY",
''',
)

runtime = ROOT / "stoney_verify/profile_card_runtime.py"
replace_regex(
    runtime,
    r'''def _compact_server_role_labels\(member: discord\.Member, config: Mapping\[str, Any\]\) -> list\[str\]:
.*?

def _compact_profile_tag_labels''',
    '''def _compact_server_role_labels(member: discord.Member, config: Mapping[str, Any]) -> list[str]:
    # Return truthful complete server roles, with Discord owner status first.
    from .commands_ext.public_self_roles_group import _role_name_key, _short_role_label

    labels: list[str] = []
    guild = getattr(member, "guild", None)
    try:
        if guild is not None and int(getattr(guild, "owner_id", 0) or 0) == int(member.id):
            labels.append("Server Owner")
    except Exception:
        pass

    profile_name_keys = _profile_role_name_keys()
    cosmetic_ids = _configured_role_ids(config, "profile_cosmetic_role_ids")
    for role in sorted(list(getattr(member, "roles", []) or []), reverse=True):
        try:
            if role.is_default() or role.managed or int(role.id) in cosmetic_ids:
                continue
        except Exception:
            continue
        if _role_name_key(role.name) in profile_name_keys:
            continue
        label = _short_role_label(role.name)
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 4:
            break
    return labels


def _member_is_guild_owner(member: discord.Member) -> bool:
    try:
        return int(getattr(member.guild, "owner_id", 0) or 0) == int(member.id)
    except Exception:
        return False


def _compact_profile_tag_labels''',
)
replace_once(
    runtime,
    '''    if require_live_enabled and not bool(preferences.get("live_cards_enabled", True)):
        return None
''',
    '''    if require_live_enabled and not bool(preferences.get("live_cards_enabled", False)):
        return None
''',
)
replace_once(
    runtime,
    '''    show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
    platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)
''',
    '''    show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
    show_server_branding = bool(preferences.get("show_server_branding", True))
    platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)
''',
)
replace_once(
    runtime,
    '''    server_role_labels = _compact_server_role_labels(member, guild_config) if show_server_roles else []
''',
    '''    discovered_roles = _compact_server_role_labels(member, guild_config)
    server_role_labels = discovered_roles if show_server_roles else (
        ["Server Owner"] if _member_is_guild_owner(member) else []
    )
''',
)
replace_once(
    runtime,
    '''        tuple(date_labels),
        _stable_cache_value(platforms),
''',
    '''        tuple(date_labels),
        show_server_branding,
        _stable_cache_value(platforms),
''',
)
replace_once(
    runtime,
    '''            platform_entries=platforms,
        )
''',
    '''            platform_entries=platforms,
            show_server_branding=show_server_branding,
        )
''',
)

renderer = ROOT / "stoney_verify/profile_signature_live_renderer.py"
replace_once(renderer, "from dataclasses import dataclass\n", "from dataclasses import dataclass, replace\n")
replace_once(
    renderer,
    '''def _draw_rich(image: Image.Image, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, font: Any, fill: tuple[int, int, int, int], assets: Mapping[str, bytes], emoji_size: int) -> int:
''',
    '''def _complete_lines(
    draw: ImageDraw.ImageDraw,
    value: Any,
    *,
    max_width: int,
    max_lines: int,
    assets: Mapping[str, bytes],
    start_size: int,
    min_size: int,
    limit: int = 240,
) -> tuple[Any, list[str], int]:
    # Fit every character by shrinking/wrapping; never return ellipsis.
    clean = _safe(value, limit)
    if not clean:
        return None, [], min_size
    for size in range(start_size, min_size - 1, -1):
        font = _font(size, regular=True)
        remaining = clean
        lines: list[str] = []
        while remaining and len(lines) < max_lines:
            if _rich_width(draw, remaining, font, assets, size) <= max_width:
                lines.append(remaining)
                remaining = ""
                break
            low, high = 1, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if _rich_width(draw, remaining[:middle], font, assets, size) <= max_width:
                    low = middle
                else:
                    high = middle - 1
            split_at = max(1, low)
            space_at = remaining.rfind(" ", 0, split_at + 1)
            if space_at > 0:
                line = remaining[:space_at].rstrip()
                remaining = remaining[space_at + 1 :].lstrip()
            else:
                line = remaining[:split_at]
                remaining = remaining[split_at:]
            if not line:
                break
            lines.append(line)
        if lines and not remaining:
            return font, lines, size
    return None, [], min_size


def _draw_complete(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: Any,
    *,
    max_width: int,
    max_lines: int,
    assets: Mapping[str, bytes],
    start_size: int,
    min_size: int,
    fill: tuple[int, int, int, int],
    limit: int = 240,
    line_gap: int = 2,
) -> int:
    font, lines, size = _complete_lines(
        draw,
        value,
        max_width=max_width,
        max_lines=max_lines,
        assets=assets,
        start_size=start_size,
        min_size=min_size,
        limit=limit,
    )
    if font is None:
        return 0
    x, y = xy
    for index, line in enumerate(lines):
        _draw_rich(
            image,
            draw,
            (x, y + index * (size + line_gap)),
            line,
            font=font,
            fill=fill,
            assets=assets,
            emoji_size=size,
        )
    return len(lines) * size + max(0, len(lines) - 1) * line_gap


def _draw_rich(image: Image.Image, draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, font: Any, fill: tuple[int, int, int, int], assets: Mapping[str, bytes], emoji_size: int) -> int:
''',
)
replace_regex(
    renderer,
    r'''def _draw_pills\(image: Image\.Image, labels: Sequence\[tuple\[str, tuple\[int, int, int\]\]\], palette: ProfilePalette, spec: Layout, assets: Mapping\[str, bytes\]\) -> None:
.*?

def _role_icon''',
    '''def _draw_pills(image: Image.Image, labels: Sequence[tuple[str, tuple[int, int, int]]], palette: ProfilePalette, spec: Layout, assets: Mapping[str, bytes]) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    x, y = spec.content_x, spec.tags_y
    row = 0
    for raw, accent in labels:
        clean = _safe(raw, 110)
        if not clean:
            continue
        row_width = spec.content_right - spec.content_x
        font = None
        size = 17
        width = 0
        for candidate in range(17, 10, -1):
            trial = _font(candidate, regular=True)
            trial_width = _rich_width(draw, clean, trial, assets, candidate) + 28
            if trial_width <= row_width:
                font, size, width = trial, candidate, trial_width
                break
        if font is None:
            continue
        if x + width > spec.content_right and x > spec.content_x:
            row += 1
            if row >= 2:
                break
            x, y = spec.content_x, y + 39
        if x + width > spec.content_right:
            continue
        fill = _mix((3, 7, 10), accent, 0.12)
        draw.rounded_rectangle((x, y, x + width, y + 32), radius=14, fill=fill + (224,), outline=accent + (185,), width=2)
        _draw_rich(image, draw, (x + 14, y + max(3, (32 - size) // 2 - 1)), clean, font=font, fill=palette.text + (248,), assets=assets, emoji_size=size)
        x += width + 8


def _role_icon''',
)
replace_once(
    renderer,
    '''    role_font = _font(17, regular=True)
    fitted_role = _fit(draw, role.upper(), role_font, 245, assets, 18, 60) or "MEMBER"
    role_width = min(278, _rich_width(draw, fitted_role, role_font, assets, 18) + 58)
''',
    '''    role_max = max(150, spec.brand_x - spec.platform_x - 24)
    role_font, role_lines, role_size = _complete_lines(
        draw,
        role.upper() or "MEMBER",
        max_width=role_max - 58,
        max_lines=2,
        assets=assets,
        start_size=17,
        min_size=10,
        limit=100,
    )
    if role_font is None:
        role_font, role_lines, role_size = _font(15, regular=True), ["MEMBER"], 15
    role_width = min(role_max, max(_rich_width(draw, line, role_font, assets, role_size) for line in role_lines) + 58)
''',
)
replace_once(
    renderer,
    '''        (spec.platform_x, 37, spec.platform_x + role_width, 75),
        radius=16,
''',
    '''        (spec.platform_x, 32, spec.platform_x + role_width, 82),
        radius=18,
''',
)
replace_once(
    renderer,
    '''    _role_icon(draw, spec.platform_x + 13, 45, palette, theme_key)
    _draw_rich(
        image,
        draw,
        (spec.platform_x + 43, 46),
        fitted_role,
        font=role_font,
        fill=palette.highlight + (255,),
        assets=assets,
        emoji_size=18,
    )
''',
    '''    _role_icon(draw, spec.platform_x + 13, 46, palette, theme_key)
    role_y = 40 if len(role_lines) == 1 else 35
    for index, line in enumerate(role_lines):
        _draw_rich(
            image,
            draw,
            (spec.platform_x + 43, role_y + index * (role_size + 1)),
            line,
            font=role_font,
            fill=palette.highlight + (255,),
            assets=assets,
            emoji_size=role_size,
        )
''',
)
replace_once(
    renderer,
    '''        username = _safe(focus_entry.get("username"), 40)
        username_font = _font(18, regular=True)
        username_text = username if username and platform_entry_mode(focus_entry) != "logo" else "Platform-focused style"
        fitted = _fit(draw, username_text, username_font, 190, assets, 18, 60)
        _draw_rich(
            image,
            draw,
            (x + size + 16, y + 34),
            fitted,
            font=username_font,
            fill=palette.highlight + (250,),
            assets=assets,
            emoji_size=18,
        )
''',
    '''        username = _safe(focus_entry.get("username"), 80)
        username_text = username if username and platform_entry_mode(focus_entry) != "logo" else "Platform-focused style"
        _draw_complete(
            image,
            draw,
            (x + size + 16, y + 31),
            username_text,
            max_width=max(100, spec.brand_x - (x + size + 16) - 20),
            max_lines=2,
            assets=assets,
            start_size=18,
            min_size=10,
            fill=palette.highlight + (250,),
            limit=100,
        )
''',
)
replace_once(renderer, '        username = _safe(entry.get("username"), 34)\n', '        username = _safe(entry.get("username"), 80)\n')
replace_once(
    renderer,
    '''    if shared:
        draw = ImageDraw.Draw(image, "RGBA")
        font = _font(17, regular=True)
        line = _fit(draw, "   ".join(shared[:2]), font, 304, assets, 18, 150)
        _draw_rich(
            image,
            draw,
            (spec.platform_x, 168),
            line,
            font=font,
            fill=palette.tertiary + (255,),
            assets=assets,
            emoji_size=18,
        )
''',
    '''    if shared:
        draw = ImageDraw.Draw(image, "RGBA")
        line_y = 164
        max_width = max(120, spec.brand_x - spec.platform_x - 24)
        for value in shared[:2]:
            used = _draw_complete(
                image,
                draw,
                (spec.platform_x, line_y),
                value,
                max_width=max_width,
                max_lines=2,
                assets=assets,
                start_size=16,
                min_size=10,
                fill=palette.tertiary + (255,),
                limit=120,
            )
            if used:
                line_y += used + 5
''',
)
replace_once(
    renderer,
    '''    font = _font(18, regular=True)
    label = _fit(draw, server_name, font, 160, assets, 18, 70)
    width = _rich_width(draw, label, font, assets, 18)
    _draw_rich(image, draw, (icon_x + max(-24, (size - width) // 2), 184), label, font=font, fill=palette.text + (248,), assets=assets, emoji_size=18)
''',
    '''    font, lines, brand_size = _complete_lines(
        draw,
        server_name,
        max_width=176,
        max_lines=2,
        assets=assets,
        start_size=18,
        min_size=11,
        limit=100,
    )
    if font is not None:
        for index, line in enumerate(lines):
            width = _rich_width(draw, line, font, assets, brand_size)
            _draw_rich(
                image,
                draw,
                (icon_x + (size - width) // 2, 182 + index * (brand_size + 2)),
                line,
                font=font,
                fill=palette.text + (248,),
                assets=assets,
                emoji_size=brand_size,
            )
''',
)
replace_once(
    renderer,
    '''    guild_icon_bytes: bytes = b"",
    emoji_assets: Optional[Mapping[str, bytes]] = None,
) -> bytes:
''',
    '''    guild_icon_bytes: bytes = b"",
    emoji_assets: Optional[Mapping[str, bytes]] = None,
    show_server_branding: bool = True,
) -> bytes:
''',
)
replace_once(
    renderer,
    '    spec = _LAYOUTS.get(layout_key, _LAYOUTS["classic"])\n',
    '    spec = _LAYOUTS.get(layout_key, _LAYOUTS["classic"])\n'
    '    if not show_server_branding:\n'
    '        spec = replace(spec, brand_x=1370)\n',
)
replace_once(
    renderer,
    '    eyebrow = _fit(draw, server_name.upper(), eyebrow_font, spec.content_right - spec.content_x, assets, 18, 70)\n',
    '    eyebrow_value = server_name.upper() if show_server_branding else "MEMBER PROFILE"\n'
    '    eyebrow = _fit(draw, eyebrow_value, eyebrow_font, spec.content_right - spec.content_x, assets, 18, 70)\n',
)
replace_once(
    renderer,
    '    _draw_brand(image, server_name, guild_icon_bytes, palette, spec, assets, theme_key)\n',
    '    if show_server_branding:\n'
    '        _draw_brand(image, server_name, guild_icon_bytes, palette, spec, assets, theme_key)\n',
)
replace_once(
    renderer,
    '''    platform_entries: Sequence[Mapping[str, Any]] = (),
) -> bytes:
''',
    '''    platform_entries: Sequence[Mapping[str, Any]] = (),
    show_server_branding: bool = True,
) -> bytes:
''',
)
replace_once(
    renderer,
    '''        emoji_assets=emojis,
    )
''',
    '''        emoji_assets=emojis,
        show_server_branding=show_server_branding,
    )
''',
)

studio = ROOT / "stoney_verify/profile_signature_studio.py"
replace_once(
    studio,
    '''    DEFAULT_MEMBER_PROFILE_STYLE,
    DEFAULT_SERVER_PROFILE_STYLE,
''',
    '''    DEFAULT_MEMBER_PROFILE_STYLE,
    DEFAULT_SERVER_PROFILE_STYLE,
    MEMBER_CUSTOM_BACKGROUND_KEY,
''',
)
replace_once(
    studio,
    '''from .guild_config import get_guild_config, upsert_guild_config
''',
    '''from .guild_config import get_guild_config, upsert_guild_config
from .profile_custom_background import profile_background_guide, profile_background_requirements
''',
)
replace_once(
    studio,
    '''            f"**Live signature:** {'On' if effective_privacy.get('live_cards_enabled', True) else 'Off'}\n"
            f"**Server roles:** {'Shown' if effective_privacy.get('show_server_roles', False) else 'Hidden'}\n"
''',
    '''            f"**Live signature:** {'On' if effective_privacy.get('live_cards_enabled', False) else 'Off'}\n"
            f"**Server roles:** {'Shown' if effective_privacy.get('show_server_roles', False) else 'Hidden'}\n"
            f"**Server branding:** {'Shown' if effective_privacy.get('show_server_branding', True) else 'Hidden'}\n"
''',
)
replace_once(
    studio,
    'message=f"Server profile-signature theme set to **{PROFILE_THEME_SPECS[value].label}** with its colors and background.",',
    'message=f"Server profile-signature theme set to **{PROFILE_THEME_SPECS[value].label}**. Existing custom colors and artwork were preserved.",',
)
replace_once(
    studio,
    'message=f"Your signature theme is now **{label}** with its colors and background.",',
    'message=f"Your signature theme is now **{label}**. Existing custom colors and artwork were preserved.",',
)
replace_once(
    studio,
    'description="Apply this theme\'s colors, background, and artwork",',
    'description="Change the visual family while preserving custom colors and artwork",',
)
replace_once(
    studio,
    'content="## 🖼️ Signature Themes\\nPick a complete look. Its colors, background, and artwork apply immediately; you can override individual parts afterward.",',
    'content="## 🖼️ Signature Themes\\nPick the visual family. Custom colors and custom artwork stay active. Use **Selected Theme** under Colors or **Theme Artwork** under Background only when you want those parts reset to the theme.",',
)
replace_once(
    studio,
    '("Server Custom Artwork", "custom", "Use the server\'s uploaded profile artwork when available.", "📎"),',
    '("Custom Artwork", "custom", "Use your personal upload, or the server upload when no personal image exists.", "📎"),',
)
replace_once(
    studio,
    '''    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.success, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _preview(interaction)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
''',
    '''    @discord.ui.button(label="Custom Art Guide", emoji="📎", style=discord.ButtonStyle.secondary, row=2)
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
''',
)
studio_text = studio.read_text(encoding="utf-8")
studio_text = studio_text.replace('.get("live_cards_enabled", True)', '.get("live_cards_enabled", False)')
studio.write_text(studio_text, encoding="utf-8")

public = ROOT / "stoney_verify/commands_ext/public_profile_cards.py"
replace_once(
    public,
    '''class ProfileSettingsView(_core.ProfileSettingsView):
''',
    '''async def profile_background_upload(
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


class ProfileSettingsView(_core.ProfileSettingsView):
''',
)
replace_once(
    public,
    '''            ("Accounts", "show_platforms", "🔗"),
''',
    '''            ("Accounts", "show_platforms", "🔗"),
            ("Server Branding", "show_server_branding", "🏰"),
''',
)
replace_once(
    public,
    '''        ("settings", "Open your private profile privacy and platform settings.", profile_settings),
''',
    '''        ("settings", "Open your private profile privacy and platform settings.", profile_settings),
        ("background-upload", "Upload personal or server-default profile background artwork.", profile_background_upload),
        ("background-clear", "Remove personal or server-default profile background artwork.", profile_background_clear),
''',
)
public_text = public.read_text(encoding="utf-8")
public_text = public_text.replace('.get("live_cards_enabled", True)', '.get("live_cards_enabled", False)')
public.write_text(public_text, encoding="utf-8")

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

workflow = ROOT / ".github/workflows/profile-runtime-diagnostics.yml"
replace_once(
    workflow,
    '''            tests/test_profile_signature_color_mixer.py \\
            2>&1 | tee profile-focused-tests.log
''',
    '''            tests/test_profile_signature_color_mixer.py \\
            tests/test_profile_signature_owner_customization.py \\
            2>&1 | tee profile-focused-tests.log
''',
)

print("Applied PR #145 final deployed-smoke corrections.")
