from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, stoney_group


SELF_ROLE_PREFIX = "dank:selfrole:v1:"
PROFILE_PREFIX = "dank:profile:v1:"

_ATTACHED = False
_LISTENER_ATTACHED = False

_PROFILE_PANEL_HARD_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
_PROFILE_PANEL_BLOCK_UNTIL: dict[tuple[int, int], float] = {}
_PROFILE_PANEL_LAST_NOTICE: dict[tuple[int, int], float] = {}
_PROFILE_PANEL_HARD_COOLDOWN_SECONDS = 2.5
_PROFILE_PANEL_NOTICE_SECONDS = 3.0
_PROFILE_PANEL_SESSIONS: dict[tuple[int, int, str], float] = {}
_PROFILE_PANEL_SESSION_TTL_SECONDS = 45.0

_PROFILE_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
_PROFILE_LAST_CLICK: dict[tuple[int, int], float] = {}
_PROFILE_COOLDOWN_SECONDS = 1.0


DEFAULT_PRONOUN_ROLE_NAMES: tuple[str, ...] = (
    "Pronouns: he/him",
    "Pronouns: she/her",
    "Pronouns: they/them",
    "Pronouns: he/they",
    "Pronouns: she/they",
    "Pronouns: it/its",
    "Pronouns: any pronouns",
    "Pronouns: no pronouns",
    "Pronouns: ask me",
)

DEFAULT_IDENTITY_ROLE_NAMES: tuple[str, ...] = (
    "Identity: man",
    "Identity: woman",
    "Identity: non-binary",
    "Identity: genderfluid",
    "Identity: agender",
    "Identity: trans",
    "Identity: questioning",
    "Identity: prefer not to say",
)

DEFAULT_INTEREST_ROLE_NAMES: tuple[str, ...] = (
    "Interest: gaming",
    "Interest: memes",
    "Interest: music",
    "Interest: movies",
    "Interest: anime",
    "Interest: smoke lounge",
    "Interest: late-night chat",
)

PROFILE_CATEGORIES: dict[str, tuple[str, str, tuple[str, ...], str]] = {
    "pronouns": (
        "🪪",
        "Pronouns",
        DEFAULT_PRONOUN_ROLE_NAMES,
        "Choose the pronoun roles you want shown. You can select more than one.",
    ),
    "identity": (
        "🌈",
        "Identity",
        DEFAULT_IDENTITY_ROLE_NAMES,
        "Choose optional identity roles. These are cosmetic only and never control access.",
    ),
    "interests": (
        "🎮",
        "Interests",
        DEFAULT_INTEREST_ROLE_NAMES,
        "Choose interests so other members know what you like talking about. These do not ping you or unlock access.",
    ),
}

profile_group = app_commands.Group(
    name="profile",
    description="Build and use member profile panels.",
)

roles_group = app_commands.Group(
    name="roles",
    description="Advanced role-panel tools.",
)


def _role_name_key(name: str) -> str:
    return str(name or "").strip().casefold()


