from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


core = Path("stoney_verify/commands_ext/public_profile_cards_core.py")
replace_once(
    core,
    '''    embed = discord.Embed(
        title="🔐 Profile Privacy & Platforms",
        description=(
            "These controls apply to your public/live profile in this server. "
            "Your platform identities are private until you explicitly save them as shared."
        ),
''',
    '''    embed = discord.Embed(
        title="🔐 Profile Privacy",
        description=(
            "Choose what your compact signature may show. For Steam, Xbox, PlayStation, and every other "
            "saved account, use **Manage Accounts** below to change that individual account between "
            "**Public** and **Private**."
        ),
''',
    label="privacy embed heading",
)
replace_once(
    core,
    '''        visibility = "shared" if bool(entry.get("shared")) else "private"
        link_state = " • linked" if str(entry.get("url") or "").strip() else ""
        safe_username = display_profile_username(username)
        identity_lines.append(
            f"{spec.emoji} **{spec.label}:** `{safe_username}` — {visibility}{link_state}"
        )
    embed.add_field(
        name="Saved platform identities",
        value="\\n".join(identity_lines)[:1024] if identity_lines else "None saved. Use `/dank profile platform`.",
        inline=False,
    )
''',
    '''        visibility = "🌐 Public" if bool(entry.get("shared")) else "🔒 Private"
        link_state = " • official link" if str(entry.get("url") or "").strip() else " • username only"
        safe_username = display_profile_username(username)
        identity_lines.append(
            f"{spec.emoji} **{spec.label}:** `{safe_username}` — {visibility}{link_state}"
        )
    account_summary = "\\n".join(identity_lines)[:820] if identity_lines else "No accounts saved yet."
    embed.add_field(
        name="Gaming & social accounts",
        value=(
            account_summary
            + "\\n\\nUse **Manage Accounts** below to add, edit, remove, or change an account's Public/Private status."
        )[:1024],
        inline=False,
    )
''',
    label="platform identity clarity",
)

