from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


def patch_service() -> None:
    path = "stoney_verify/profile_card_service.py"
    text = read(path)
    if "PLATFORM_MODE_LOGO" in text:
        return

    text = replace_once(
        text,
        '_BIDI_CONTROL_RE = re.compile("[\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")\n',
        '_BIDI_CONTROL_RE = re.compile("[\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")\n\n'
        'PLATFORM_MODE_LINK = "link"\n'
        'PLATFORM_MODE_USERNAME = "username"\n'
        'PLATFORM_MODE_LOGO = "logo"\n'
        'PLATFORM_SHARE_MODES = frozenset({\n'
        '    PLATFORM_MODE_LINK,\n'
        '    PLATFORM_MODE_USERNAME,\n'
        '    PLATFORM_MODE_LOGO,\n'
        '})\n',
        label="service mode constants",
    )

    text = replace_once(
        text,
        'def display_profile_username(value: Any) -> str:\n'
        '    """Return a Discord-safe username that cannot create markdown links."""\n'
        '    return clean_profile_username(value).replace("`", "ʼ")\n',
        'def clean_optional_profile_username(value: Any) -> str:\n'
        '    """Normalize an optional username without forcing logo-only profiles to invent one."""\n'
        '    if not str(value or "").strip():\n'
        '        return ""\n'
        '    return clean_profile_username(value)\n\n\n'
        'def display_profile_username(value: Any) -> str:\n'
        '    """Return a Discord-safe username that cannot create markdown links."""\n'
        '    return clean_profile_username(value).replace("`", "ʼ")\n\n\n'
        'def platform_entry_mode(entry: Optional[Mapping[str, Any]]) -> str:\n'
        '    """Resolve legacy and current entries to link, username, or logo-only display."""\n'
        '    raw = dict(entry or {}) if isinstance(entry, Mapping) else {}\n'
        '    mode = str(raw.get("mode") or "").strip().lower()\n'
        '    if mode in PLATFORM_SHARE_MODES:\n'
        '        return mode\n'
        '    if str(raw.get("url") or "").strip():\n'
        '        return PLATFORM_MODE_LINK\n'
        '    if str(raw.get("username") or "").strip():\n'
        '        return PLATFORM_MODE_USERNAME\n'
        '    return PLATFORM_MODE_LOGO\n',
        label="service optional username and mode helper",
    )

    text = sub_once(
        text,
        r'def normalize_platform_entry\(\n    platform: Any,\n    \*,\n    username: Any,\n    profile_url: Any = "",\n    shared: bool = False,\n\) -> dict\[str, Any\]:\n    key = clean_platform_key\(platform\)\n    return \{\n        "platform": key,\n        "username": clean_profile_username\(username\),\n        "url": normalize_platform_url\(key, profile_url\),\n        "shared": bool\(shared\),\n        "updated_at": utc_now_iso\(\),\n    \}\n',
        'def normalize_platform_entry(\n'
        '    platform: Any,\n'
        '    *,\n'
        '    username: Any = "",\n'
        '    profile_url: Any = "",\n'
        '    shared: bool = False,\n'
        '    mode: Any = "",\n'
        ') -> dict[str, Any]:\n'
        '    key = clean_platform_key(platform)\n'
        '    spec = PLATFORM_SPECS[key]\n'
        '    clean_username = clean_optional_profile_username(username)\n'
        '    clean_url = normalize_platform_url(key, profile_url)\n'
        '    requested = str(mode or "").strip().lower()\n'
        '    if requested not in PLATFORM_SHARE_MODES:\n'
        '        requested = (\n'
        '            PLATFORM_MODE_LINK\n'
        '            if clean_url\n'
        '            else PLATFORM_MODE_USERNAME\n'
        '            if clean_username\n'
        '            else PLATFORM_MODE_LOGO\n'
        '        )\n'
        '    if requested == PLATFORM_MODE_LINK:\n'
        '        if not spec.supports_url:\n'
        '            raise InvalidPlatformProfile(f"{spec.label} does not support a reliable public profile link.")\n'
        '        if not clean_url:\n'
        '            raise InvalidPlatformProfile(f"Add an official {spec.label} profile link before showing Link mode.")\n'
        '    if requested == PLATFORM_MODE_USERNAME and not clean_username:\n'
        '        raise InvalidPlatformProfile("Add the username before showing Username mode.")\n'
        '    return {\n'
        '        "platform": key,\n'
        '        "username": clean_username,\n'
        '        "url": clean_url,\n'
        '        "shared": bool(shared),\n'
        '        "mode": requested,\n'
        '        "updated_at": utc_now_iso(),\n'
        '    }\n',
        label="service normalize platform entry",
    )

    text = replace_once(
        text,
        '                profile_url=raw.get("url"),\n'
        '                shared=True,\n',
        '                profile_url=raw.get("url"),\n'
        '                shared=True,\n'
        '                mode=raw.get("mode"),\n',
        label="service visible entry mode",
    )

    text = replace_once(
        text,
        'async def save_platform_identity(\n'
        '    user_id: int,\n'
        '    platform: Any,\n'
        '    *,\n'
        '    username: Any,\n'
        '    profile_url: Any = "",\n'
        '    shared: bool = False,\n'
        ') -> dict[str, Any]:\n'
        '    uid = int(user_id)\n'
        '    key = clean_platform_key(platform)\n'
        '    entry = normalize_platform_entry(key, username=username, profile_url=profile_url, shared=shared)\n',
        'async def save_platform_identity(\n'
        '    user_id: int,\n'
        '    platform: Any,\n'
        '    *,\n'
        '    username: Any = "",\n'
        '    profile_url: Any = "",\n'
        '    shared: bool = False,\n'
        '    mode: Any = "",\n'
        ') -> dict[str, Any]:\n'
        '    uid = int(user_id)\n'
        '    key = clean_platform_key(platform)\n'
        '    entry = normalize_platform_entry(\n'
        '        key,\n'
        '        username=username,\n'
        '        profile_url=profile_url,\n'
        '        shared=shared,\n'
        '        mode=mode,\n'
        '    )\n',
        label="service save platform mode",
    )
    write(path, text)


