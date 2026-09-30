from __future__ import annotations

"""Generic Community & Pings manager and member self-selection UI."""

import asyncio
import re
from dataclasses import replace
from typing import Any, Mapping, Optional

import discord

from stoney_verify.community_pings_service import (
    CAP_TOKE_NOTIFY,
    CAP_TOKE_START,
    COMMUNITY_PINGS_KEY,
    LEGACY_TOKE_CHANNEL_KEY,
    MAX_COMMUNITY_OPTIONS,
    CommunityPingGroup,
    CommunityPingOption,
    CommunityPingsConfig,
    enabled_options,
    move_option,
    option_for_role,
    parse_community_pings,
    toke_role_ids,
    upsert_group,
    without_group,
    validate_config,
    validate_member_selection,
    with_option,
    without_option,
)
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.ui.picker import DankChoice, DankMultiPickerView, DankPickerView
from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView


_MEMBER_LOCKS: dict[str, asyncio.Lock] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _member_lock(guild_id: int, member_id: int) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(member_id)}"
    lock = _MEMBER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _MEMBER_LOCKS[key] = lock
    if len(_MEMBER_LOCKS) > 4096:
        for old_key, old_lock in list(_MEMBER_LOCKS.items())[:1024]:
            if not old_lock.locked() and old_key != key:
                _MEMBER_LOCKS.pop(old_key, None)
    return lock