def _find_role_by_name(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    target = _role_name_key(name)
    for role in list(getattr(guild, "roles", []) or []):
        if isinstance(role, discord.Role) and _role_name_key(role.name) == target:
            return role
    return None


def _can_manage(role: discord.Role, guild: discord.Guild) -> tuple[bool, str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
            return False, "Dank Shield is missing Manage Roles."
        if role >= me.top_role:
            return False, f"Move Dank Shield's role above {role.mention}."
        if role.is_default() or role.managed:
            return False, f"{role.mention} cannot be member-assigned."
    except Exception:
        return False, "Discord role hierarchy could not be checked."
    return True, ""


def _bot_can_create_roles(guild: discord.Guild) -> tuple[bool, str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
            return False, "Dank Shield is missing Manage Roles."
    except Exception:
        return False, "Discord role permissions could not be checked."
    return True, ""


async def _ensure_role(guild: discord.Guild, name: str, *, reason: str) -> discord.Role:
    existing = _find_role_by_name(guild, name)
    if isinstance(existing, discord.Role):
        return existing
    return await guild.create_role(name=name[:100], mentionable=False, reason=reason)


async def _reply(interaction: discord.Interaction, content: str, *, ok: bool = True) -> None:
    prefix = "✅ " if ok else "❌ "
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                prefix + content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                prefix + content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


def _all_profile_role_names() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for _key, payload in PROFILE_CATEGORIES.items():
        for name in payload[2]:
            clean = str(name or "").strip()
            if clean and clean.casefold() not in seen:
                seen.add(clean.casefold())
                out.append(clean)
    return tuple(out)


def _category_roles(guild: discord.Guild, category_key: str) -> list[discord.Role]:
    payload = PROFILE_CATEGORIES.get(str(category_key or "").lower())
    if not payload:
        return []
    roles: list[discord.Role] = []
    for name in payload[2]:
        role = _find_role_by_name(guild, name)
        if isinstance(role, discord.Role):
            roles.append(role)
    return roles


def _short_role_label(role_name: str) -> str:
    text = str(role_name or "").strip()
    for prefix in ("Pronouns: ", "Identity: "):
        if text.casefold().startswith(prefix.casefold()):
            text = text[len(prefix):]
            break
    return text[:100] or "Role"


def _profile_channel_missing(guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Resolve bot member"]

    perms = channel.permissions_for(me)
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    return missing


def _profile_manual_blockers(guild: discord.Guild) -> list[str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield bot member could not be resolved."]

    blockers: list[str] = []
    if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
        blockers.append("Dank Shield is missing Manage Roles.")

    for role_name in _all_profile_role_names():
        role = _find_role_by_name(guild, role_name)
        if isinstance(role, discord.Role):
            ok, why = _can_manage(role, guild)
            if not ok:
                blockers.append(why)
                break

    return blockers


def _profile_can_fix_channel(guild: discord.Guild) -> bool:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    return bool(me.guild_permissions.manage_channels or me.guild_permissions.administrator)


def _profile_builder_status(guild: discord.Guild, channel: discord.TextChannel) -> tuple[bool, list[str], list[str]]:
    manual = _profile_manual_blockers(guild)
    channel_missing = _profile_channel_missing(guild, channel)
    fixable = channel_missing if channel_missing and _profile_can_fix_channel(guild) else []

    if channel_missing and not fixable:
        manual.append(f"Missing in {channel.mention}: " + ", ".join(channel_missing))

    ready = not manual and not channel_missing
    return ready, fixable, manual


def _member_profile_roles(member: discord.Member, names: tuple[str, ...]) -> list[discord.Role]:
    keys = {_role_name_key(name) for name in names}
    return [role for role in member.roles if _role_name_key(role.name) in keys]


def _role_labels(roles: list[discord.Role]) -> str:
    if not roles:
        return "Not set"
    return ", ".join(_short_role_label(role.name) for role in roles)[:1024]


def _profile_card(member: discord.Member) -> discord.Embed:
    pronouns = _member_profile_roles(member, DEFAULT_PRONOUN_ROLE_NAMES)
    identity = _member_profile_roles(member, DEFAULT_IDENTITY_ROLE_NAMES)
    interests = _member_profile_roles(member, DEFAULT_INTEREST_ROLE_NAMES)

    embed = discord.Embed(
        title=f"{member.display_name}'s Profile",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="🪪 Pronouns", value=_role_labels(pronouns), inline=False)
    embed.add_field(name="🌈 Identity", value=_role_labels(identity), inline=False)
    embed.add_field(name="🎮 Interests", value=_role_labels(interests), inline=False)
    embed.add_field(name="Profile roles", value=str(len(pronouns) + len(identity) + len(interests)), inline=True)

    if member.joined_at:
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="D"), inline=True)
    embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)

    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    embed.set_footer(text="Dank Shield profile")
    return embed


def _profile_panel_embed(guild: discord.Guild, *, title: str = "Profile Panel") -> discord.Embed:
    embed = discord.Embed(
        title=title[:256],
        description=(
            "Customize your server profile with optional pronoun, identity, and interest roles.\\n\\n"
            "These roles are cosmetic only. They never control verification, tickets, moderation, staff access, or server permissions."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Available profile sections",
        value="\\n".join(
            f"{emoji} **{label}**"
            for _key, (emoji, label, _names, _desc) in PROFILE_CATEGORIES.items()
        ),
        inline=False,
    )
    embed.add_field(
        name="Missing Identity?",
        value=(
            "Use **Missing Identity?** only when your identity is not listed. "
            "This sends a staff review request and does not create or assign a role automatically."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield profile panel")
    return embed


class ProfilePanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="View My Profile", emoji="👤", style=discord.ButtonStyle.primary, custom_id=f"{PROFILE_PREFIX}view", row=0))
        self.add_item(discord.ui.Button(label="Pronouns", emoji="🪪", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:pronouns", row=1))
        self.add_item(discord.ui.Button(label="Identity", emoji="🌈", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:identity", row=1))
        self.add_item(discord.ui.Button(label="Interests", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:interests", row=2))
        self.add_item(discord.ui.Button(label="Suggest Missing Interest", emoji="➕", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}missing_interest", row=2))
        self.add_item(discord.ui.Button(label="Clear Profile Roles", emoji="🧹", style=discord.ButtonStyle.danger, custom_id=f"{PROFILE_PREFIX}clear", row=3))
        self.add_item(discord.ui.Button(label="Missing Identity?", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}missing", row=3))


class ProfileCategorySelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, member: discord.Member, category_key: str) -> None:
        super().__init__(timeout=300)

        options: list[discord.SelectOption] = []
        for role in _category_roles(guild, category_key)[:25]:
            options.append(
                discord.SelectOption(
                    label=_short_role_label(role.name),
                    value=str(role.id),
                    default=role in member.roles,
                )
            )

        if not options:
            options.append(discord.SelectOption(label="No roles available", value="0"))

        self.add_item(
            discord.ui.Select(
                placeholder="Pick your choices…",
                min_values=0,
                max_values=max(1, len(options)),
                options=options,
                custom_id=f"{PROFILE_PREFIX}select:{category_key}",
            )
        )


class MissingIdentityModal(discord.ui.Modal, title="Missing Identity?"):
    label = discord.ui.TextInput(
        label="Identity missing from the list",
        placeholder="Example: genderqueer, demigirl, Two-Spirit, questioning, etc.",
        min_length=2,
        max_length=80,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        clean = str(self.label.value or "").replace("@everyone", "everyone").replace("@here", "here").strip()
        clean = " ".join(clean.split())[:80]
        if not clean:
            return await _reply(interaction, "Missing identity label was empty.", ok=False)

        channel = await _staff_review_channel(guild)
        if not isinstance(channel, discord.TextChannel):
            return await _reply(
                interaction,
                "No staff/modlog channel found for Missing Identity requests. Set a modlog channel first.",
                ok=False,
            )

        embed = discord.Embed(
            title="🪪 Missing Identity Request",
            description="A member says their identity is missing from the profile list. Staff review is required.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member.mention}\\n`{member}` (`{member.id}`)", inline=False)
        embed.add_field(name="Requested label", value=f"`{clean}`", inline=False)
        embed.add_field(
            name="Important",
            value="This request does not create or assign a role automatically. Approve manually only if appropriate for this server.",
            inline=False,
        )
        embed.set_footer(text="Dank Shield profile builder")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await _reply(interaction, f"Missing Identity request sent to staff: `{clean}`", ok=True)
        except Exception as exc:
            await _reply(interaction, f"Could not send Missing Identity request: {type(exc).__name__}.", ok=False)


async def _staff_review_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.commands_ext import public_modlog_group as modlog

        cfg = await get_guild_config(int(guild.id), refresh=True)
        channel = modlog._modlog_channel(guild, cfg)
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass

    for name in ("mod-log", "modlog", "staff-log", "staff", "support"):
        for channel in list(getattr(guild, "text_channels", []) or []):
            raw = str(getattr(channel, "name", "") or "").lower().replace("_", "-").replace(" ", "-")
            if name in raw and isinstance(channel, discord.TextChannel):
                return channel
    return None


BANNED_INTEREST_WORDS: set[str] = {
    "admin", "mod", "moderator", "staff", "owner", "manager",
    "everyone", "here", "discord", "nitro",
}


def _clean_missing_interest(value: Any) -> tuple[str, str | None]:
    raw = str(value or "").strip().lower()
    raw = raw.replace("@everyone", "everyone").replace("@here", "here")
    raw = " ".join(raw.split())
    raw = re.sub(r"[^a-z0-9 #+&/.-]", "", raw).strip(" .-/")
    raw = raw[:32]

    if len(raw) < 2:
        return "", "Interest is too short."
    if "http://" in raw or "https://" in raw or "discord.gg" in raw:
        return "", "Links are not allowed."
    if any(word in raw.split() for word in BANNED_INTEREST_WORDS):
        return "", "That interest name is reserved."
    return raw, None


class MissingInterestModal(discord.ui.Modal, title="Suggest Missing Interest"):
    interest = discord.ui.TextInput(
        label="Interest missing from the list",
        placeholder="Example: cars, art, cooking, sports",
        min_length=2,
        max_length=32,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        clean, error = _clean_missing_interest(str(self.interest.value or ""))
        if error:
            return await _reply(interaction, error, ok=False)

        channel = await _staff_review_channel(guild)
        if not isinstance(channel, discord.TextChannel):
            return await _reply(interaction, "No staff/modlog channel found for missing interest requests.", ok=False)

        embed = discord.Embed(
            title="🎮 Missing Interest Request",
            description=(
                "A member says this interest is missing from the profile list.\\n\\n"
                "This does not create a role automatically."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member.mention}\\n`{member}` (`{member.id}`)", inline=False)
        embed.add_field(name="Requested interest", value=f"`{clean}`", inline=False)
        embed.add_field(
            name="Safe staff action",
            value=(
                "Approve manually only if appropriate. "
                "Recommended role name if approved: "
                f"`Interest: {clean}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Dank Shield profile builder • staff-reviewed request")

        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await _reply(interaction, f"Missing interest request sent to staff: `{clean}`", ok=True)


class ProfileBuilderView(discord.ui.View):
    def __init__(self, *, author_id: int, ready: bool, fixable: bool, title: str) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.title_text = str(title or "Profile Panel")[:80]

        if ready:
            self.add_item(discord.ui.Button(label="Create / Repair Roles + Post Panel", emoji="🌿", style=discord.ButtonStyle.success, custom_id=f"{PROFILE_PREFIX}builder:post", row=0))
            self.add_item(discord.ui.Button(label="Preview Panel", emoji="👀", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}builder:preview", row=0))
        elif fixable:
            self.add_item(discord.ui.Button(label="Fix Channel Permissions", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id=f"{PROFILE_PREFIX}builder:fix", row=0))

        self.add_item(discord.ui.Button(label="Health", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}builder:health", row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _reply(interaction, "Only the staff member who opened this builder can use it.", ok=False)
            return False
        return True


async def _create_profile_roles(interaction: discord.Interaction, guild: discord.Guild) -> tuple[list[discord.Role], list[str], list[str]]:
    roles: list[discord.Role] = []
    created: list[str] = []
    reused: list[str] = []

    for name in _all_profile_role_names():
        before = _find_role_by_name(guild, name)
        role = await _ensure_role(
            guild,
            name,
            reason=f"Dank Shield profile builder setup by {interaction.user} ({interaction.user.id})",
        )
        ok, why = _can_manage(role, guild)
        if not ok:
            raise RuntimeError(why)
        roles.append(role)
        (reused if before else created).append(role.name)

    return roles, created, reused


async def _post_profile_builder(interaction: discord.Interaction, *, title: str = "Profile Panel") -> None:
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel):
        return await _reply(interaction, "Run this inside the text channel where the profile panel should be posted.", ok=False)

    ready, fixable, manual = _profile_builder_status(guild, channel)

    embed = discord.Embed(
        title="🌿 Profile Builder",
        description=(
            "**Nothing has been created or posted yet.**\\n\\n"
            "This builder uses the current channel as the panel target. "
            "When ready, press **Create / Repair Roles + Post Panel**."
        ),
        color=discord.Color.green() if ready else discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Panel target", value=channel.mention, inline=False)
    embed.add_field(name="Default profile sections", value="🪪 Pronouns\\n🌈 Identity\\n🎮 Interests\\n✍️ Missing Identity request\\n➕ Missing Interest request", inline=False)
    embed.add_field(name="Status", value="✅ Ready" if ready else "⚠️ Not ready", inline=False)

    if fixable:
        embed.add_field(name="Bot can fix now", value="\\n".join(f"• {x}" for x in fixable), inline=False)
    if manual:
        embed.add_field(name="Needs manual fix", value="\\n".join(f"• {x}" for x in manual), inline=False)

    await interaction.response.send_message(
        embed=embed,
        view=ProfileBuilderView(
            author_id=int(interaction.user.id),
            ready=ready,
            fixable=bool(fixable),
            title=title,
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _handle_builder_action(interaction: discord.Interaction, action: str) -> bool:
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel):
        await _reply(interaction, "Run builder actions inside the panel target channel.", ok=False)
        return True

    ready, fixable, manual = _profile_builder_status(guild, channel)

    if action == "health":
        lines: list[str] = []
        if ready:
            lines.append("Ready.")
        if fixable:
            lines.append("Fixable: " + ", ".join(fixable))
        if manual:
            lines.append("Manual: " + " | ".join(manual))
        await _reply(interaction, "\\n".join(lines) if lines else "No status found.", ok=ready)
        return True

    if action == "fix":
        if not fixable:
            await _reply(interaction, "No fixable channel permissions are missing right now.", ok=True)
            return True

        me = guild.me
        if not isinstance(me, discord.Member):
            await _reply(interaction, "Dank Shield bot member could not be resolved.", ok=False)
            return True

        try:
            await channel.set_permissions(
                me,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                reason=f"Dank Shield profile builder permission repair by {interaction.user} ({interaction.user.id})",
            )
            await _reply(interaction, "Fixed channel permissions. Run `/dank profile builder` again.", ok=True)
        except Exception as exc:
            await _reply(interaction, f"Could not fix channel permissions: {type(exc).__name__}.", ok=False)
        return True

    if action == "preview":
        await interaction.response.send_message(
            embed=_profile_panel_embed(guild),
            view=ProfilePanelView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if action == "post":
        if not ready:
            await _reply(interaction, "Builder is not ready. Use Health to see the exact blocker.", ok=False)
            return True

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass

        try:
            _roles, created, reused = await _create_profile_roles(interaction, guild)
            await channel.send(
                embed=_profile_panel_embed(guild),
                view=ProfilePanelView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.followup.send(
                f"✅ Profile panel posted in {channel.mention}. Roles created: {len(created)}. Reused: {len(reused)}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Could not create/post profile panel: `{type(exc).__name__}: {str(exc)[:250]}`",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return True

    return False


def _profile_custom_id(interaction: discord.Interaction) -> str:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _is_profile_interaction(custom_id: str) -> bool:
    return custom_id.startswith(PROFILE_PREFIX) or custom_id.startswith(SELF_ROLE_PREFIX)


async def _acquire_profile_gate(interaction: discord.Interaction, custom_id: str) -> Optional[tuple[int, int]]:
    if not _is_profile_interaction(custom_id):
        return None

    guild_id = int(interaction.guild.id) if interaction.guild else 0
    user_id = int(interaction.user.id)
    key = (guild_id, user_id)
    now = monotonic()

    if len(_PROFILE_LAST_CLICK) > 5000:
        stale = [k for k, ts in _PROFILE_LAST_CLICK.items() if now - ts > 120]
        for stale_key in stale[:1000]:
            _PROFILE_LAST_CLICK.pop(stale_key, None)
            lock = _PROFILE_LOCKS.get(stale_key)
            if lock is not None and not lock.locked():
                _PROFILE_LOCKS.pop(stale_key, None)

    lock = _PROFILE_LOCKS.setdefault(key, asyncio.Lock())

    if lock.locked():
        await _reply(interaction, "Already updating your profile. Wait a second.", ok=False)
        return None

    last = _PROFILE_LAST_CLICK.get(key, 0.0)
    if now - last < _PROFILE_COOLDOWN_SECONDS:
        await _reply(interaction, "One profile action at a time. Try again in a second.", ok=False)
        return None

    _PROFILE_LAST_CLICK[key] = now
    await lock.acquire()
    return key


def _release_profile_gate(key: Optional[tuple[int, int]]) -> None:
    if key is None:
        return
    lock = _PROFILE_LOCKS.get(key)
    if lock is not None and lock.locked():
        lock.release()


async def _handle_profile_interaction(interaction: discord.Interaction) -> bool:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    custom_id = str(data.get("custom_id") or "")

    if not custom_id.startswith(PROFILE_PREFIX):
        return False

    suffix = custom_id[len(PROFILE_PREFIX):]

    if suffix.startswith("builder:"):
        return await _handle_builder_action(interaction, suffix.split(":", 1)[1])

    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None

    if guild is None or member is None:
        await _reply(interaction, "This only works inside the server.", ok=False)
        return True

    if suffix == "view":
        await interaction.response.send_message(
            embed=_profile_card(member),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix.startswith("open:"):
        category = suffix.split(":", 1)[1]
        payload = PROFILE_CATEGORIES.get(category)
        if not payload:
            await _reply(interaction, "That profile section no longer exists.", ok=False)
            return True
        emoji, label, _names, desc = payload
        await interaction.response.send_message(
            embed=discord.Embed(title=f"{emoji} {label}", description=desc, color=discord.Color.blurple()),
            view=ProfileCategorySelectView(guild, member, category),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix.startswith("select:"):
        category = suffix.split(":", 1)[1]
        raw_values = data.get("values") if isinstance(data.get("values"), list) else []
        selected = {int(value) for value in raw_values if str(value).isdigit() and int(value) > 0}
        category_roles = _category_roles(guild, category)

        to_add = [role for role in category_roles if int(role.id) in selected and role not in member.roles and _can_manage(role, guild)[0]]
        to_remove = [role for role in category_roles if int(role.id) not in selected and role in member.roles and _can_manage(role, guild)[0]]

        try:
            if to_add:
                await member.add_roles(*to_add, reason="Dank Shield profile picker")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Dank Shield profile picker")
        except Exception as exc:
            await _reply(interaction, f"Could not update your profile roles: {type(exc).__name__}.", ok=False)
            return True

        changes: list[str] = []
        if to_add:
            changes.append("Added: " + ", ".join(role.mention for role in to_add))
        if to_remove:
            changes.append("Removed: " + ", ".join(role.mention for role in to_remove))

        await _reply(interaction, "\\n".join(changes) if changes else "No profile changes needed.", ok=True)
        return True

    if suffix == "clear":
        roles: list[discord.Role] = []
        for category in PROFILE_CATEGORIES:
            roles.extend(
                role
                for role in _category_roles(guild, category)
                if role in member.roles and _can_manage(role, guild)[0]
            )
        if roles:
            try:
                await member.remove_roles(*roles, reason="Dank Shield profile clear")
                await _reply(interaction, "Removed your optional profile roles.", ok=True)
            except Exception as exc:
                await _reply(interaction, f"Could not clear your profile roles: {type(exc).__name__}.", ok=False)
        else:
            await _reply(interaction, "You do not have any optional profile roles from this panel.", ok=True)
        return True

    if suffix == "missing":
        await interaction.response.send_modal(MissingIdentityModal())
        return True

    if suffix == "missing_interest":
        await interaction.response.send_modal(MissingInterestModal())
        return True

    return True


async def _handle_self_role(interaction: discord.Interaction) -> bool:
    try:
        if interaction.type is not discord.InteractionType.component:
            return False
        data = interaction.data if isinstance(interaction.data, dict) else {}
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith(SELF_ROLE_PREFIX):
            return False
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "This only works inside the server.", ok=False)
            return True

        role_id = int(custom_id[len(SELF_ROLE_PREFIX):].split(":", 1)[0])
        role = interaction.guild.get_role(role_id)
        if not isinstance(role, discord.Role):
            await _reply(interaction, "That role no longer exists.", ok=False)
            return True

        ok, why = _can_manage(role, interaction.guild)
        if not ok:
            await _reply(interaction, why, ok=False)
            return True

        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Dank Shield advanced self-role toggle")
            await _reply(interaction, f"Removed {role.mention}.", ok=True)
        else:
            await interaction.user.add_roles(role, reason="Dank Shield advanced self-role toggle")
            await _reply(interaction, f"Added {role.mention}.", ok=True)
        return True
    except Exception as exc:
        await _reply(interaction, f"Self-role failed: {type(exc).__name__}.", ok=False)
        return True


def _panel_guard_custom_id(interaction: discord.Interaction) -> str:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _panel_guard_is_profile_action(custom_id: str) -> bool:
    text = str(custom_id or "")
    return (
        text.startswith("dank:profile:v1:")
        or text.startswith("dank:rolepicker:v2:")
        or text.startswith("dank:selfrole:v1:")
    )


async def _panel_guard_quiet_reject(interaction: discord.Interaction, key: tuple[int, int]) -> None:
    now = monotonic()
    last_notice = float(_PROFILE_PANEL_LAST_NOTICE.get(key, 0.0) or 0.0)
    _PROFILE_PANEL_LAST_NOTICE[key] = now

    # Show one clear warning, then quietly acknowledge repeat spam so Discord does not stack errors.
    if now - last_notice >= _PROFILE_PANEL_NOTICE_SECONDS:
        await _reply(interaction, "Already updating your profile. Wait a couple seconds.", ok=False)
        return

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=False)
    except Exception:
        pass


def _profile_action_key_from_custom_id(custom_id: str) -> str:
    text = str(custom_id or "")
    if text.startswith("dank:profile:v1:"):
        suffix = text[len("dank:profile:v1:"):]
        if suffix.startswith("open:"):
            return suffix
        if suffix in {"view", "clear", "missing", "missing_interest"}:
            return suffix
    if text.startswith("dank:rolepicker:v2:"):
        suffix = text[len("dank:rolepicker:v2:"):]
        if suffix.startswith("open:"):
            return suffix
        if suffix in {"clear", "custom"}:
            return suffix
    return text[:80]


async def _profile_session_gate(interaction: discord.Interaction, custom_id: str) -> bool:
    # Only suppress actions that create new ephemeral messages/views.
    action_key = _profile_action_key_from_custom_id(custom_id)
    suppress_prefixes = (
        "open:pronouns",
        "open:identity",
        "open:interests",
        "view",
    )
    if action_key not in suppress_prefixes:
        return True

    guild_id = int(interaction.guild.id) if interaction.guild else 0
    user_id = int(interaction.user.id)
    key = (guild_id, user_id, action_key)
    now = monotonic()

    if len(_PROFILE_PANEL_SESSIONS) > 5000:
        stale = [k for k, until in _PROFILE_PANEL_SESSIONS.items() if now > float(until or 0.0)]
        for stale_key in stale[:1000]:
            _PROFILE_PANEL_SESSIONS.pop(stale_key, None)

    active_until = float(_PROFILE_PANEL_SESSIONS.get(key, 0.0) or 0.0)
    if now < active_until:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=False)
        except Exception:
            pass
        return False

    _PROFILE_PANEL_SESSIONS[key] = now + _PROFILE_PANEL_SESSION_TTL_SECONDS
    return True


async def _panel_guard_acquire(interaction: discord.Interaction, custom_id: str) -> Optional[tuple[int, int]]:
    if not _panel_guard_is_profile_action(custom_id):
        return None

    guild_id = int(interaction.guild.id) if interaction.guild else 0
    user_id = int(interaction.user.id)
    key = (guild_id, user_id)
    now = monotonic()

    if len(_PROFILE_PANEL_BLOCK_UNTIL) > 5000:
        stale = [k for k, until in _PROFILE_PANEL_BLOCK_UNTIL.items() if now - float(until or 0.0) > 120]
        for stale_key in stale[:1000]:
            _PROFILE_PANEL_BLOCK_UNTIL.pop(stale_key, None)
            _PROFILE_PANEL_LAST_NOTICE.pop(stale_key, None)
            lock = _PROFILE_PANEL_HARD_LOCKS.get(stale_key)
            if lock is not None and not lock.locked():
                _PROFILE_PANEL_HARD_LOCKS.pop(stale_key, None)

    lock = _PROFILE_PANEL_HARD_LOCKS.setdefault(key, asyncio.Lock())
    blocked_until = float(_PROFILE_PANEL_BLOCK_UNTIL.get(key, 0.0) or 0.0)

    if lock.locked() or now < blocked_until:
        await _panel_guard_quiet_reject(interaction, key)
        return None

    _PROFILE_PANEL_BLOCK_UNTIL[key] = now + _PROFILE_PANEL_HARD_COOLDOWN_SECONDS
    await lock.acquire()
    return key


def _panel_guard_release(key: Optional[tuple[int, int]]) -> None:
    if key is None:
        return
    now = monotonic()
    _PROFILE_PANEL_BLOCK_UNTIL[key] = max(
        float(_PROFILE_PANEL_BLOCK_UNTIL.get(key, 0.0) or 0.0),
        now + _PROFILE_PANEL_HARD_COOLDOWN_SECONDS,
    )
    lock = _PROFILE_PANEL_HARD_LOCKS.get(key)
    if lock is not None and lock.locked():
        lock.release()


async def _interaction_listener(interaction: discord.Interaction) -> None:
    custom_id = _panel_guard_custom_id(interaction)
    gate_key: Optional[tuple[int, int]] = None

    if _panel_guard_is_profile_action(custom_id):
        if not await _profile_session_gate(interaction, custom_id):
            return
        gate_key = await _panel_guard_acquire(interaction, custom_id)
        if gate_key is None:
            return

    try:
        if await _handle_profile_interaction(interaction):
            return
        await _handle_self_role(interaction)
    finally:
        _panel_guard_release(gate_key)

class AdvancedSelfRolePanelView(discord.ui.View):
    def __init__(self, roles: list[discord.Role]) -> None:
        super().__init__(timeout=None)
        for index, role in enumerate(roles[:20]):
            self.add_item(
                discord.ui.Button(
                    label=role.name[:80],
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"{SELF_ROLE_PREFIX}{int(role.id)}",
                    row=min(4, index // 4),
                )
            )


async def _post_advanced_panel(interaction: discord.Interaction, channel: discord.TextChannel, title: str, roles: list[discord.Role]) -> None:
    embed = discord.Embed(
        title=title[:256],
        description="Advanced existing-role panel. Staff picked these roles manually.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Roles", value="\\n".join(f"• {role.mention}" for role in roles)[:1024], inline=False)
    embed.add_field(
        name="Safety note",
        value="Use this only for cosmetic roles. Do not use these buttons for verification, staff access, tickets, moderation, or permissions.",
        inline=False,
    )
    embed.set_footer(text="Dank Shield advanced role panel")
    try:
        await channel.send(embed=embed, view=AdvancedSelfRolePanelView(roles), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"✅ Advanced role panel posted in {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not post advanced role panel: `{type(exc).__name__}: {str(exc)[:250]}`", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@profile_group.command(name="builder", description="Open the complete profile panel builder in this channel.")
@app_commands.describe(title="Optional panel title.")
async def profile_builder(
    interaction: discord.Interaction,
    title: str = "Profile Panel",
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await _post_profile_builder(interaction, title=title)


@profile_group.command(name="view", description="View your profile card or another member's profile.")
@app_commands.describe(member="Member to view. Leave blank to view yourself.")
async def profile_view(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
) -> None:
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)

    target = member
    if target is None:
        target = interaction.user if isinstance(interaction.user, discord.Member) else None

    if not isinstance(target, discord.Member):
        return await _reply(interaction, "Could not resolve that member.", ok=False)

    await interaction.response.send_message(
        embed=_profile_card(target),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@roles_group.command(name="panel", description="Advanced: post a custom panel using roles that already exist.")
@app_commands.describe(
    channel="Where to post the advanced role panel.",
    title="Panel title.",
    role1="First existing role.",
    role2="Optional existing role.",
    role3="Optional existing role.",
    role4="Optional existing role.",
    role5="Optional existing role.",
)
async def roles_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    role1: discord.Role,
    role2: Optional[discord.Role] = None,
    role3: Optional[discord.Role] = None,
    role4: Optional[discord.Role] = None,
    role5: Optional[discord.Role] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)

    roles: list[discord.Role] = []
    seen: set[int] = set()
    for role in (role1, role2, role3, role4, role5):
        if not isinstance(role, discord.Role) or int(role.id) in seen:
            continue
        seen.add(int(role.id))
        ok, why = _can_manage(role, interaction.guild)
        if not ok:
            return await _reply(interaction, why, ok=False)
        roles.append(role)

    if not roles:
        return await _reply(interaction, "Pick at least one usable existing role.", ok=False)

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass

    await _post_advanced_panel(interaction, channel, title, roles)


@roles_group.command(name="health", description="Advanced: check whether Dank Shield can manage one existing role.")
@app_commands.describe(role="Role to test.")
async def roles_health(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)
    ok, why = _can_manage(role, interaction.guild)
    await _reply(interaction, f"{role.mention} is ready for advanced role panels." if ok else why, ok=ok)


def _attach_groups() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True

    ok = True
    try:
        if stoney_group.get_command("profile") is None:
            stoney_group.add_command(profile_group)
    except Exception as exc:
        ok = False
        try:
            print(f"⚠️ public_self_roles_group failed attaching /dank profile: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    try:
        if stoney_group.get_command("roles") is None:
            stoney_group.add_command(roles_group)
    except Exception as exc:
        ok = False
        try:
            print(f"⚠️ public_self_roles_group failed attaching /dank roles: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    _ATTACHED = ok
    return ok


def register_public_self_roles_group_commands(bot: Any, tree: Any) -> None:
    global _LISTENER_ATTACHED
    _ = tree

    if bot is not None and not _LISTENER_ATTACHED:
        try:
            bot.add_listener(_interaction_listener, "on_interaction")
            _LISTENER_ATTACHED = True
        except Exception as exc:
            try:
                print(f"⚠️ public_self_roles_group listener failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass

    if _attach_groups():
        try:
            print("✅ public_self_roles_group: attached /dank profile and advanced /dank roles commands")
        except Exception:
            pass


_attach_groups()

__all__ = ["register_public_self_roles_group_commands", "profile_group", "roles_group"]