def patch_runtime_core() -> None:
    path = "stoney_verify/profile_card_runtime_core.py"
    text = read(path)
    if "dank:profilecopy:v1" in text:
        return
    text = replace_once(
        text,
        '    normalize_server_allowed_fields,\n'
        '    upsert_live_card_state,\n'
        '    visible_platform_entries,\n',
        '    normalize_server_allowed_fields,\n'
        '    platform_entry_mode,\n'
        '    upsert_live_card_state,\n'
        '    visible_platform_entries,\n',
        label="core import platform mode",
    )
    text = sub_once(
        text,
        r'def _platform_view\(entries: list\[dict\[str, Any\]\]\) -> Optional\[discord\.ui\.View\]:\n.*?\n\nasync def render_live_profile_card',
        'def _platform_view(\n'
        '    entries: list[dict[str, Any]],\n'
        '    *,\n'
        '    owner_user_id: Optional[int] = None,\n'
        ') -> Optional[discord.ui.View]:\n'
        '    """Build cross-client platform controls without fake or unsafe links."""\n'
        '    view = discord.ui.View(timeout=None)\n'
        '    for index, entry in enumerate(entries[:20]):\n'
        '        platform = str(entry.get("platform") or "")\n'
        '        spec = PLATFORM_SPECS.get(platform)\n'
        '        if spec is None:\n'
        '            continue\n'
        '        mode = platform_entry_mode(entry)\n'
        '        username = ""\n'
        '        if str(entry.get("username") or "").strip():\n'
        '            try:\n'
        '                username = display_profile_username(entry.get("username"))\n'
        '            except Exception:\n'
        '                username = ""\n'
        '        url = str(entry.get("url") or "").strip()\n'
        '        row = min(3, index // 5)\n'
        '        if mode == "link" and url:\n'
        '            view.add_item(\n'
        '                discord.ui.Button(\n'
        '                    label=spec.label[:80],\n'
        '                    emoji=spec.emoji,\n'
        '                    style=discord.ButtonStyle.link,\n'
        '                    url=url,\n'
        '                    row=row,\n'
        '                )\n'
        '            )\n'
        '            continue\n'
        '        if mode == "username" and username and owner_user_id:\n'
        '            view.add_item(\n'
        '                discord.ui.Button(\n'
        '                    label=username[:80],\n'
        '                    emoji=spec.emoji,\n'
        '                    style=discord.ButtonStyle.secondary,\n'
        '                    custom_id=f"dank:profilecopy:v1:{int(owner_user_id)}:{platform}",\n'
        '                    row=row,\n'
        '                )\n'
        '            )\n'
        '            continue\n'
        '        if mode == "logo":\n'
        '            view.add_item(\n'
        '                discord.ui.Button(\n'
        '                    emoji=spec.emoji,\n'
        '                    style=discord.ButtonStyle.secondary,\n'
        '                    disabled=True,\n'
        '                    row=row,\n'
        '                )\n'
        '            )\n'
        '    return view if view.children else None\n\n\n'
        'async def render_live_profile_card',
        label="core platform view modes",
    )
    write(path, text)