public = Path("stoney_verify/commands_ext/public_profile_cards.py")
replace_once(
    public,
    '''class _PreviewProfileButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Preview Compact Signature",
            emoji="👀",
            style=discord.ButtonStyle.primary,
            custom_id="dank:profilecard:v2:preview_compact",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _safe_ephemeral(interaction, "Could not resolve your server member.", ok=False)
        await _defer_private(interaction)
        try:
            config = await get_guild_config(view.guild_id)
            allowed = set(parse_live_card_config(config).allowed_fields)
            rendered = await render_live_profile_card(
                member,
                allowed,
                trigger_message_id=0,
                require_live_enabled=False,
            )
        except ProfileStorageUnavailable:
            return await _safe_ephemeral(interaction, "Private profile storage is unavailable.", ok=False)
        if rendered is None:
            return await _safe_ephemeral(
                interaction,
                "Your current privacy settings hide every optional signature detail.",
                ok=True,
            )
        rendered.embed.set_footer(text="Preview only • compact signature • nothing was posted publicly")
        payload: dict[str, Any] = {
            "embed": rendered.embed,
            "view": rendered.view,
        }
        if rendered.file is not None:
            payload["file"] = rendered.file
        await _send_private(interaction, **payload)
''',
    '''class _BackToPrivacyButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Back to Privacy",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:profilecard:v3:preview_back_privacy",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await profile_settings(interaction)


class _BackToSignatureButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Back to Profile",
            emoji="🪪",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:profilecard:v3:back_signature",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.profile_signature_studio import open_profile_signature_studio

        await open_profile_signature_studio(interaction, replace=True)


class _ManagePlatformsButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(
            label="Manage Accounts",
            emoji="🎮",
            style=discord.ButtonStyle.success,
            custom_id="dank:profilecard:v3:manage_platforms",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.profile_signature_studio import open_platform_manager

        await open_platform_manager(interaction, replace=True)


class _ProfilePreviewView(discord.ui.View):
    def __init__(self, *, author_id: int, source_view: Optional[discord.ui.View]) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button) or not child.url:
                continue
            self.add_item(
                discord.ui.Button(
                    label=str(child.label or "Profile")[:80],
                    emoji=child.emoji,
                    style=discord.ButtonStyle.link,
                    url=str(child.url),
                )
            )
        self.add_item(_BackToPrivacyButton())
        self.add_item(_BackToSignatureButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _safe_ephemeral(interaction, "Only the member who opened this preview can use it.", ok=False)
            return False
        return True


class _PreviewProfileButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Preview Signature",
            emoji="👀",
            style=discord.ButtonStyle.primary,
            custom_id="dank:profilecard:v3:preview_compact",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _safe_ephemeral(interaction, "Could not resolve your server member.", ok=False)
        await _defer_private(interaction, component_update=True)
        try:
            config = await get_guild_config(view.guild_id)
            allowed = set(parse_live_card_config(config).allowed_fields)
            rendered = await render_live_profile_card(
                member,
                allowed,
                trigger_message_id=0,
                require_live_enabled=False,
            )
        except ProfileStorageUnavailable:
            return await interaction.edit_original_response(
                content="❌ Private profile storage is unavailable.",
                embed=None,
                view=ProfileSettingsView(
                    author_id=view.author_id,
                    guild_id=view.guild_id,
                    user_preferences={},
                    guild_settings={},
                ),
                attachments=[],
            )
        if rendered is None:
            return await profile_settings(interaction)
        rendered.embed.set_footer(text="Preview only • compact signature • nothing was posted publicly")
        payload: dict[str, Any] = {
            "content": None,
            "embed": rendered.embed,
            "view": _ProfilePreviewView(author_id=view.author_id, source_view=rendered.view),
            "attachments": [rendered.file] if rendered.file is not None else [],
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        await interaction.edit_original_response(**payload)
''',
    label="privacy preview lifecycle",
)
replace_once(
    public,
    '''        for label, key, emoji in specs:
            self.add_item(_core._GuildPrivacyToggleButton(label, key, local_values, emoji, 1))
        self.add_item(_PreviewProfileButton(row=2))
''',
    '''        for label, key, emoji in specs:
            self.add_item(_core._GuildPrivacyToggleButton(label, key, local_values, emoji, 1))
        self.add_item(_ManagePlatformsButton(row=2))
        self.add_item(_PreviewProfileButton(row=2))
        self.add_item(_BackToSignatureButton(row=2))
''',
    label="privacy navigation buttons",
)
replace_once(
    public,
    '''    await _defer_private(interaction)
    try:
        user_row, guild_row, effective = await _settings_payload(guild.id, member.id)
''',
    '''    component_update = getattr(interaction, "type", None) == discord.InteractionType.component
    await _defer_private(interaction, component_update=component_update)
    try:
        user_row, guild_row, effective = await _settings_payload(guild.id, member.id)
''',
    label="profile settings component defer",
)
replace_once(
    public,
    '''    await _send_private(
        interaction,
        embed=_settings_embed(member, user_row, guild_row, effective),
        view=ProfileSettingsView(
            author_id=member.id,
            guild_id=guild.id,
            user_preferences=dict(user_row.get("preferences") or {}),
            guild_settings=dict(guild_row.get("settings") or {}),
        ),
    )
''',
    '''    await interaction.edit_original_response(
        content=None,
        embed=_settings_embed(member, user_row, guild_row, effective),
        view=ProfileSettingsView(
            author_id=member.id,
            guild_id=guild.id,
            user_preferences=dict(user_row.get("preferences") or {}),
            guild_settings=dict(guild_row.get("settings") or {}),
        ),
        attachments=[],
        allowed_mentions=discord.AllowedMentions.none(),
    )
''',
    label="profile settings completes deferred response",
)