async def _reply(interaction: discord.Interaction, message: str, *, ok: bool = False) -> None:
    prefix = "✅ " if ok else "❌ "
    payload = {
        "content": prefix + str(message),
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _replace(
    interaction: discord.Interaction,
    *,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    kwargs = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


async def _staff_authorized(interaction: discord.Interaction) -> bool:
    from .public_setup_group import _require_setup_permission
    return bool(await _require_setup_permission(interaction))


async def _load(guild: discord.Guild) -> tuple[Mapping[str, Any], CommunityPingsConfig]:
    from stoney_verify.guild_config import get_guild_config
    raw = await get_guild_config(int(guild.id), refresh=True)
    return raw, parse_community_pings(raw)


async def _save(
    interaction: discord.Interaction,
    *,
    expected_config: Mapping[str, Any],
    updated: CommunityPingsConfig,
) -> Optional[Mapping[str, Any]]:
    errors = validate_config(updated)
    if errors:
        await _reply(interaction, "Community & Pings validation failed: " + "; ".join(errors[:4]))
        return None

    guild = interaction.guild
    if guild is None:
        await _reply(interaction, "This only works inside a server.")
        return None

    from stoney_verify.guild_config import compare_and_swap_guild_config_key

    expected = expected_config.get(COMMUNITY_PINGS_KEY)
    try:
        applied, saved = await compare_and_swap_guild_config_key(
            int(guild.id),
            COMMUNITY_PINGS_KEY,
            expected=expected,
            value=updated.to_payload(),
            source="community_pings_builder",
        )
    except Exception as exc:
        await _reply(
            interaction,
            f"Community & Pings could not save safely: {type(exc).__name__}. Nothing was changed.",
        )
        return None

    if not applied:
        await _reply(
            interaction,
            "Community & Pings changed in another admin session. Refresh this manager before trying again.",
        )
        return None
    return saved


def _profile_safe_blocker(
    guild: discord.Guild,
    role: discord.Role,
    config: Mapping[str, Any],
) -> str:
    from .public_self_roles_group import _profile_cosmetic_role_blocker
    return str(_profile_cosmetic_role_blocker(guild, role, config) or "")


def _option_role(
    guild: discord.Guild,
    option: CommunityPingOption,
    config: Mapping[str, Any],
) -> Optional[discord.Role]:
    role = guild.get_role(int(option.role_id))
    if not isinstance(role, discord.Role):
        return None
    if _profile_safe_blocker(guild, role, config):
        return None
    return role


def _resolved_options(
    guild: discord.Guild,
    config: Mapping[str, Any],
    model: CommunityPingsConfig,
) -> list[tuple[CommunityPingOption, discord.Role]]:
    resolved: list[tuple[CommunityPingOption, discord.Role]] = []
    for option in enabled_options(model):
        role = _option_role(guild, option, config)
        if isinstance(role, discord.Role):
            resolved.append((option, role))
    return resolved[:MAX_COMMUNITY_OPTIONS]


def _group_map(model: CommunityPingsConfig) -> dict[str, CommunityPingGroup]:
    return {group.key: group for group in model.groups}


def _manager_embed(
    guild: discord.Guild,
    raw_config: Mapping[str, Any],
    model: CommunityPingsConfig,
) -> discord.Embed:
    embed = discord.Embed(
        title="🌿 Community & Pings Manager",
        description=(
            "**Home › Community & Engagement › Community & Pings Manager**\n"
            "Build the optional roles members may choose for themselves. Community roles and notification roles "
            "share one safe engine; access/staff/verification roles remain blocked."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Revision", value=str(int(model.revision)), inline=True)
    embed.add_field(name="Options", value=f"{len(model.options)} / {MAX_COMMUNITY_OPTIONS}", inline=True)
    embed.add_field(name="Groups", value=str(len(model.groups)), inline=True)

    starter_id, notify_id = toke_role_ids(model, raw_config)
    starter = guild.get_role(starter_id) if starter_id else None
    notify = guild.get_role(notify_id) if notify_id else None
    channel_id = _safe_int(raw_config.get(LEGACY_TOKE_CHANNEL_KEY), 0)
    channel = guild.get_channel(channel_id) if channel_id else None
    embed.add_field(
        name="/toke integration",
        value=(
            f"Starter: {starter.mention if isinstance(starter, discord.Role) else 'Not configured'}\n"
            f"Notify: {notify.mention if isinstance(notify, discord.Role) else 'Not configured'}\n"
            f"Preferred channel: {channel.mention if isinstance(channel, discord.TextChannel) else 'Use the command channel'}"
        ),
        inline=False,
    )

    if model.source == "legacy":
        embed.add_field(
            name="Legacy preset detected",
            value=(
                "Your existing Stoner / Sesh Pings mapping is loaded as a compatibility preset. "
                "The first Builder save converts it into the generic revisioned model without removing the roles."
            ),
            inline=False,
        )

    if not model.options:
        embed.add_field(
            name="Configured options",
            value="None yet. Add an existing safe role to begin.",
            inline=False,
        )
    else:
        groups = _group_map(model)
        lines: list[str] = []
        for option in model.options[:20]:
            role = guild.get_role(int(option.role_id))
            role_text = role.mention if isinstance(role, discord.Role) else f"Missing role \`{option.role_id}\`"
            group = groups.get(option.group_key)
            group_name = group.label if group is not None else option.group_key
            flags: list[str] = []
            if not option.enabled:
                flags.append("disabled")
            if not option.removable:
                flags.append("locked removal")
            if option.prerequisite_role_id:
                flags.append("prerequisite")
            if option.exclusive_key:
                flags.append(f"exclusive:{option.exclusive_key}")
            if CAP_TOKE_START in option.capabilities:
                flags.append("/toke starter")
            if CAP_TOKE_NOTIFY in option.capabilities:
                flags.append("/toke notify")
            flag_text = f" • {', '.join(flags)}" if flags else ""
            kind = "Ping" if option.kind == "notification" else "Community"
            lines.append(
                f"{option.emoji} **{option.label}** · {kind} · {group_name}\n"
                f"↳ {role_text}{flag_text}"
            )
        suffix = f"\n… and {len(model.options) - 20} more" if len(model.options) > 20 else ""
        embed.add_field(name="Configured options", value=("\n".join(lines) + suffix)[:1024], inline=False)

    embed.add_field(
        name="Member safety",
        value=(
            "Every role is revalidated against live hierarchy and sensitive-role rules before it can be offered or changed. "
            "Prerequisites, mutual exclusion, group limits, and non-removable choices are rechecked at click time."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield • Community & Pings • changes are per server")
    return embed


def _option_embed(
    guild: discord.Guild,
    model: CommunityPingsConfig,
    option: CommunityPingOption,
) -> discord.Embed:
    role = guild.get_role(int(option.role_id))
    role_text = role.mention if isinstance(role, discord.Role) else f"Missing role \`{option.role_id}\`"
    groups = _group_map(model)
    group = groups.get(option.group_key)
    prereq = guild.get_role(int(option.prerequisite_role_id)) if option.prerequisite_role_id else None
    embed = discord.Embed(
        title=f"{option.emoji} {option.label}",
        description="Community & Pings option editor",
        color=discord.Color.green(),
    )
    embed.add_field(name="Discord role", value=role_text, inline=False)
    embed.add_field(
        name="Type / group",
        value=f"{option.kind.title()} • {group.label if group else option.group_key}",
        inline=True,
    )
    embed.add_field(name="Enabled", value="Yes" if option.enabled else "No", inline=True)
    embed.add_field(name="Members may remove", value="Yes" if option.removable else "No", inline=True)
    embed.add_field(
        name="Prerequisite",
        value=prereq.mention if isinstance(prereq, discord.Role) else ("None" if not option.prerequisite_role_id else f"Missing \`{option.prerequisite_role_id}\`"),
        inline=False,
    )
    embed.add_field(name="Mutual exclusion", value=option.exclusive_key or "None", inline=True)
    caps = []
    if CAP_TOKE_START in option.capabilities:
        caps.append("May start /toke")
    if CAP_TOKE_NOTIFY in option.capabilities:
        caps.append("Receives /toke notifications")
    embed.add_field(name="/toke compatibility", value=" • ".join(caps) if caps else "None", inline=False)
    embed.add_field(name="Description", value=option.description or "No description.", inline=False)
    embed.set_footer(text=f"Revision {model.revision} • option key {option.key}")
    return embed


def _member_embed(
    guild: discord.Guild,
    member: discord.Member,
    model: CommunityPingsConfig,
    resolved: list[tuple[CommunityPingOption, discord.Role]],
) -> discord.Embed:
    embed = discord.Embed(
        title="🌿 My Community & Pings",
        description=(
            "Choose the optional community and notification roles you want. "
            "These choices never grant staff, moderation, verification, ticket, or protected-server access."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    groups = _group_map(model)
    grouped: dict[str, list[str]] = {}
    for option, role in resolved:
        marker = "✅" if role in member.roles else "▫️"
        kind = "Ping" if option.kind == "notification" else "Community"
        rule_bits: list[str] = []
        if option.prerequisite_role_id:
            rule_bits.append("requires another role")
        if option.exclusive_key:
            rule_bits.append("exclusive choice")
        if not option.removable:
            rule_bits.append("cannot self-remove")
        rule = f" • {', '.join(rule_bits)}" if rule_bits else ""
        grouped.setdefault(option.group_key, []).append(
            f"{marker} {option.emoji} **{option.label}** · {kind}{rule}"
        )

    for group_key, lines in list(grouped.items())[:10]:
        group = groups.get(group_key)
        title = f"{group.emoji} {group.label}" if group is not None else group_key
        if group is not None and group.max_selections > 0:
            title += f" · max {group.max_selections}"
        embed.add_field(name=title[:256], value="\n".join(lines)[:1024], inline=False)

    embed.set_footer(text=f"Community & Pings revision {model.revision} • changes affect only your account")
    return embed


def _preview_embed(guild: discord.Guild, model: CommunityPingsConfig) -> discord.Embed:
    lines = [
        f"{option.emoji} **{option.label}** · {option.group_key} · {option.kind.title()}"
        for option in enabled_options(model)
    ]
    embed = discord.Embed(
        title="👁️ Member Preview • Community & Pings",
        description="This is a read-only preview. No member roles can change from this screen.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Visible choices",
        value="\n".join(lines)[:1024] if lines else "No enabled choices.",
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • revision {model.revision}")
    return embed


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _safe_int(getattr(interaction.user, "id", 0), 0) == self.owner_id:
            return True
        await _reply(interaction, "Open your own Community & Pings panel to use these controls.")
        return False


async def _open_manager_message(interaction: discord.Interaction, *, owner_id: Optional[int] = None) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside a server.")
    raw, model = await _load(guild)
    await _replace(
        interaction,
        embed=_manager_embed(guild, raw, model),
        view=CommunityPingsManagerView(owner_id or int(interaction.user.id)),
    )


class CommunityOptionModal(discord.ui.Modal, title="Community / Ping Option"):
    display_name = discord.ui.TextInput(label="Display name", placeholder="Gaming Pings", min_length=1, max_length=80)
    emoji = discord.ui.TextInput(label="Emoji", placeholder="🎮", required=False, max_length=32)
    description = discord.ui.TextInput(
        label="Description",
        placeholder="Get notified for gaming sessions.",
        required=False,
        max_length=100,
    )
    kind = discord.ui.TextInput(
        label="Type: community or notification",
        placeholder="notification",
        min_length=1,
        max_length=20,
    )
    group = discord.ui.TextInput(label="Group key", placeholder="gaming", min_length=1, max_length=48)

    def __init__(
        self,
        *,
        owner_id: int,
        role_id: int,
        baseline: Mapping[str, Any],
        option: Optional[CommunityPingOption] = None,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.role_id = int(role_id)
        self.baseline = dict(baseline)
        self.option = option
        if option is not None:
            self.display_name.default = option.label
            self.emoji.default = option.emoji
            self.description.default = option.description
            self.kind.default = option.kind
            self.group.default = option.group_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if _safe_int(getattr(interaction.user, "id", 0), 0) != self.owner_id:
            return await _reply(interaction, "This editor belongs to another admin.")
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")

        raw_kind = str(self.kind.value or "").strip().lower()
        if raw_kind not in {"community", "notification"}:
            return await _reply(interaction, "Type must be exactly \`community\` or \`notification\`.")

        _raw_now, model = await _load(guild)
        if self.option is None:
            key = "-".join(
                part
                for part in re.split(r"[^a-z0-9]+", str(self.display_name.value or "").strip().lower())
                if part
            )[:48] or f"role-{self.role_id}"
            option = CommunityPingOption(
                key=key,
                role_id=self.role_id,
                label=str(self.display_name.value or "")[:80],
                emoji=str(self.emoji.value or "").strip()[:32] or ("💬" if raw_kind == "notification" else "🌿"),
                description=str(self.description.value or "").strip()[:100],
                kind=raw_kind,
                group_key="-".join(
                    part
                    for part in re.split(r"[^a-z0-9]+", str(self.group.value or "community").strip().lower())
                    if part
                )[:48] or "community",
                order=len(model.options),
            )
        else:
            live = next((item for item in model.options if item.key == self.option.key), None)
            if live is None:
                return await _reply(interaction, "That option changed or was removed. Refresh the manager.")
            option = replace(
                live,
                label=str(self.display_name.value or "")[:80],
                emoji=str(self.emoji.value or "").strip()[:32] or live.emoji,
                description=str(self.description.value or "").strip()[:100],
                kind=raw_kind,
                group_key="-".join(
                    part
                    for part in re.split(r"[^a-z0-9]+", str(self.group.value or "community").strip().lower())
                    if part
                )[:48] or "community",
            )

        try:
            updated = with_option(model, option)
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        saved = await _save(interaction, expected_config=self.baseline, updated=updated)
        if saved is None:
            return
        await _open_manager_message(interaction, owner_id=self.owner_id)


class CommunityGroupModal(discord.ui.Modal, title="Community & Pings Group"):
    key_input = discord.ui.TextInput(label="Group key", placeholder="gaming", min_length=1, max_length=48)
    label_input = discord.ui.TextInput(label="Display name", placeholder="Gaming", min_length=1, max_length=80)
    emoji_input = discord.ui.TextInput(label="Emoji", placeholder="🎮", required=False, max_length=32)
    max_input = discord.ui.TextInput(
        label="Max selections (0 = unlimited)",
        placeholder="0",
        required=False,
        max_length=2,
    )

    def __init__(
        self,
        *,
        owner_id: int,
        baseline: Mapping[str, Any],
        group: Optional[CommunityPingGroup] = None,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.baseline = dict(baseline)
        self.group = group
        if group is not None:
            self.key_input.default = group.key
            self.label_input.default = group.label
            self.emoji_input.default = group.emoji
            self.max_input.default = str(group.max_selections)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if _safe_int(getattr(interaction.user, "id", 0), 0) != self.owner_id:
            return await _reply(interaction, "This editor belongs to another admin.")
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")

        key = "-".join(
            part
            for part in re.split(r"[^a-z0-9]+", str(self.key_input.value or "").strip().lower())
            if part
        )[:48]
        if self.group is not None:
            key = self.group.key
        if not key:
            return await _reply(interaction, "Enter a valid group key.")

        max_text = str(self.max_input.value or "0").strip() or "0"
        if not max_text.isdigit():
            return await _reply(interaction, "Max selections must be a number from 0 to 25.")
        max_selections = int(max_text)
        if max_selections > MAX_COMMUNITY_OPTIONS:
            return await _reply(interaction, f"Max selections must be 0 to {MAX_COMMUNITY_OPTIONS}.")

        _raw_now, model = await _load(guild)
        live_group = next((item for item in model.groups if item.key == key), None)
        group = CommunityPingGroup(
            key=key,
            label=str(self.label_input.value or key)[:80],
            emoji=str(self.emoji_input.value or "").strip()[:32] or (live_group.emoji if live_group else "🏷️"),
            max_selections=max_selections,
            order=live_group.order if live_group is not None else len(model.groups),
        )
        try:
            updated = upsert_group(model, group)
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        saved = await _save(interaction, expected_config=self.baseline, updated=updated)
        if saved is None:
            return
        await _open_manager_message(interaction, owner_id=self.owner_id)


class ExclusiveKeyModal(discord.ui.Modal, title="Mutual Exclusion"):
    key_input = discord.ui.TextInput(
        label="Exclusive key (blank clears)",
        placeholder="platform-choice",
        required=False,
        max_length=48,
    )

    def __init__(
        self,
        *,
        owner_id: int,
        option_key: str,
        baseline: Mapping[str, Any],
        current: str,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.option_key = option_key
        self.baseline = dict(baseline)
        self.key_input.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if _safe_int(getattr(interaction.user, "id", 0), 0) != self.owner_id:
            return await _reply(interaction, "This editor belongs to another admin.")
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        _raw_now, model = await _load(guild)
        option = next((item for item in model.options if item.key == self.option_key), None)
        if option is None:
            return await _reply(interaction, "That option changed or was removed. Refresh.")
        key = "-".join(
            part
            for part in re.split(r"[^a-z0-9]+", str(self.key_input.value or "").strip().lower())
            if part
        )[:48]
        updated = with_option(model, replace(option, exclusive_key=key))
        saved = await _save(interaction, expected_config=self.baseline, updated=updated)
        if saved is None:
            return
        await _open_option_editor(interaction, self.owner_id, option.key)


class DeleteOptionConfirmView(_OwnedView):
    def __init__(self, owner_id: int, option_key: str, baseline: Mapping[str, Any]) -> None:
        super().__init__(owner_id)
        self.option_key = option_key
        self.baseline = dict(baseline)

    @discord.ui.button(label="Delete Option", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        _raw_now, model = await _load(guild)
        updated = without_option(model, self.option_key)
        saved = await _save(interaction, expected_config=self.baseline, updated=updated)
        if saved is None:
            return
        await _open_manager_message(interaction, owner_id=self.owner_id)

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_option_editor(interaction, self.owner_id, self.option_key)


class CommunityOptionEditorView(_OwnedView):
    def __init__(self, owner_id: int, option_key: str) -> None:
        super().__init__(owner_id)
        self.option_key = option_key

    async def _state(
        self,
        interaction: discord.Interaction,
    ) -> tuple[Optional[discord.Guild], Mapping[str, Any], Optional[CommunityPingsConfig], Optional[CommunityPingOption]]:
        guild = interaction.guild
        if guild is None:
            await _reply(interaction, "This only works inside a server.")
            return None, {}, None, None
        raw, model = await _load(guild)
        option = next((item for item in model.options if item.key == self.option_key), None)
        if option is None:
            await _reply(interaction, "That option no longer exists. Refresh the manager.")
            return guild, raw, model, None
        return guild, raw, model, option

    @discord.ui.button(label="Edit Details", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit_details(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild, raw, _model, option = await self._state(interaction)
        if guild is None or option is None:
            return
        await interaction.response.send_modal(
            CommunityOptionModal(
                owner_id=self.owner_id,
                role_id=option.role_id,
                baseline=raw,
                option=option,
            )
        )

    @discord.ui.button(label="Prerequisite", emoji="🔐", style=discord.ButtonStyle.secondary, row=0)
    async def prerequisite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild, raw, model, option = await self._state(interaction)
        if guild is None or model is None or option is None:
            return

        async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
            if not isinstance(resource, discord.Role):
                return await _reply(pick_interaction, "Choose a role.")
            fresh_raw, fresh_model = await _load(guild)
            fresh = next((item for item in fresh_model.options if item.key == option.key), None)
            if fresh is None:
                return await _reply(pick_interaction, "That option changed. Refresh.")
            updated = with_option(fresh_model, replace(fresh, prerequisite_role_id=int(resource.id)))
            saved = await _save(pick_interaction, expected_config=raw, updated=updated)
            if saved is None:
                return
            await _open_option_editor(pick_interaction, self.owner_id, option.key)

        def allowed(role: Any) -> bool:
            return isinstance(role, discord.Role) and not role.is_default() and int(role.id) != int(option.role_id)

        async def back(back_interaction: discord.Interaction) -> None:
            await _open_option_editor(back_interaction, self.owner_id, option.key)

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=("role",),
            on_pick=picked,
            custom_id="dank:community_pings:prereq",
            title=f"Prerequisite for {option.label}",
            placeholder="Choose a prerequisite role…",
            predicate=allowed,
            on_home=back,
            home_label="Back to option",
        )
        await _replace(interaction, embed=browser.embed(), view=browser)

    @discord.ui.button(label="Clear Prerequisite", emoji="🧹", style=discord.ButtonStyle.secondary, row=0)
    async def clear_prereq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild, raw, model, option = await self._state(interaction)
        if guild is None or model is None or option is None:
            return
        updated = with_option(model, replace(option, prerequisite_role_id=0))
        saved = await _save(interaction, expected_config=raw, updated=updated)
        if saved is None:
            return
        await _open_option_editor(interaction, self.owner_id, option.key)

    @discord.ui.button(label="Mutual Exclusion", emoji="↔️", style=discord.ButtonStyle.secondary, row=1)
    async def exclusive(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild, raw, _model, option = await self._state(interaction)
        if guild is None or option is None:
            return
        await interaction.response.send_modal(
            ExclusiveKeyModal(
                owner_id=self.owner_id,
                option_key=option.key,
                baseline=raw,
                current=option.exclusive_key,
            )
        )

    @discord.ui.button(label="Toggle Removable", emoji="🔓", style=discord.ButtonStyle.secondary, row=1)
    async def removable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._toggle_bool(interaction, "removable")

    @discord.ui.button(label="Enable / Disable", emoji="⏯️", style=discord.ButtonStyle.secondary, row=1)
    async def enabled(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._toggle_bool(interaction, "enabled")

    async def _toggle_bool(self, interaction: discord.Interaction, field: str) -> None:
        if not await _staff_authorized(interaction):
            return
        guild, raw, model, option = await self._state(interaction)
        if guild is None or model is None or option is None:
            return
        updated_option = replace(option, **{field: not bool(getattr(option, field))})
        updated = with_option(model, updated_option)
        saved = await _save(interaction, expected_config=raw, updated=updated)
        if saved is None:
            return
        await _open_option_editor(interaction, self.owner_id, option.key)

    @discord.ui.button(label="Toke Starter", emoji="🌿", style=discord.ButtonStyle.secondary, row=2)
    async def toke_start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._toggle_capability(interaction, CAP_TOKE_START)

    @discord.ui.button(label="Toke Notify", emoji="💨", style=discord.ButtonStyle.secondary, row=2)
    async def toke_notify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._toggle_capability(interaction, CAP_TOKE_NOTIFY)

    async def _toggle_capability(self, interaction: discord.Interaction, capability: str) -> None:
        if not await _staff_authorized(interaction):
            return
        guild, raw, model, option = await self._state(interaction)
        if guild is None or model is None or option is None:
            return
        caps = list(option.capabilities)
        options = list(model.options)
        if capability in caps:
            caps = [value for value in caps if value != capability]
        else:
            options = [
                replace(
                    item,
                    capabilities=tuple(value for value in item.capabilities if value != capability),
                )
                if item.key != option.key and capability in item.capabilities
                else item
                for item in options
            ]
            model = CommunityPingsConfig(
                revision=model.revision,
                groups=model.groups,
                options=tuple(options),
                source=model.source,
            )
            caps.append(capability)
        updated = with_option(model, replace(option, capabilities=tuple(dict.fromkeys(caps))))
        saved = await _save(interaction, expected_config=raw, updated=updated)
        if saved is None:
            return
        await _open_option_editor(interaction, self.owner_id, option.key)

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.secondary, row=2)
    async def move_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._move(interaction, -1)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.secondary, row=2)
    async def move_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._move(interaction, 1)

    async def _move(self, interaction: discord.Interaction, delta: int) -> None:
        if not await _staff_authorized(interaction):
            return
        guild, raw, model, option = await self._state(interaction)
        if guild is None or model is None or option is None:
            return
        updated = move_option(model, option.key, delta)
        if updated is model:
            return await _reply(interaction, "That option is already at the end of the list.", ok=True)
        saved = await _save(interaction, expected_config=raw, updated=updated)
        if saved is None:
            return
        await _open_option_editor(interaction, self.owner_id, option.key)

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger, row=3)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild, raw, model, option = await self._state(interaction)
        if guild is None or model is None or option is None:
            return
        embed = discord.Embed(
            title="🗑️ Delete Community & Pings Option?",
            description=(
                f"Delete **{option.label}** from the self-service list? "
                "This does **not** delete the Discord role or remove it from existing members."
            ),
            color=discord.Color.red(),
        )
        await _replace(
            interaction,
            embed=embed,
            view=DeleteOptionConfirmView(self.owner_id, option.key, raw),
        )

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manager_message(interaction, owner_id=self.owner_id)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=3)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Community & Pings editor closed.", embed=None, view=None)


async def _open_option_editor(interaction: discord.Interaction, owner_id: int, option_key: str) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside a server.")
    _raw, model = await _load(guild)
    option = next((item for item in model.options if item.key == option_key), None)
    if option is None:
        return await _open_manager_message(interaction, owner_id=owner_id)
    await _replace(
        interaction,
        embed=_option_embed(guild, model, option),
        view=CommunityOptionEditorView(owner_id, option.key),
    )


class DeleteGroupConfirmView(_OwnedView):
    def __init__(self, owner_id: int, group_key: str, baseline: Mapping[str, Any]) -> None:
        super().__init__(owner_id)
        self.group_key = str(group_key)
        self.baseline = dict(baseline)

    @discord.ui.button(label="Delete Group", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        _raw_now, model = await _load(guild)
        try:
            updated = without_group(model, self.group_key)
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        saved = await _save(interaction, expected_config=self.baseline, updated=updated)
        if saved is None:
            return
        await _open_manager_message(interaction, owner_id=self.owner_id)

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manager_message(interaction, owner_id=self.owner_id)


class CommunityPingsManagerView(_OwnedView):
    @discord.ui.button(label="Add Option", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_option(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        raw, model = await _load(guild)
        if len(model.options) >= MAX_COMMUNITY_OPTIONS:
            return await _reply(interaction, f"This server already has the maximum {MAX_COMMUNITY_OPTIONS} Community & Pings options.")

        def allowed(role: Any) -> bool:
            return (
                isinstance(role, discord.Role)
                and not role.is_default()
                and option_for_role(model, int(role.id)) is None
                and not _profile_safe_blocker(guild, role, raw)
            )

        async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
            if not isinstance(resource, discord.Role):
                return await _reply(pick_interaction, "Choose a role.")
            await pick_interaction.response.send_modal(
                CommunityOptionModal(
                    owner_id=self.owner_id,
                    role_id=int(resource.id),
                    baseline=raw,
                )
            )

        async def back(back_interaction: discord.Interaction) -> None:
            await _open_manager_message(back_interaction, owner_id=self.owner_id)

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=("role",),
            on_pick=picked,
            custom_id="dank:community_pings:add_role",
            title="Add Community & Pings Option",
            placeholder="Choose a safe self-service role…",
            predicate=allowed,
            on_home=back,
            home_label="Back to manager",
            empty_message="No additional safe self-service roles are available.",
        )
        await _replace(interaction, embed=browser.embed(), view=browser)

    @discord.ui.button(label="Edit Option", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit_option(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        _raw, model = await _load(guild)
        choices = [
            DankChoice(
                label=item.label,
                value=item.key,
                description=f"{item.kind.title()} • {item.group_key}",
                emoji=item.emoji,
            )
            for item in model.options
        ]

        async def picked(pick_interaction: discord.Interaction, value: str) -> None:
            await _open_option_editor(pick_interaction, self.owner_id, value)

        async def back(back_interaction: discord.Interaction) -> None:
            await _open_manager_message(back_interaction, owner_id=self.owner_id)

        await _replace(
            interaction,
            embed=discord.Embed(
                title="✏️ Edit Community & Pings Option",
                description="Choose an option to edit its rules, ordering, state, and /toke compatibility.",
                color=discord.Color.green(),
            ),
            view=DankPickerView(
                author_id=self.owner_id,
                choices=choices,
                on_pick=picked,
                custom_id="dank:community_pings:edit_option",
                placeholder="Choose an option…",
                on_home=back,
                home_label="Back to manager",
            ),
        )

    @discord.ui.button(label="Add Group", emoji="🗂️", style=discord.ButtonStyle.secondary, row=0)
    async def add_group(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        raw, _model = await _load(guild)
        await interaction.response.send_modal(
            CommunityGroupModal(owner_id=self.owner_id, baseline=raw)
        )

    @discord.ui.button(label="Edit Group", emoji="🧩", style=discord.ButtonStyle.secondary, row=0)
    async def edit_group(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        raw, model = await _load(guild)
        choices = [
            DankChoice(
                label=group.label,
                value=group.key,
                description=f"Maximum {group.max_selections}" if group.max_selections > 0 else "Unlimited selections",
                emoji=group.emoji,
            )
            for group in model.groups
        ]

        async def picked(pick_interaction: discord.Interaction, value: str) -> None:
            selected = next((item for item in model.groups if item.key == value), None)
            if selected is None:
                return await _reply(pick_interaction, "That group changed. Refresh.")
            await pick_interaction.response.send_modal(
                CommunityGroupModal(
                    owner_id=self.owner_id,
                    baseline=raw,
                    group=selected,
                )
            )

        async def back(back_interaction: discord.Interaction) -> None:
            await _open_manager_message(back_interaction, owner_id=self.owner_id)

        await _replace(
            interaction,
            embed=discord.Embed(
                title="🧩 Community & Pings Groups",
                description="Edit display names, emojis, and maximum selections for a group.",
                color=discord.Color.green(),
            ),
            view=DankPickerView(
                author_id=self.owner_id,
                choices=choices,
                on_pick=picked,
                custom_id="dank:community_pings:edit_group",
                placeholder="Choose a group…",
                on_home=back,
                home_label="Back to manager",
            ),
        )

    @discord.ui.button(label="Delete Group", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def delete_group(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        raw, model = await _load(guild)
        choices = [
            DankChoice(
                label=group.label,
                value=group.key,
                description="Empty group • safe to delete",
                emoji=group.emoji,
            )
            for group in model.groups
            if not any(option.group_key == group.key for option in model.options)
        ]
        if not choices:
            return await _reply(
                interaction,
                "No empty groups are available to delete. Move or delete a group's options first.",
                ok=True,
            )

        async def picked(pick_interaction: discord.Interaction, value: str) -> None:
            selected = next((item for item in model.groups if item.key == value), None)
            if selected is None:
                return await _reply(pick_interaction, "That group changed. Refresh.")
            embed = discord.Embed(
                title="🗑️ Delete Community & Pings Group?",
                description=(
                    f"Delete **{selected.label}** from Community & Pings? "
                    "Only empty groups can be deleted. Discord roles are never deleted here."
                ),
                color=discord.Color.red(),
            )
            await _replace(
                pick_interaction,
                embed=embed,
                view=DeleteGroupConfirmView(self.owner_id, selected.key, raw),
            )

        async def back(back_interaction: discord.Interaction) -> None:
            await _open_manager_message(back_interaction, owner_id=self.owner_id)

        await _replace(
            interaction,
            embed=discord.Embed(
                title="🗑️ Delete Community & Pings Group",
                description="Choose an empty group. Groups that still contain options are intentionally hidden.",
                color=discord.Color.red(),
            ),
            view=DankPickerView(
                author_id=self.owner_id,
                choices=choices,
                on_pick=picked,
                custom_id="dank:community_pings:delete_group",
                placeholder="Choose an empty group…",
                on_home=back,
                home_label="Back to manager",
            ),
        )

    @discord.ui.button(label="Member Preview", emoji="👁️", style=discord.ButtonStyle.secondary, row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        _raw, model = await _load(guild)
        await _replace(
            interaction,
            embed=_preview_embed(guild, model),
            view=CommunityPreviewView(self.owner_id),
        )

    @discord.ui.button(label="Toke Channel", emoji="💨", style=discord.ButtonStyle.secondary, row=1)
    async def toke_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")

        async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
            if not isinstance(resource, discord.TextChannel):
                return await _reply(pick_interaction, "Choose a normal text or announcement channel.")
            from stoney_verify.guild_config import upsert_guild_config

            await upsert_guild_config(
                int(guild.id),
                {
                    LEGACY_TOKE_CHANNEL_KEY: str(int(resource.id)),
                    "__config_write_mode": "explicit_override",
                    "__config_write_source": "community_pings_builder_toke_channel",
                    "__config_write_actor_id": str(getattr(pick_interaction.user, "id", "") or ""),
                    "__config_write_allow_keys": [LEGACY_TOKE_CHANNEL_KEY],
                },
            )
            await _open_manager_message(pick_interaction, owner_id=self.owner_id)

        async def back(back_interaction: discord.Interaction) -> None:
            await _open_manager_message(back_interaction, owner_id=self.owner_id)

        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=("text",),
            on_pick=picked,
            custom_id="dank:community_pings:toke_channel",
            title="Choose /toke Preferred Channel",
            placeholder="Choose a text channel…",
            predicate=lambda resource: isinstance(resource, discord.TextChannel),
            on_home=back,
            home_label="Back to manager",
        )
        await _replace(interaction, embed=browser.embed(), view=browser)

    @discord.ui.button(label="Clear Toke Channel", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
    async def clear_toke_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        from stoney_verify.guild_config import clear_guild_config_keys

        await clear_guild_config_keys(
            int(guild.id),
            (LEGACY_TOKE_CHANNEL_KEY,),
            source="community pings builder toke channel",
            actor=interaction.user,
        )
        await _open_manager_message(interaction, owner_id=self.owner_id)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manager_message(interaction, owner_id=self.owner_id)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import replace_with_compact_dank_home
        await replace_with_compact_dank_home(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Community & Pings Manager closed.", embed=None, view=None)


class CommunityPreviewView(_OwnedView):
    @discord.ui.button(label="Back to Manager", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manager_message(interaction, owner_id=self.owner_id)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Community & Pings preview closed.", embed=None, view=None)


async def _handle_member_pick(interaction: discord.Interaction, values: list[str]) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "This only works inside a server.")

    if not interaction.response.is_done():
        await interaction.response.defer()

    async with _member_lock(guild.id, member.id):
        raw, model = await _load(guild)
        resolved = _resolved_options(guild, raw, model)
        available = {int(role.id): (option, role) for option, role in resolved}
        selected = {
            int(value)
            for value in values
            if str(value).isdigit() and int(value) in available
        }
        current_ids = {
            int(getattr(role, "id", 0) or 0)
            for role in list(member.roles or [])
        }

        effective_model = CommunityPingsConfig(
            revision=model.revision,
            groups=model.groups,
            options=tuple(option for option, _role in resolved),
            source=model.source,
        )
        error = validate_member_selection(
            effective_model,
            selected_role_ids=selected,
            current_role_ids=current_ids,
        )
        if error:
            return await _reply(interaction, error)

        to_add = [
            role
            for role_id, (_option, role) in available.items()
            if role_id in selected and role not in member.roles
        ]
        to_remove = [
            role
            for role_id, (option, role) in available.items()
            if role_id not in selected and role in member.roles and option.removable
        ]

        try:
            if to_add:
                await member.add_roles(*to_add, reason="Dank Shield Community & Pings self-selection")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Dank Shield Community & Pings self-selection")
        except discord.Forbidden:
            return await _reply(interaction, "Dank Shield cannot manage one of those roles. Staff should check role hierarchy.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"Discord could not update those roles: {type(exc).__name__}.")

    if to_add or to_remove:
        try:
            from .public_profile_cards import invalidate_member_live_cards
            await invalidate_member_live_cards(interaction.client, guild, member.id)
        except Exception:
            pass

    changes: list[str] = []
    if to_add:
        changes.append("Added: " + ", ".join(role.mention for role in to_add))
    if to_remove:
        changes.append("Removed: " + ", ".join(role.mention for role in to_remove))
    await _reply(interaction, "\n".join(changes) if changes else "No Community & Pings changes needed.", ok=True)


async def open_community_ping_setup(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    if not await _staff_authorized(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside a server.")
    if replace_message and not interaction.response.is_done():
        await interaction.response.defer()
    elif not replace_message and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    await _open_manager_message(interaction, owner_id=int(interaction.user.id))


async def open_member_community_pings(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "This only works inside a server.")

    if replace_message and not interaction.response.is_done():
        await interaction.response.defer()
    elif not replace_message and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    raw, model = await _load(guild)
    resolved = _resolved_options(guild, raw, model)
    if not resolved:
        return await _replace(
            interaction,
            content="ℹ️ This server has not configured any available Community & Pings choices yet.",
            embed=None,
            view=None,
        )

    choices = [
        DankChoice(
            label=option.label,
            value=str(int(role.id)),
            description=option.description or ("Notification role" if option.kind == "notification" else "Community role"),
            emoji=option.emoji,
            default=role in member.roles,
        )
        for option, role in resolved
    ]
    await _replace(
        interaction,
        embed=_member_embed(guild, member, model, resolved),
        view=DankMultiPickerView(
            author_id=int(member.id),
            choices=choices,
            on_pick=_handle_member_pick,
            custom_id="dank:community_pings:member:v2",
            placeholder="Choose your Community & Pings roles…",
            min_values=0,
            max_values=len(choices),
            allow_anyone=False,
        ),
    )


__all__ = [
    "CommunityOptionEditorView",
    "CommunityOptionModal",
    "CommunityPingsManagerView",
    "DeleteGroupConfirmView",
    "open_community_ping_setup",
    "open_member_community_pings",
]