def patch_runtime() -> None:
    path = "stoney_verify/profile_card_runtime.py"
    text = read(path)
    if "owner_user_id=member.id" in text:
        return
    text = replace_once(
        text,
        '    list_live_card_states_for_user,\n'
        '    upsert_live_card_state,\n'
        '    visible_platform_entries,\n',
        '    list_live_card_states_for_user,\n'
        '    platform_entry_mode,\n'
        '    upsert_live_card_state,\n'
        '    visible_platform_entries,\n',
        label="runtime import platform mode",
    )
    text = sub_once(
        text,
        r'def _compact_platform_labels\(entries: list\[dict\[str, Any\]\]\) -> list\[str\]:\n.*?\n    return labels\n',
        'def _compact_platform_labels(entries: list[dict[str, Any]]) -> list[str]:\n'
        '    labels: list[str] = []\n'
        '    for entry in entries[:4]:\n'
        '        spec = PLATFORM_SPECS.get(str(entry.get("platform") or ""))\n'
        '        if spec is None or platform_entry_mode(entry) == "logo":\n'
        '            continue\n'
        '        username = ""\n'
        '        if str(entry.get("username") or "").strip():\n'
        '            try:\n'
        '                username = display_profile_username(entry.get("username"))\n'
        '            except Exception:\n'
        '                username = ""\n'
        '        labels.append(f"{spec.label}: {username}" if username else spec.label)\n'
        '    return labels\n',
        label="runtime compact platform labels",
    )
    text = replace_once(
        text,
        '    view = _platform_view(platforms)\n',
        '    view = _platform_view(platforms, owner_user_id=member.id)\n',
        label="runtime owner-aware platform view",
    )
    write(path, text)