studio = Path("stoney_verify/profile_signature_studio.py")
replace_once(
    studio,
    '''async def _defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
''',
    '''async def _edit_private(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    file: Optional[discord.File] = None,
) -> None:
    payload: dict[str, Any] = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "attachments": [file] if file is not None else [],
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.edit_message(**payload)
    else:
        await interaction.edit_original_response(**payload)


async def _defer(interaction: discord.Interaction, *, component_update: bool = False) -> None:
    if interaction.response.is_done():
        return
    if component_update:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)
''',
    label="studio edit and defer helpers",
)
replace_once(
    studio,
    '''async def _preview(interaction: discord.Interaction, *, member: Optional[discord.Member] = None) -> None:
    target = member or _member(interaction)
    guild = interaction.guild
    if target is None or guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    await _defer(interaction)
    try:
        config = parse_live_card_config(await get_guild_config(guild.id, refresh=True))
        rendered = await render_live_profile_card(
            target,
            set(config.allowed_fields),
            trigger_message_id=0,
            require_live_enabled=False,
        )
    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable.")
    if rendered is None:
        return await _private(
            interaction,
            content="Your privacy settings hide every optional detail. Turn on at least one detail to preview a signature.",
        )
    rendered.embed.set_footer(text="Preview only • compact profile signature")
    await _private(interaction, embed=rendered.embed, view=rendered.view, file=rendered.file)
''',
    '''class SignaturePreviewView(discord.ui.View):
    def __init__(self, *, author_id: int, source_view: Optional[discord.ui.View]) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button) or not child.url:
                continue
            self.add_item(
                discord.ui.Button(
                    label=str(child.label or "Profile")[:80],
                    emoji=child.emoji,
                    style=discord.ButtonStyle.link,
                    url=str(child.url),
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _private(interaction, content="❌ Only the member who opened this preview can use it.")
            return False
        return True

    @discord.ui.button(label="Back to Profile", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_profile_signature_studio(interaction, replace=True)


async def _preview(
    interaction: discord.Interaction,
    *,
    member: Optional[discord.Member] = None,
    notice: str = "",
) -> None:
    target = member or _member(interaction)
    guild = interaction.guild
    if target is None or guild is None:
        return await _private(interaction, content="❌ Use this inside a server as a member.")
    component_update = getattr(interaction, "type", None) == discord.InteractionType.component
    await _defer(interaction, component_update=component_update)
    try:
        config = parse_live_card_config(await get_guild_config(guild.id, refresh=True))
        rendered = await render_live_profile_card(
            target,
            set(config.allowed_fields),
            trigger_message_id=0,
            require_live_enabled=False,
        )
    except ProfileStorageUnavailable:
        return await _edit_private(interaction, content="❌ Private profile storage is unavailable.")
    if rendered is None:
        return await _edit_private(
            interaction,
            content="Your live signature is currently unavailable. Return to Profile Privacy and check Live Signature.",
            view=SignatureStudioView(author_id=target.id),
        )
    rendered.embed.set_footer(text="Preview only • compact profile signature • nothing posted publicly")
    await _edit_private(
        interaction,
        content=notice,
        embed=rendered.embed,
        view=SignaturePreviewView(author_id=target.id, source_view=rendered.view),
        file=rendered.file,
    )
''',
    label="studio preview completion",
)
replace_once(
    studio,
    '''    except ProfileStorageUnavailable:
        return await _private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
    await _private(interaction, content=f"✅ {message}")
    await _preview(interaction, member=member)
''',
    '''    except ProfileStorageUnavailable:
        return await _edit_private(interaction, content="❌ Private profile storage is unavailable. Nothing changed.")
    await _preview(interaction, member=member, notice=f"✅ {message}")
''',
    label="member style save completion",
)
replace_once(
    studio,
    '''    await upsert_guild_config(guild.id, dict(updates))
    await _invalidate_guild(interaction)
    await _private(interaction, content=f"✅ {message}")
    member = _member(interaction)
    if member is not None:
        await _preview(interaction, member=member)
''',
    '''    await upsert_guild_config(guild.id, dict(updates))
    await _invalidate_guild(interaction)
    member = _member(interaction)
    if member is not None:
        await _preview(interaction, member=member, notice=f"✅ {message}")
    else:
        await _edit_private(interaction, content=f"✅ {message}")
''',
    label="server style save completion",
)
replace_once(
    studio,
    '''class PlatformDetailView(discord.ui.View):
    def __init__(self, *, author_id: int, platform: str) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.platform = str(platform)
''',
    '''def _platform_detail_embed(platform: str, entry: Mapping[str, Any]) -> discord.Embed:
    spec = PLATFORM_SPECS[platform]
    username = str(entry.get("username") or "").strip()
    shared = bool(entry.get("shared")) and bool(username)
    embed = discord.Embed(
        title=f"{spec.emoji} {spec.label}",
        description=(
            "Use the large visibility button below. **Make Public** allows this account to appear on your compact "
            "signature; **Make Private** hides it everywhere."
        ),
        color=discord.Color.green() if shared else discord.Color.blurple(),
    )
    embed.add_field(
        name="Username",
        value=f"`{display_profile_username(username)}`" if username else "Not saved yet",
        inline=False,
    )
    embed.add_field(name="Visibility", value="🌐 Public" if shared else "🔒 Private", inline=True)
    embed.add_field(name="Official link", value="Saved" if entry.get("url") else "Username only", inline=True)
    return embed


class PlatformDetailView(discord.ui.View):
    def __init__(self, *, author_id: int, platform: str, entry: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        self.platform = str(platform)
        raw = dict(entry or {})
        has_identity = bool(str(raw.get("username") or "").strip())
        shared = bool(raw.get("shared")) and has_identity
        self.share.label = "Make Private" if shared else "Make Public"
        self.share.emoji = "🔒" if shared else "🌐"
        self.share.style = discord.ButtonStyle.danger if shared else discord.ButtonStyle.success
        self.share.disabled = not has_identity
''',
    label="dynamic platform detail view",
)
replace_once(
    studio,
    '''            content=(
                f"✅ {spec.label} saved as `{display_profile_username(entry['username'])}`. "
                f"It is currently **{'shared' if entry['shared'] else 'private'}**."
            ),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform),
        )
''',
    '''            content=f"✅ {spec.label} saved. Choose **Make Public** when you want it shown on your signature.",
            embed=_platform_detail_embed(self.platform, entry),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),
        )
''',
    label="platform modal result clarity",
)
replace_once(
    studio,
    '''    @discord.ui.button(label="Share / Hide", emoji="👁️", style=discord.ButtonStyle.success, row=0)
''',
    '''    @discord.ui.button(label="Make Public", emoji="🌐", style=discord.ButtonStyle.success, row=0)
''',
    label="visibility button wording",
)
replace_once(
    studio,
    '''        await _private(
            interaction,
            content=f"✅ {PLATFORM_SPECS[self.platform].label} is now **{'shared' if entry['shared'] else 'private'}**.",
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform),
        )
''',
    '''        await _edit_private(
            interaction,
            content=(
                f"✅ {PLATFORM_SPECS[self.platform].label} is now "
                f"**{'Public' if entry['shared'] else 'Private'}**."
            ),
            embed=_platform_detail_embed(self.platform, entry),
            view=PlatformDetailView(author_id=self.author_id, platform=self.platform, entry=entry),
        )
''',
    label="visibility toggle result",
)
replace_once(
    studio,
    '''        await _private(
            interaction,
            content=(
                f"✅ Removed {PLATFORM_SPECS[self.platform].label}."
                if removed
                else f"No {PLATFORM_SPECS[self.platform].label} profile was saved."
            ),
            view=PlatformManagerView(author_id=self.author_id),
        )
''',
    '''        await open_platform_manager(interaction, replace=True)
''',
    label="remove returns to manager",
)
replace_once(
    studio,
    '''        await open_platform_manager(interaction)
''',
    '''        await open_platform_manager(interaction, replace=True)
''',
    label="platform detail back navigation",
)
replace_once(
    studio,
    '''        spec = PLATFORM_SPECS[platform]
        embed = discord.Embed(
            title=f"{spec.emoji} {spec.label}",
            description=(
                "Add or edit the username, then use **Share / Hide** to control whether it appears on your signature. "
                "Links are accepted only for supported official profile pages."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Username", value=f"`{display_profile_username(entry['username'])}`" if entry.get("username") else "Not saved", inline=False)
        embed.add_field(name="Visibility", value="Shared" if entry.get("shared") else "Private", inline=True)
        embed.add_field(name="Official link", value="Saved" if entry.get("url") else "None", inline=True)
        await _private(
            interaction,
            embed=embed,
            view=PlatformDetailView(author_id=view.author_id, platform=platform),
        )
''',
    '''        await _edit_private(
            interaction,
            embed=_platform_detail_embed(platform, entry),
            view=PlatformDetailView(author_id=view.author_id, platform=platform, entry=entry),
        )
''',
    label="platform selection single panel",
)
replace_once(
    studio,
    '''        await open_profile_signature_studio(interaction)
''',
    '''        await open_profile_signature_studio(interaction, replace=True)
''',
    label="manager back navigation",
)
replace_once(
    studio,
    '''async def open_platform_manager(interaction: discord.Interaction) -> None:
''',
    '''async def open_platform_manager(interaction: discord.Interaction, *, replace: bool = False) -> None:
''',
    label="platform manager replace parameter",
)
replace_once(
    studio,
    '''            f"{spec.emoji} **{spec.label}:** `{display_profile_username(raw.get('username'))}` — "
            f"{'shared' if raw.get('shared') else 'private'}"
''',
    '''            f"{spec.emoji} **{spec.label}:** `{display_profile_username(raw.get('username'))}` — "
            f"{'🌐 Public' if raw.get('shared') else '🔒 Private'}"
''',
    label="manager visibility labels",
)
replace_once(
    studio,
    '''        description=(
            "Choose a platform below. Saving an account does **not** share it automatically; "
            "you control visibility with **Share / Hide**."
        ),
''',
    '''        description=(
            "Choose an account below. The next screen gives you an obvious **Make Public** or **Make Private** "
            "button. Saving a username never exposes it automatically."
        ),
''',
    label="manager instructions",
)
replace_once(
    studio,
    '''    embed.add_field(name="Saved accounts", value="\\n".join(lines)[:1024] if lines else "None saved yet.", inline=False)
    await _private(interaction, embed=embed, view=PlatformManagerView(author_id=member.id))
''',
    '''    embed.add_field(name="Saved accounts", value="\\n".join(lines)[:1024] if lines else "None saved yet.", inline=False)
    panel = PlatformManagerView(author_id=member.id)
    if replace:
        await _edit_private(interaction, embed=embed, view=panel)
    else:
        await _private(interaction, embed=embed, view=panel)
''',
    label="manager replace rendering",
)
replace_once(
    studio,
    '''        await open_platform_manager(interaction)
''',
    '''        await open_platform_manager(interaction, replace=True)
''',
    label="signature platforms navigation",
)
replace_once(
    studio,
    '''async def open_profile_signature_studio(interaction: discord.Interaction) -> None:
''',
    '''async def open_profile_signature_studio(interaction: discord.Interaction, *, replace: bool = False) -> None:
''',
    label="signature studio replace parameter",
)
replace_once(
    studio,
    '''    await _private(interaction, embed=embed, view=SignatureStudioView(author_id=member.id))
''',
    '''    panel = SignatureStudioView(author_id=member.id)
    if replace:
        await _edit_private(interaction, embed=embed, view=panel)
    else:
        await _private(interaction, embed=embed, view=panel)
''',
    label="signature studio replace rendering",
)

