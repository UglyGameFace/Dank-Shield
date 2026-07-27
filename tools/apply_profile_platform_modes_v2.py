from __future__ import annotations

from pathlib import Path
import importlib.util
import re


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "tools" / "apply_profile_platform_modes_patch.py"
_spec = importlib.util.spec_from_file_location("legacy_platform_patch", LEGACY_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Could not load legacy platform patch from {LEGACY_PATH}")
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


def apply_legacy_modes() -> None:
    legacy.patch_service()
    legacy.patch_runtime_core()
    legacy.patch_runtime()
    legacy.patch_studio()


def patch_runtime_controls() -> None:
    path = "stoney_verify/profile_card_runtime_core.py"
    text = read(path)
    replacement = '''def _platform_view(
    entries: list[dict[str, Any]],
    *,
    owner_user_id: Optional[int] = None,
) -> Optional[discord.ui.View]:
    """Build fast text controls; real brand marks are rendered inside the card."""
    view = discord.ui.View(timeout=None)
    for index, entry in enumerate(entries[:20]):
        platform = str(entry.get("platform") or "")
        spec = PLATFORM_SPECS.get(platform)
        if spec is None:
            continue
        mode = platform_entry_mode(entry)
        row = min(3, index // 5)
        url = str(entry.get("url") or "").strip()
        username = ""
        if str(entry.get("username") or "").strip():
            try:
                username = display_profile_username(entry.get("username"))
            except Exception:
                username = ""
        if mode == "link" and url:
            view.add_item(
                discord.ui.Button(
                    label=spec.label[:80],
                    style=discord.ButtonStyle.link,
                    url=url,
                    row=row,
                )
            )
        elif mode == "username" and username and owner_user_id:
            view.add_item(
                discord.ui.Button(
                    label=username[:80],
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"dank:profilecopy:v1:{int(owner_user_id)}:{platform}",
                    row=row,
                )
            )
        # Logo-only identities are already visible in the card image and need no dead button.
    return view if view.children else None
'''
    text = sub_once(
        text,
        r'def _platform_view\(\n.*?\n    return view if view\.children else None\n',
        replacement,
        "runtime text-only platform controls",
    )
    write(path, text)


def patch_live_renderer() -> None:
    path = "stoney_verify/profile_signature_live_renderer.py"
    text = read(path)
    if "PLATFORM_LOGO_DIR" in text:
        return
    text = replace_once(text, "from io import BytesIO\n", "from io import BytesIO\nfrom pathlib import Path\n", "renderer Path import")
    text = replace_once(
        text,
        "from PIL import Image, ImageDraw, ImageFilter, ImageOps\n",
        "from PIL import Image, ImageDraw, ImageFilter, ImageOps\n\nfrom .profile_card_service import PLATFORM_SPECS, platform_entry_mode\n",
        "renderer service imports",
    )
    text = replace_once(
        text,
        "SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT\n",
        "SIGNATURE_RATIO = SIGNATURE_WIDTH / SIGNATURE_HEIGHT\n"
        "PLATFORM_LOGO_DIR = Path(__file__).resolve().parent / \"assets\" / \"platform_logos\"\n"
        "_PLATFORM_LOGO_CACHE: dict[tuple[str, int], Optional[Image.Image]] = {}\n",
        "renderer logo directory",
    )
    start = text.index("def _chip_width(")
    end = text.index("\n\ndef render_profile_signature(", start)
    chip_block = '''def _platform_logo(platform: str, size: int = 24) -> Optional[Image.Image]:
    cache_key = (str(platform or ""), int(size))
    if cache_key in _PLATFORM_LOGO_CACHE:
        cached = _PLATFORM_LOGO_CACHE[cache_key]
        return cached.copy() if cached is not None else None
    try:
        with Image.open(PLATFORM_LOGO_DIR / f"{cache_key[0]}.png") as source:
            logo = source.convert("RGBA")
            logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    except Exception:
        _PLATFORM_LOGO_CACHE[cache_key] = None
        return None
    _PLATFORM_LOGO_CACHE[cache_key] = logo.copy()
    return logo


def _chip_width(draw: ImageDraw.ImageDraw, label: str, font: Any, *, has_logo: bool = False) -> int:
    text_width = 0
    if label:
        box = draw.textbbox((0, 0), label, font=font)
        text_width = box[2] - box[0]
    return max(54, text_width + 32 + (34 if has_logo else 0))


def _draw_chip(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    *,
    platform: str = "",
    font: Any,
    accent: tuple[int, int, int],
    text: tuple[int, int, int],
) -> int:
    logo = _platform_logo(platform, 24) if platform else None
    width = _chip_width(draw, label, font, has_logo=logo is not None)
    draw.rounded_rectangle((x, y, x + width, y + 42), radius=18, fill=accent + (62,), outline=accent + (155,), width=2)
    text_x = x + 16
    if logo is not None:
        image.alpha_composite(logo, (x + 12, y + 9))
        text_x = x + 46
    if label:
        draw.text((text_x, y + 10), label, font=font, fill=text + (255,))
    return width


def _pack_chips(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    chips: Sequence[tuple[str, tuple[int, int, int], str]],
    *,
    start_x: int,
    start_y: int,
    max_x: int,
    font: Any,
    text: tuple[int, int, int],
    max_rows: int,
) -> None:
    x = start_x
    y = start_y
    row = 0
    for raw_label, accent, platform in chips:
        label = _safe_text(raw_label, 44)
        logo = _platform_logo(platform, 24) if platform else None
        if not label and logo is None:
            continue
        width = _chip_width(draw, label, font, has_logo=logo is not None)
        if x + width > max_x and x > start_x:
            row += 1
            if row >= max_rows:
                break
            x = start_x
            y += 52
        width = _draw_chip(
            image,
            draw,
            x,
            y,
            label,
            platform=platform,
            font=font,
            accent=accent,
            text=text,
        )
        x += width + 10
'''
    text = text[:start] + chip_block + text[end:]
    text = replace_once(
        text,
        "    platform_labels: Sequence[str],\n    style: Mapping[str, Any],\n",
        "    platform_labels: Sequence[str],\n    platform_entries: Sequence[Mapping[str, Any]] = (),\n    style: Mapping[str, Any],\n",
        "renderer platform entry argument",
    )
    old_chips = '''    chips: list[tuple[str, tuple[int, int, int]]] = []
    for index, label in enumerate(role_labels[:3]):
        chips.append((label, primary if index % 2 == 0 else secondary))
    for index, label in enumerate(platform_labels[:3]):
        chips.append((label, secondary if index % 2 == 0 else primary))
    for index, label in enumerate(date_labels[:2]):
        chips.append((label, secondary if index % 2 == 0 else primary))
    if not chips:
        chips.append(("Private profile", primary))

    _pack_chips(
        draw,
        chips,
'''
    new_chips = '''    chips: list[tuple[str, tuple[int, int, int], str]] = []
    for index, label in enumerate(role_labels[:3]):
        chips.append((label, primary if index % 2 == 0 else secondary, ""))
    if platform_entries:
        for index, entry in enumerate(platform_entries[:4]):
            platform = str(entry.get("platform") or "")
            if platform not in PLATFORM_SPECS:
                continue
            mode = platform_entry_mode(entry)
            username = _safe_text(entry.get("username"), 32)
            label = "" if mode == "logo" else (username or PLATFORM_SPECS[platform].label)
            chips.append((label, secondary if index % 2 == 0 else primary, platform))
    else:
        for index, label in enumerate(platform_labels[:3]):
            chips.append((label, secondary if index % 2 == 0 else primary, ""))
    for index, label in enumerate(date_labels[:2]):
        chips.append((label, secondary if index % 2 == 0 else primary, ""))
    if not chips:
        chips.append(("Private profile", primary, ""))

    _pack_chips(
        image,
        draw,
        chips,
'''
    text = replace_once(text, old_chips, new_chips, "renderer logo chips")
    text = replace_once(
        text,
        "    platform_labels: Sequence[str],\n) -> bytes:\n",
        "    platform_labels: Sequence[str],\n    platform_entries: Sequence[Mapping[str, Any]] = (),\n) -> bytes:\n",
        "async renderer platform entries",
    )
    text = replace_once(
        text,
        "        platform_labels=list(platform_labels),\n        style=dict(style or {}),\n",
        "        platform_labels=list(platform_labels),\n        platform_entries=[dict(entry) for entry in platform_entries],\n        style=dict(style or {}),\n",
        "async renderer pass entries",
    )
    write(path, text)


def patch_runtime_delivery() -> None:
    path = "stoney_verify/profile_card_runtime.py"
    text = read(path)
    if "platform_entries=platforms" not in text:
        text = replace_once(
            text,
            "        tuple(platform_labels),\n        _stable_cache_value(style),\n",
            "        tuple(platform_labels),\n        _stable_cache_value(platforms),\n        _stable_cache_value(style),\n",
            "runtime cache platform entries",
        )
        text = replace_once(
            text,
            "            platform_labels=platform_labels,\n        )\n",
            "            platform_labels=platform_labels,\n            platform_entries=platforms,\n        )\n",
            "runtime pass platform entries",
        )
    write(path, text)


def patch_public_profile_core() -> None:
    path = "stoney_verify/commands_ext/public_profile_cards_core.py"
    text = read(path)
    if "platform_entry_mode," not in text:
        text = replace_once(text, "    get_profile_user,\n", "    get_profile_user,\n    platform_entry_mode,\n", "public core mode import")
    old_summary = '''        username = str(entry.get("username") or "").strip()
        if not username:
            continue
        visibility = "🌐 Public" if bool(entry.get("shared")) else "🔒 Private"
        link_state = " • official link" if str(entry.get("url") or "").strip() else " • username only"
        safe_username = display_profile_username(username)
        identity_lines.append(
            f"{spec.emoji} **{spec.label}:** `{safe_username}` — {visibility}{link_state}"
        )
'''
    if old_summary in text:
        text = text.replace(
            old_summary,
            '''        username = str(entry.get("username") or "").strip()
        visibility = "Public" if bool(entry.get("shared")) else "Private"
        mode = platform_entry_mode(entry)
        mode_label = {"link": "profile link", "username": "copyable username", "logo": "logo only"}[mode]
        identity = f"`{display_profile_username(username)}`" if username and mode != "logo" else "Logo only"
        identity_lines.append(f"**{spec.label}:** {identity} — {visibility} • {mode_label}")
''',
            1,
        )
    old_clone = '''        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button) or not child.url:
                continue
            self.add_item(
                discord.ui.Button(
                    label=str(child.label or "Profile")[:80],
                    emoji=child.emoji,
                    style=discord.ButtonStyle.link,
                    url=str(child.url),
                    row=child.row,
                )
            )
'''
    if old_clone in text:
        text = text.replace(
            old_clone,
            '''        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button):
                continue
            if child.url:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Profile")[:80],
                        style=discord.ButtonStyle.link,
                        url=str(child.url),
                        row=child.row,
                    )
                )
            elif child.custom_id:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Username")[:80],
                        style=discord.ButtonStyle.secondary,
                        custom_id=str(child.custom_id),
                        row=child.row,
                    )
                )
''',
            1,
        )
    write(path, text)


def patch_public_profile_listener() -> None:
    path = "stoney_verify/commands_ext/public_profile_cards.py"
    text = read(path)
    if "_PROFILE_COPY_PREFIX" in text:
        return
    text = replace_once(text, "from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport asyncio\n", "public asyncio import")
    text = replace_once(
        text,
        "from stoney_verify.profile_card_service import ProfileStorageUnavailable, get_effective_profile_settings\n",
        "from stoney_verify.profile_card_service import (\n"
        "    PLATFORM_SPECS,\n"
        "    ProfileStorageUnavailable,\n"
        "    display_profile_username,\n"
        "    effective_preferences,\n"
        "    get_effective_profile_settings,\n"
        "    get_profile_guild_settings,\n"
        "    get_profile_user,\n"
        "    platform_entry_mode,\n"
        ")\n",
        "public service imports",
    )
    text = replace_once(
        text,
        "_REGISTERED = False\n",
        "_REGISTERED = False\n_PROFILE_COPY_LISTENER_REGISTERED = False\n_PROFILE_COPY_PREFIX = \"dank:profilecopy:v1:\"\n",
        "copy listener globals",
    )
    handler = '''

async def _handle_profile_username_copy(interaction: discord.Interaction) -> None:
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = str((interaction.data or {}).get("custom_id") or "")
    if not custom_id.startswith(_PROFILE_COPY_PREFIX):
        return
    parts = custom_id.split(":")
    if len(parts) != 5:
        return await _safe_ephemeral(interaction, "That platform username is no longer available.", ok=False)
    try:
        owner_id = int(parts[3])
    except Exception:
        owner_id = 0
    platform = parts[4]
    if interaction.guild is None or owner_id <= 0 or platform not in PLATFORM_SPECS:
        return await _safe_ephemeral(interaction, "That platform username is no longer available.", ok=False)
    try:
        user_row, guild_row = await asyncio.gather(
            get_profile_user(owner_id, refresh=True),
            get_profile_guild_settings(interaction.guild.id, owner_id, refresh=True),
        )
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(interaction, "Private profile storage is temporarily unavailable.", ok=False)
    preferences = effective_preferences(user_row.get("preferences"), guild_row.get("settings"))
    raw = dict(user_row.get("platforms") or {}).get(platform)
    if (
        not bool(preferences.get("show_platforms", True))
        or not isinstance(raw, Mapping)
        or not bool(raw.get("shared"))
        or platform_entry_mode(raw) != "username"
        or not str(raw.get("username") or "").strip()
    ):
        return await _safe_ephemeral(interaction, "That member no longer shares this username.", ok=False)
    username = display_profile_username(raw.get("username"))
    await _send_private(interaction, content=f"```text\\n{username}\\n```")
'''
    text = replace_once(text, "\n\ndef _attach_profile_commands() -> None:\n", handler + "\n\ndef _attach_profile_commands() -> None:\n", "copy handler")
    text = replace_once(text, "    global _REGISTERED\n", "    global _REGISTERED, _PROFILE_COPY_LISTENER_REGISTERED\n", "copy listener register globals")
    text = replace_once(
        text,
        "        bot.add_listener(runtime.on_guild_channel_delete, \"on_guild_channel_delete\")\n    if not _REGISTERED:\n",
        "        bot.add_listener(runtime.on_guild_channel_delete, \"on_guild_channel_delete\")\n"
        "    if not _PROFILE_COPY_LISTENER_REGISTERED:\n"
        "        bot.add_listener(_handle_profile_username_copy, \"on_interaction\")\n"
        "        _PROFILE_COPY_LISTENER_REGISTERED = True\n"
        "    if not _REGISTERED:\n",
        "copy listener registration",
    )
    write(path, text)


def remove_platform_emojis_from_studio() -> None:
    path = "stoney_verify/profile_signature_studio.py"
    text = read(path)
    text = text.replace('        emojis = {"link": "🔗", "username": "📋", "logo": spec.emoji}\n', '')
    text = text.replace('            emoji=emojis[mode],\n', '')
    text = text.replace('            emoji="🔒",\n', '')
    text = text.replace('@discord.ui.button(label="Add / Edit Details", emoji="✏️",', '@discord.ui.button(label="Add / Edit Details",')
    text = text.replace('@discord.ui.button(label="Remove", emoji="🗑️",', '@discord.ui.button(label="Remove",')
    text = text.replace('@discord.ui.button(label="Back to Platforms", emoji="↩️",', '@discord.ui.button(label="Back to Platforms",')
    text = text.replace('discord.SelectOption(label=spec.label, value=key, emoji=spec.emoji)', 'discord.SelectOption(label=spec.label, value=key)')
    text = text.replace('f"{spec.emoji} **{spec.label}:** {identity} — "', 'f"**{spec.label}:** {identity} — "')
    text = text.replace('title="🎮 Platforms & Accounts",', 'title="Platforms & Accounts",')
    text = text.replace('f"{\'🌐 Public\' if raw.get(\'shared\') else \'🔒 Private\'} • {mode.title()}"', 'f"{\'Public\' if raw.get(\'shared\') else \'Private\'} • {mode.title()}"')
    write(path, text)


def patch_tests_and_assets() -> None:
    visual_path = "tests/test_live_profile_card_visual_links.py"
    visual = read(visual_path)
    visual = visual.replace(
        '        assert rendered.view is None\n        assert captured["platform_labels"] == ["Steam: @UGLY123"]\n',
        '        assert rendered.view is not None\n'
        '        buttons = [child for child in rendered.view.children if isinstance(child, discord.ui.Button)]\n'
        '        assert len(buttons) == 1\n'
        '        assert buttons[0].label == "@UGLY123"\n'
        '        assert buttons[0].emoji is None\n'
        '        assert buttons[0].custom_id == "dank:profilecopy:v1:42:steam"\n'
        '        assert captured["platform_entries"][0]["platform"] == "steam"\n',
    )
    visual = visual.replace(
        '        assert rendered.view is None\n        assert captured["platform_labels"] == ["Xbox: UGLY123"]\n',
        '        assert rendered.view is not None\n'
        '        buttons = [child for child in rendered.view.children if isinstance(child, discord.ui.Button)]\n'
        '        assert len(buttons) == 1\n'
        '        assert buttons[0].label == "UGLY123"\n'
        '        assert buttons[0].emoji is None\n'
        '        assert buttons[0].custom_id == "dank:profilecopy:v1:42:xbox"\n'
        '        assert captured["platform_entries"][0]["platform"] == "xbox"\n',
    )
    write(visual_path, visual)

    write(
        "tests/test_profile_platform_display_modes.py",
        '''from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

import stoney_verify.profile_card_runtime_core as core
import stoney_verify.profile_card_service as service
from stoney_verify.commands_ext import public_profile_cards


def test_logo_only_entry_needs_no_username_or_url():
    entry = service.normalize_platform_entry("xbox", shared=True, mode="logo")
    assert entry["shared"] is True
    assert entry["mode"] == "logo"
    assert entry["username"] == ""
    assert entry["url"] == ""


def test_legacy_entries_resolve_without_database_migration():
    assert service.platform_entry_mode({"url": "https://example.test/profile"}) == "link"
    assert service.platform_entry_mode({"username": "UglyGameFace"}) == "username"
    assert service.platform_entry_mode({}) == "logo"


def test_controls_are_text_only_and_logo_mode_has_no_dead_button():
    view = core._platform_view(
        [
            {"platform": "twitch", "username": "Streamer", "url": "https://twitch.tv/streamer", "shared": True, "mode": "link"},
            {"platform": "xbox", "username": "UglyGameFace", "url": "", "shared": True, "mode": "username"},
            {"platform": "playstation", "username": "", "url": "", "shared": True, "mode": "logo"},
        ],
        owner_user_id=42,
    )
    assert view is not None
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert len(buttons) == 2
    assert buttons[0].label == "Twitch"
    assert buttons[0].emoji is None
    assert buttons[1].label == "UglyGameFace"
    assert buttons[1].emoji is None
    assert buttons[1].custom_id == "dank:profilecopy:v1:42:xbox"


def test_copy_button_rechecks_current_privacy_and_returns_copy_ready_text(monkeypatch):
    async def scenario() -> None:
        async def user_row(_user_id: int, refresh: bool = False):
            assert refresh is True
            return {
                "preferences": {"show_platforms": True},
                "platforms": {"xbox": {"username": "UglyGameFace", "shared": True, "mode": "username"}},
            }

        async def guild_row(_guild_id: int, _user_id: int, refresh: bool = False):
            assert refresh is True
            return {"settings": {}}

        sent: dict[str, object] = {}

        class Response:
            def is_done(self) -> bool:
                return False

            async def send_message(self, **kwargs):
                sent.update(kwargs)

        interaction = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": "dank:profilecopy:v1:42:xbox"},
            guild=SimpleNamespace(id=7),
            response=Response(),
            followup=SimpleNamespace(send=None),
        )
        monkeypatch.setattr(public_profile_cards, "get_profile_user", user_row)
        monkeypatch.setattr(public_profile_cards, "get_profile_guild_settings", guild_row)
        await public_profile_cards._handle_profile_username_copy(interaction)
        assert sent["ephemeral"] is True
        assert sent["content"] == "```text\\nUglyGameFace\\n```"

    asyncio.run(scenario())
''',
    )

    write(
        "tools/build_profile_platform_logos.mjs",
        '''import fs from "node:fs";
import path from "node:path";
import * as simple from "simple-icons";
import { icon as faIcon } from "@fortawesome/fontawesome-svg-core";
import { faXbox, faPlaystation, faSteam, faTwitch, faYoutube, faNintendoSwitch } from "@fortawesome/free-brands-svg-icons";
import { faLink } from "@fortawesome/free-solid-svg-icons";
import sharp from "sharp";

const outDir = path.resolve("stoney_verify/assets/platform_logos");
fs.mkdirSync(outDir, { recursive: true });
const simpleIcon = (slug) => Object.values(simple).find((value) => value && typeof value === "object" && value.slug === slug);
const simpleSvg = (slug) => {
  const found = simpleIcon(slug);
  if (!found) throw new Error(`Missing Simple Icons slug: ${slug}`);
  return found.svg.replace("<svg ", '<svg fill="#FFFFFF" ');
};
const faSvg = (definition) => faIcon(definition, { styles: { color: "#FFFFFF" } }).html.join("");
const sources = {
  steam: () => simpleIcon("steam") ? simpleSvg("steam") : faSvg(faSteam),
  epic: () => simpleSvg("epicgames"),
  xbox: () => faSvg(faXbox),
  playstation: () => simpleIcon("playstation") ? simpleSvg("playstation") : faSvg(faPlaystation),
  nintendo: () => faSvg(faNintendoSwitch),
  riot: () => simpleSvg("riotgames"),
  battle_net: () => simpleSvg("battledotnet"),
  roblox: () => simpleSvg("roblox"),
  twitch: () => simpleIcon("twitch") ? simpleSvg("twitch") : faSvg(faTwitch),
  youtube: () => simpleIcon("youtube") ? simpleSvg("youtube") : faSvg(faYoutube),
  kick: () => simpleSvg("kick"),
  custom: () => faSvg(faLink),
};
for (const [key, source] of Object.entries(sources)) {
  await sharp(Buffer.from(source())).resize(96, 96, { fit: "contain" }).png().toFile(path.join(outDir, `${key}.png`));
}
console.log(`Generated ${Object.keys(sources).length} bundled platform logos.`);
''',
    )
    write(
        "stoney_verify/assets/platform_logos/ATTRIBUTION.md",
        "# Platform logo assets\n\nGenerated from Simple Icons 16.27.1 and Font Awesome Free. Assets are bundled locally so live signatures never depend on a network request. Brand marks remain trademarks of their respective owners and are used only to identify the member-selected platform.\n",
    )


def main() -> None:
    apply_legacy_modes()
    patch_runtime_controls()
    patch_live_renderer()
    patch_runtime_delivery()
    patch_public_profile_core()
    patch_public_profile_listener()
    remove_platform_emojis_from_studio()
    patch_tests_and_assets()
    print("profile platform modes and real logo renderer applied")


if __name__ == "__main__":
    main()