def patch_public_profile_cards() -> None:
    path = "stoney_verify/commands_ext/public_profile_cards.py"
    text = read(path)
    if "_PROFILE_COPY_PREFIX" in text:
        return
    text = replace_once(
        text,
        '    get_profile_user,\n'
        '    remove_platform_identity,\n',
        '    get_profile_user,\n'
        '    platform_entry_mode,\n'
        '    remove_platform_identity,\n',
        label="public profile import mode",
    )
    text = replace_once(
        text,
        '_REGISTERED = False\n',
        '_REGISTERED = False\n'
        '_PROFILE_COPY_LISTENER_REGISTERED = False\n'
        '_PROFILE_COPY_PREFIX = "dank:profilecopy:v1:"\n',
        label="public profile copy globals",
    )

    copy_handler = '''\n\nasync def _handle_profile_username_copy(interaction: discord.Interaction) -> bool:\n    if interaction.type != discord.InteractionType.component:\n        return False\n    data = interaction.data or {}\n    custom_id = str(data.get("custom_id") or "")\n    if not custom_id.startswith(_PROFILE_COPY_PREFIX):\n        return False\n    parts = custom_id.split(":", 5)\n    if len(parts) != 5:\n        return True\n    try:\n        owner_id = int(parts[3])\n    except Exception:\n        owner_id = 0\n    platform = str(parts[4] or "")\n    if interaction.guild is None or owner_id <= 0 or platform not in PLATFORM_SPECS:\n        await _safe_ephemeral(interaction, "That platform username is no longer available.", ok=False)\n        return True\n    try:\n        user_row, guild_row = await asyncio.gather(\n            get_profile_user(owner_id, refresh=True),\n            get_profile_guild_settings(interaction.guild.id, owner_id, refresh=True),\n        )\n    except ProfileStorageUnavailable:\n        await _safe_ephemeral(interaction, "Private profile storage is temporarily unavailable.", ok=False)\n        return True\n    preferences = effective_preferences(\n        user_row.get("preferences"),\n        guild_row.get("settings"),\n    )\n    raw = dict(user_row.get("platforms") or {}).get(platform)\n    if (\n        not bool(preferences.get("show_platforms", True))\n        or not isinstance(raw, Mapping)\n        or not bool(raw.get("shared"))\n        or platform_entry_mode(raw) != "username"\n        or not str(raw.get("username") or "").strip()\n    ):\n        await _safe_ephemeral(interaction, "That member no longer shares this username.", ok=False)\n        return True\n    username = display_profile_username(raw.get("username"))\n    await _send_private(\n        interaction,\n        content=f"```text\\n{username}\\n```",\n    )\n    return True\n'''
    text = replace_once(
        text,
        '\n\ndef _attach_profile_commands() -> None:\n',
        copy_handler + '\n\ndef _attach_profile_commands() -> None:\n',
        label="public profile copy handler",
    )

    text = replace_once(
        text,
        'def register_public_profile_cards(bot: Any, tree: Any) -> None:\n'
        '    del tree\n'
        '    global _REGISTERED\n',
        'def register_public_profile_cards(bot: Any, tree: Any) -> None:\n'
        '    del tree\n'
        '    global _REGISTERED, _PROFILE_COPY_LISTENER_REGISTERED\n',
        label="public profile register globals",
    )
    text = replace_once(
        text,
        '        bot.add_listener(runtime.on_guild_channel_delete, "on_guild_channel_delete")\n'
        '    if not _REGISTERED:\n',
        '        bot.add_listener(runtime.on_guild_channel_delete, "on_guild_channel_delete")\n'
        '    if not _PROFILE_COPY_LISTENER_REGISTERED:\n'
        '        @bot.listen("on_interaction")\n'
        '        async def _dank_profile_username_copy_listener(interaction: discord.Interaction) -> None:\n'
        '            try:\n'
        '                await _handle_profile_username_copy(interaction)\n'
        '            except Exception as exc:\n'
        '                print(f"⚠️ profile username copy failed: {type(exc).__name__}: {exc}")\n'
        '        _PROFILE_COPY_LISTENER_REGISTERED = True\n'
        '    if not _REGISTERED:\n',
        label="public profile copy listener registration",
    )

    text = replace_once(
        text,
        '        username = str(entry.get("username") or "").strip()\n'
        '        if not username:\n'
        '            continue\n'
        '        visibility = "🌐 Public" if bool(entry.get("shared")) else "🔒 Private"\n'
        '        link_state = " • official link" if str(entry.get("url") or "").strip() else " • username only"\n'
        '        safe_username = display_profile_username(username)\n'
        '        identity_lines.append(\n'
        '            f"{spec.emoji} **{spec.label}:** `{safe_username}` — {visibility}{link_state}"\n'
        '        )\n',
        '        username = str(entry.get("username") or "").strip()\n'
        '        visibility = "🌐 Public" if bool(entry.get("shared")) else "🔒 Private"\n'
        '        mode = platform_entry_mode(entry)\n'
        '        mode_label = {"link": "official link", "username": "copyable username", "logo": "logo only"}[mode]\n'
        '        identity = (\n'
        '            f"`{display_profile_username(username)}`"\n'
        '            if username and mode != "logo"\n'
        '            else "Logo only"\n'
        '        )\n'
        '        identity_lines.append(\n'
        '            f"{spec.emoji} **{spec.label}:** {identity} — {visibility} • {mode_label}"\n'
        '        )\n',
        label="public profile settings logo summary",
    )
    write(path, text)