Path("tests/test_profile_platform_privacy_preview_ux.py").write_text(
    '''from __future__ import annotations

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
        private_button = next(child for child in private_view.children if child.callback.__name__ == "share")
        public_button = next(child for child in public_view.children if child.callback.__name__ == "share")
        assert private_button.label == "Make Public"
        assert private_button.disabled is False
        assert public_button.label == "Make Private"
        assert public_button.disabled is False


def test_unsaved_platform_cannot_be_published_before_username_exists():
    view = profile_signature_studio.PlatformDetailView(author_id=42, platform="steam", entry={})
    button = next(child for child in view.children if child.callback.__name__ == "share")
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
    assert "await _preview(interaction, member=member, notice=f\"✅ {message}\")" in studio_source
    assert "class SignaturePreviewView" in studio_source
''',
    encoding="utf-8",
)

active = Path("ACTIVE_TASK.md")
active.write_text(
    '''# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-008 — Simplify platform visibility and finish preview navigation

**Status:** ROOT CAUSE CONFIRMED / IMPLEMENTATION VALIDATION REQUIRED
**Branch:** `fix/profile-platform-privacy-preview-ux`
**PR:** pending
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until the deployed Profile Signature flow posts live cards, clearly allows every saved platform to become Public or Private, and every preview/style save completes without a stuck loading state.

## Confirmed findings

- Profile Privacy displayed saved Steam/Xbox accounts but offered no button to manage their individual visibility.
- The actual visibility control was hidden in the separate Platforms screen under the ambiguous label **Share / Hide**.
- Privacy exposed eight similarly styled global/server buttons with no Back or Manage Accounts action, making the mobile panel difficult to understand.
- Preview and style-save callbacks deferred a loading response, then sent follow-up messages instead of completing the deferred response.
- Platform manager/detail navigation stacked new ephemeral messages rather than replacing one mobile-friendly panel.

## Scope

- Add an obvious **Manage Accounts** action to Profile Privacy.
- Replace **Share / Hide** with state-aware **Make Public** / **Make Private** for every platform.
- Mark saved identities as `🌐 Public` or `🔒 Private` in every summary.
- Add Back navigation between Privacy, Platforms, Preview, and the Signature home.
- Complete deferred preview/style-save responses by editing the original ephemeral panel.
- Keep account saves private by default and require a username before Public can be enabled.

## Validation

- [ ] Privacy panel exposes Manage Accounts, Preview Signature, and Back to Profile.
- [ ] Every platform detail screen shows Make Public or Make Private based on saved state.
- [ ] Unsaved identities cannot be made Public.
- [ ] Privacy summaries clearly mark Steam and all other platforms Public/Private.
- [ ] Preview and style-save callbacks complete the original deferred response.
- [ ] Focused tests and changed-module compilation pass.
- [ ] Full unit suite and repository audits pass on exact clean head.
- [ ] Branch is conflict-free with current `main`.
- [ ] Deployed Discord smoke confirms Steam can be made Public and Preview returns without hanging.

## Cleanup

- Temporary materialization workflow/script removed before final validation.
- No duplicate privacy panel, compatibility fork, or temporary runtime path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
''',
    encoding="utf-8",
)

print("materialized profile platform privacy and preview UX correction")