def patch_studio() -> None:
    path = "stoney_verify/profile_signature_studio.py"
    text = read(path)
    if "class _PlatformModeButton" in text:
        return
    text = replace_once(
        text,
        '    get_profile_user,\n'
        '    remove_platform_identity,\n',
        '    get_profile_user,\n'
        '    platform_entry_mode,\n'
        '    remove_platform_identity,\n',
        label="studio import platform mode",
    )

    text = replace_once(
        text,
        '        for child in list(getattr(source_view, "children", []) or []):\n'
        '            if not isinstance(child, discord.ui.Button) or not child.url:\n'
        '                continue\n'
        '            self.add_item(\n'
        '                discord.ui.Button(\n'
        '                    label=str(child.label or "Profile")[:80],\n'
        '                    emoji=child.emoji,\n'
        '                    style=discord.ButtonStyle.link,\n'
        '                    url=str(child.url),\n'
        '                )\n'
        '            )\n',
        '        for child in list(getattr(source_view, "children", []) or []):\n'
        '            if not isinstance(child, discord.ui.Button):\n'
        '                continue\n'
        '            if child.url:\n'
        '                self.add_item(\n'
        '                    discord.ui.Button(\n'
        '                        label=str(child.label or "Profile")[:80],\n'
        '                        emoji=child.emoji,\n'
        '                        style=discord.ButtonStyle.link,\n'
        '                        url=str(child.url),\n'
        '                    )\n'
        '                )\n'
        '            elif child.custom_id or child.disabled:\n'
        '                self.add_item(\n'
        '                    discord.ui.Button(\n'
        '                        label=str(child.label)[:80] if child.label else None,\n'
        '                        emoji=child.emoji,\n'
        '                        style=child.style,\n'
        '                        custom_id=str(child.custom_id) if child.custom_id else None,\n'
        '                        disabled=bool(child.disabled),\n'
        '                    )\n'
        '                )\n',
        label="studio preview platform buttons",
    )

    platform_block = '''class PlatformEditModal(discord.ui.Modal):\n    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any]) -> None:\n        spec = PLATFORM_SPECS[platform]\n        super().__init__(title=f"{spec.label} Profile", timeout=900)\n        self.author_id = int(author_id)\n        self.platform = platform\n        self.username = discord.ui.TextInput(\n            label="Username or handle (optional)",\n            default=str(entry.get("username") or "")[:80],\n            max_length=80,\n            required=False,\n            placeholder="Leave blank when you only want the platform logo",\n        )\n        self.url = discord.ui.TextInput(\n            label="Official profile link (optional)",\n            default=str(entry.get("url") or "")[:500],\n            max_length=500,\n            required=False,\n            placeholder="Only supported official profile links are accepted",\n        )\n        self.add_item(self.username)\n        self.add_item(self.url)\n\n    async def on_submit(self, interaction: discord.Interaction) -> None:\n        if int(interaction.user.id) != self.author_id:\n            return await _private(interaction, content="❌ Only the person who opened this editor can submit it.")\n        user = await get_profile_user(self.author_id, refresh=True)\n        current = dict(user.get("platforms") or {}).get(self.platform)\n        current = dict(current) if isinstance(current, Mapping) else {}\n        shared = bool(current.get("shared"))\n        username = str(self.username.value or "").strip()\n        profile_url = str(self.url.value or "").strip()\n        mode = platform_entry_mode(current) if current else ""\n        if not username and not profile_url:\n            mode = "logo"\n        elif not mode or mode == "logo":\n            mode = "link" if profile_url else "username"\n        try:\n            entry = await save_platform_identity(\n                self.author_id,\n                self.platform,\n                username=username,\n                profile_url=profile_url,\n                shared=shared,\n                mode=mode,\n            )\n        except InvalidPlatformProfile as exc:\n            return await _private(interaction, content=f"❌ {exc}")\n        except ProfileStorageUnavailable:\n            return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")\n        await _invalidate(interaction, all_guilds=True)\n        spec = PLATFORM_SPECS[self.platform]\n        await _edit_private(\n            interaction,\n            content=f"✅ {spec.label} saved. Choose **Link**, **Username**, **Logo only**, or **Private** below.",\n            embed=_platform_detail_embed(self.platform, entry),\n            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),\n        )\n\n\ndef _platform_detail_embed(platform: str, entry: Mapping[str, Any]) -> discord.Embed:\n    spec = PLATFORM_SPECS[platform]\n    raw = dict(entry or {})\n    username = str(raw.get("username") or "").strip()\n    shared = bool(raw.get("shared"))\n    mode = platform_entry_mode(raw)\n    mode_label = {\n        "link": "🔗 Link button",\n        "username": "📋 Copyable username button",\n        "logo": "🎮 Logo only",\n    }[mode]\n    embed = discord.Embed(\n        title=f"{spec.emoji} {spec.label}",\n        description=(\n            "Choose exactly how this platform appears. **Username** creates a fast same-channel private copy box, "\n            "**Link** opens the official profile, and **Logo only** requires no username or link."\n        ),\n        color=discord.Color.green() if shared else discord.Color.blurple(),\n    )\n    embed.add_field(\n        name="Username",\n        value=f"`{display_profile_username(username)}`" if username else "Optional — not saved",\n        inline=False,\n    )\n    embed.add_field(name="Visibility", value="🌐 Public" if shared else "🔒 Private", inline=True)\n    embed.add_field(name="Public display", value=mode_label if shared else "Hidden", inline=True)\n    embed.add_field(name="Official link", value="Saved" if raw.get("url") else "Not saved", inline=True)\n    return embed\n\n\nclass _PlatformModeButton(discord.ui.Button):\n    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any], mode: str, row: int = 0) -> None:\n        spec = PLATFORM_SPECS[platform]\n        raw = dict(entry or {})\n        labels = {"link": "Show Link", "username": "Show Username", "logo": "Logo Only"}\n        emojis = {"link": "🔗", "username": "📋", "logo": spec.emoji}\n        disabled = (mode == "link" and not str(raw.get("url") or "").strip()) or (\n            mode == "username" and not str(raw.get("username") or "").strip()\n        )\n        super().__init__(\n            label=labels[mode],\n            emoji=emojis[mode],\n            style=discord.ButtonStyle.success if bool(raw.get("shared")) and platform_entry_mode(raw) == mode else discord.ButtonStyle.secondary,\n            disabled=disabled,\n            row=row,\n        )\n        self.author_id = int(author_id)\n        self.platform = platform\n        self.mode = mode\n\n    async def callback(self, interaction: discord.Interaction) -> None:\n        user = await get_profile_user(self.author_id, refresh=True)\n        raw = dict(user.get("platforms") or {}).get(self.platform)\n        raw = dict(raw) if isinstance(raw, Mapping) else {}\n        try:\n            entry = await save_platform_identity(\n                self.author_id,\n                self.platform,\n                username=raw.get("username", ""),\n                profile_url=raw.get("url", ""),\n                shared=True,\n                mode=self.mode,\n            )\n        except (InvalidPlatformProfile, ProfileStorageUnavailable) as exc:\n            return await _private(interaction, content=f"❌ {exc}")\n        await _invalidate(interaction, all_guilds=True)\n        await _edit_private(\n            interaction,\n            content=f"✅ {PLATFORM_SPECS[self.platform].label} now uses **{self.label}** on public signatures.",\n            embed=_platform_detail_embed(self.platform, entry),\n            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),\n        )\n\n\nclass _PlatformPrivateButton(discord.ui.Button):\n    def __init__(self, *, author_id: int, platform: str, entry: Mapping[str, Any], row: int = 0) -> None:\n        raw = dict(entry or {})\n        super().__init__(\n            label="Make Private",\n            emoji="🔒",\n            style=discord.ButtonStyle.danger,\n            disabled=not bool(raw.get("shared")),\n            row=row,\n        )\n        self.author_id = int(author_id)\n        self.platform = platform\n\n    async def callback(self, interaction: discord.Interaction) -> None:\n        user = await get_profile_user(self.author_id, refresh=True)\n        raw = dict(user.get("platforms") or {}).get(self.platform)\n        raw = dict(raw) if isinstance(raw, Mapping) else {}\n        if not raw:\n            return await _private(interaction, content="Nothing is saved for that platform yet.")\n        try:\n            entry = await save_platform_identity(\n                self.author_id,\n                self.platform,\n                username=raw.get("username", ""),\n                profile_url=raw.get("url", ""),\n                shared=False,\n                mode=platform_entry_mode(raw),\n            )\n        except (InvalidPlatformProfile, ProfileStorageUnavailable) as exc:\n            return await _private(interaction, content=f"❌ {exc}")\n        await _invalidate(interaction, all_guilds=True)\n        await _edit_private(\n            interaction,\n            content=f"✅ {PLATFORM_SPECS[self.platform].label} is now private.",\n            embed=_platform_detail_embed(self.platform, entry),\n            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),\n        )\n\n\nclass PlatformDetailView(discord.ui.View):\n    def __init__(self, *, author_id: int, platform: str, entry: Optional[Mapping[str, Any]] = None) -> None:\n        super().__init__(timeout=600)\n        self.author_id = int(author_id)\n        self.platform = str(platform)\n        raw = dict(entry or {})\n        if PLATFORM_SPECS[self.platform].supports_url:\n            self.add_item(_PlatformModeButton(author_id=self.author_id, platform=self.platform, entry=raw, mode="link"))\n        self.add_item(_PlatformModeButton(author_id=self.author_id, platform=self.platform, entry=raw, mode="username"))\n        self.add_item(_PlatformModeButton(author_id=self.author_id, platform=self.platform, entry=raw, mode="logo"))\n        self.add_item(_PlatformPrivateButton(author_id=self.author_id, platform=self.platform, entry=raw))\n        self.remove.disabled = not bool(raw)\n\n    async def interaction_check(self, interaction: discord.Interaction) -> bool:\n        if int(interaction.user.id) != self.author_id:\n            await _private(interaction, content="❌ Only the person who opened this editor can use it.")\n            return False\n        return True\n\n    @discord.ui.button(label="Add / Edit Details", emoji="✏️", style=discord.ButtonStyle.primary, row=1)\n    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        _ = button\n        user = await get_profile_user(self.author_id, refresh=True)\n        raw = dict(user.get("platforms") or {}).get(self.platform)\n        entry = dict(raw) if isinstance(raw, Mapping) else {}\n        await interaction.response.send_modal(\n            PlatformEditModal(author_id=self.author_id, platform=self.platform, entry=entry)\n        )\n\n    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)\n    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        _ = button\n        try:\n            await remove_platform_identity(self.author_id, self.platform)\n        except ProfileStorageUnavailable:\n            return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")\n        await _invalidate(interaction, all_guilds=True)\n        await open_platform_manager(interaction, replace=True)\n\n    @discord.ui.button(label="Back to Platforms", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)\n    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        _ = button\n        await open_platform_manager(interaction, replace=True)\n\n\n'''
    text = sub_once(
        text,
        r'class PlatformEditModal\(discord\.ui\.Modal\):\n.*?\n\nclass PlatformSelect\(discord\.ui\.Select\):',
        platform_block + 'class PlatformSelect(discord.ui.Select):',
        label="studio platform editor and modes",
    )

    text = replace_once(
        text,
        '        raw = platforms.get(key)\n'
        '        if not isinstance(raw, Mapping) or not raw.get("username"):\n'
        '            continue\n'
        '        lines.append(\n'
        '            f"{spec.emoji} **{spec.label}:** `{display_profile_username(raw.get(\'username\'))}` — "\n'
        '            f"{\'🌐 Public\' if raw.get(\'shared\') else \'🔒 Private\'}"\n'
        '        )\n',
        '        raw = platforms.get(key)\n'
        '        if not isinstance(raw, Mapping):\n'
        '            continue\n'
        '        mode = platform_entry_mode(raw)\n'
        '        username = str(raw.get("username") or "").strip()\n'
        '        identity = (\n'
        '            f"`{display_profile_username(username)}`"\n'
        '            if username and mode != "logo"\n'
        '            else "Logo only"\n'
        '        )\n'
        '        lines.append(\n'
        '            f"{spec.emoji} **{spec.label}:** {identity} — "\n'
        '            f"{\'🌐 Public\' if raw.get(\'shared\') else \'🔒 Private\'} • {mode.title()}"\n'
        '        )\n',
        label="studio platform manager logo summary",
    )
    text = replace_once(
        text,
        '            "Choose an account below. The next screen gives you an obvious **Make Public** or **Make Private** "\n'
        '            "button. Saving a username never exposes it automatically."\n',
        '            "Choose a platform below, then select **Link**, **Username**, **Logo only**, or **Private**. "\n'
        '            "Logo only needs no account details, and saving details never exposes them automatically."\n',
        label="studio manager description",
    )
    write(path, text)


def patch_visual_tests() -> None:
    path = "tests/test_live_profile_card_visual_links.py"
    text = read(path)
    if "dank:profilecopy:v1:42:xbox" in text:
        return
    text = replace_once(
        text,
        '        assert rendered.view is None\n'
        '        assert captured["platform_labels"] == ["Steam: @UGLY123"]\n',
        '        assert rendered.view is not None\n'
        '        buttons = [child for child in rendered.view.children if isinstance(child, discord.ui.Button)]\n'
        '        assert len(buttons) == 1\n'
        '        assert buttons[0].label == "@UGLY123"\n'
        '        assert buttons[0].custom_id == "dank:profilecopy:v1:42:steam"\n'
        '        assert captured["platform_labels"] == ["Steam: @UGLY123"]\n',
        label="visual test steam username button",
    )
    text = replace_once(
        text,
        '        assert rendered.view is None\n'
        '        assert captured["platform_labels"] == ["Xbox: UGLY123"]\n',
        '        assert rendered.view is not None\n'
        '        buttons = [child for child in rendered.view.children if isinstance(child, discord.ui.Button)]\n'
        '        assert len(buttons) == 1\n'
        '        assert buttons[0].label == "UGLY123"\n'
        '        assert buttons[0].custom_id == "dank:profilecopy:v1:42:xbox"\n'
        '        assert captured["platform_labels"] == ["Xbox: UGLY123"]\n',
        label="visual test xbox username button",
    )
    write(path, text)


def create_mode_tests() -> None:
    path = ROOT / "tests/test_profile_platform_display_modes.py"
    if path.exists():
        return
    path.write_text('''from __future__ import annotations\n\nimport asyncio\nfrom types import SimpleNamespace\n\nimport discord\n\nimport stoney_verify.profile_card_runtime_core as core\nimport stoney_verify.profile_card_service as service\nfrom stoney_verify.commands_ext import public_profile_cards\n\n\ndef test_logo_only_entry_needs_no_username_or_url():\n    entry = service.normalize_platform_entry(\n        "xbox",\n        username="",\n        profile_url="",\n        shared=True,\n        mode="logo",\n    )\n    assert entry["shared"] is True\n    assert entry["mode"] == "logo"\n    assert entry["username"] == ""\n    assert entry["url"] == ""\n\n\ndef test_legacy_entries_resolve_without_data_migration():\n    assert service.platform_entry_mode({"url": "https://twitch.tv/example"}) == "link"\n    assert service.platform_entry_mode({"username": "Example"}) == "username"\n    assert service.platform_entry_mode({}) == "logo"\n\n\ndef test_cross_client_platform_controls_use_link_username_and_logo_modes():\n    view = core._platform_view(\n        [\n            {\n                "platform": "twitch",\n                "username": "Streamer",\n                "url": "https://twitch.tv/streamer",\n                "shared": True,\n                "mode": "link",\n            },\n            {\n                "platform": "xbox",\n                "username": "UglyGameFace",\n                "url": "",\n                "shared": True,\n                "mode": "username",\n            },\n            {\n                "platform": "playstation",\n                "username": "",\n                "url": "",\n                "shared": True,\n                "mode": "logo",\n            },\n        ],\n        owner_user_id=42,\n    )\n    assert view is not None\n    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]\n    assert buttons[0].url == "https://twitch.tv/streamer"\n    assert buttons[1].label == "UglyGameFace"\n    assert buttons[1].custom_id == "dank:profilecopy:v1:42:xbox"\n    assert buttons[2].disabled is True\n    assert buttons[2].label is None\n\n\ndef test_copy_button_rechecks_current_privacy_and_returns_copy_ready_text(monkeypatch):\n    async def scenario() -> None:\n        async def user_row(_user_id: int, refresh: bool = False):\n            assert refresh is True\n            return {\n                "preferences": {"show_platforms": True},\n                "platforms": {\n                    "xbox": {\n                        "platform": "xbox",\n                        "username": "UglyGameFace",\n                        "url": "",\n                        "shared": True,\n                        "mode": "username",\n                    }\n                },\n            }\n\n        async def guild_row(_guild_id: int, _user_id: int, refresh: bool = False):\n            assert refresh is True\n            return {"settings": {}}\n\n        sent: dict[str, object] = {}\n\n        class Response:\n            def is_done(self) -> bool:\n                return False\n\n            async def send_message(self, **kwargs):\n                sent.update(kwargs)\n\n        interaction = SimpleNamespace(\n            type=discord.InteractionType.component,\n            data={"custom_id": "dank:profilecopy:v1:42:xbox"},\n            guild=SimpleNamespace(id=7),\n            response=Response(),\n            followup=SimpleNamespace(send=None),\n        )\n        monkeypatch.setattr(public_profile_cards, "get_profile_user", user_row)\n        monkeypatch.setattr(public_profile_cards, "get_profile_guild_settings", guild_row)\n\n        handled = await public_profile_cards._handle_profile_username_copy(interaction)\n        assert handled is True\n        assert sent["ephemeral"] is True\n        assert sent["content"] == "```text\\nUglyGameFace\\n```"\n\n    asyncio.run(scenario())\n''', encoding="utf-8")


def main() -> None:
    patch_service()
    patch_runtime_core()
    patch_runtime()
    patch_public_profile_cards()
    patch_studio()
    patch_visual_tests()
    create_mode_tests()
    print("profile platform mode patch applied")


if __name__ == "__main__":
    main()
