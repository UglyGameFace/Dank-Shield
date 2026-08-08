from __future__ import annotations

"""UI-first verification center for the compact public command surface."""

from typing import Any, Optional

import discord

from .common import _staff_check


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _replace(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    payload = {
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    elif interaction.message is not None:
        await interaction.response.edit_message(**payload)
    else:
        await interaction.response.send_message(**payload, ephemeral=True)


async def _invoke(command: Any, interaction: discord.Interaction, /, *args: Any, **kwargs: Any) -> Any:
    callback = getattr(command, "callback", command)
    if not callable(callback):
        raise RuntimeError("Verification action is unavailable")
    return await callback(interaction, *args, **kwargs)


async def _require_staff(interaction: discord.Interaction) -> bool:
    try:
        if _staff_check(interaction):
            return True
    except Exception:
        pass
    await _private(interaction, "❌ Staff only.")
    return False


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own verification center to use these controls.")
        return False


def _center_embed() -> discord.Embed:
    embed = discord.Embed(
        title="✅ Verification Center",
        description=(
            "Member status, diagnostics, verification, role repair, server-wide Pending/Unverified "
            "repair, role mapping, and the public Verify panel are all here."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Member actions",
        value="Pick a member below for Status • Diagnose • Verify • Pending repair • Verified/Resident role changes.",
        inline=False,
    )
    embed.add_field(
        name="Server actions",
        value="Repair missing Pending roles • Post/refresh Verify panel • Map exact existing verification roles.",
        inline=False,
    )
    embed.set_footer(text="/verify • one staff doorway")
    return embed


def _target_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="👤 Verification Member Actions",
        description=f"{member.mention}\n`{member.id}`",
        color=member.color if member.color.value else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.add_field(
        name="Available",
        value=(
            "Status • Deep diagnose • Grant Verified + Resident • Restore Pending/Unverified • "
            "Add/remove Verified • Add/remove Resident"
        ),
        inline=False,
    )
    embed.set_footer(text="Existing verification command services are reused; role hierarchy is checked by them.")
    return embed


class VerifyMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent: "VerifyCenterView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a member to inspect or repair…",
            min_values=1,
            max_values=1,
            custom_id="dank:verify:center:member:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        member = interaction.guild.get_member(int(getattr(selected, "id", 0) or 0))
        if not isinstance(member, discord.Member):
            return await _private(interaction, "❌ That user is not currently a member of this server.")
        view = VerifyMemberActionView(self.parent_view.owner_id, member.id)
        await _replace(interaction, embed=_target_embed(member), view=view)


class VerifyCenterView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(VerifyMemberSelect(self))

    @discord.ui.button(label="Repair Pending Roles", emoji="🧰", style=discord.ButtonStyle.primary, row=1)
    async def repair_pending(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        from .public_verify_group import verify_repair_unverified
        await _invoke(verify_repair_unverified, interaction, role=None, create_missing_role=True)

    @discord.ui.button(label="Post / Refresh Verify Panel", emoji="📌", style=discord.ButtonStyle.secondary, row=1)
    async def panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        from .public_verify_basic_panel import verify_panel
        await verify_panel(interaction)

    @discord.ui.button(label="Role Mapping", emoji="🎭", style=discord.ButtonStyle.secondary, row=1)
    async def role_mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = VerifyRoleMappingView(self.owner_id)
        await _replace(interaction, embed=view.embed(), view=view)

    @discord.ui.button(label="Verification Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _invoke_saved
        await _invoke_saved("setup", interaction)


class VerifyMemberActionView(_OwnedView):
    def __init__(self, owner_id: int, member_id: int) -> None:
        self.member_id = int(member_id)
        super().__init__(owner_id)

    def member(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        member = interaction.guild.get_member(self.member_id) if interaction.guild else None
        return member if isinstance(member, discord.Member) else None

    async def _target(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        member = self.member(interaction)
        if member is None:
            await _private(interaction, "❌ That member is no longer in the server.")
        return member

    @discord.ui.button(label="Status", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        from .public_verify_group import verify_status
        await _invoke(verify_status, interaction, user=member)

    @discord.ui.button(label="Diagnose", emoji="🩺", style=discord.ButtonStyle.secondary, row=0)
    async def diagnose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        from .public_verify_group import verify_diagnose
        await _invoke(verify_diagnose, interaction, user=member)

    @discord.ui.button(label="Verify + Member", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def grant(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        from .public_verify_group import verify_grant_vr
        await _invoke(
            verify_grant_vr,
            interaction,
            user=member,
            verified_role=None,
            resident_role=None,
            pending_role=None,
        )

    @discord.ui.button(label="Restore Pending", emoji="⏳", style=discord.ButtonStyle.primary, row=0)
    async def pending(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        from .public_verify_group import verify_fix_member
        await _invoke(
            verify_fix_member,
            interaction,
            user=member,
            role=None,
            remove_conflicts=False,
            create_missing_role=True,
        )

    @discord.ui.button(label="Pending + Clear Conflicts", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def pending_clean(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        from .public_verify_group import verify_fix_member
        await _invoke(
            verify_fix_member,
            interaction,
            user=member,
            role=None,
            remove_conflicts=True,
            create_missing_role=True,
        )

    @discord.ui.button(label="Verified Role", emoji="🟢", style=discord.ButtonStyle.secondary, row=1)
    async def verified_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        view = VerifyRoleToggleView(self.owner_id, member.id, logical="verified")
        await _replace(interaction, embed=view.embed(member), view=view)

    @discord.ui.button(label="Resident Role", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def resident_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = await self._target(interaction)
        if member is None:
            return
        view = VerifyRoleToggleView(self.owner_id, member.id, logical="resident")
        await _replace(interaction, embed=view.embed(member), view=view)

    @discord.ui.button(label="Verification Center", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=VerifyCenterView(self.owner_id))


class VerifyRoleToggleView(_OwnedView):
    def __init__(self, owner_id: int, member_id: int, *, logical: str) -> None:
        self.member_id = int(member_id)
        self.logical = logical
        super().__init__(owner_id)

    def embed(self, member: discord.Member) -> discord.Embed:
        label = "Verified" if self.logical == "verified" else "Member / Resident"
        return discord.Embed(
            title=f"🎭 {label} Role",
            description=f"Target: {member.mention}\nAdd or remove the server's saved/mapped **{label}** role.",
            color=discord.Color.blurple(),
        )

    async def _apply(self, interaction: discord.Interaction, enable: bool) -> None:
        member = interaction.guild.get_member(self.member_id) if interaction.guild else None
        if not isinstance(member, discord.Member):
            return await _private(interaction, "❌ That member left the server.")
        if self.logical == "verified":
            from .public_verify_group import verify_set_verified
            return await _invoke(verify_set_verified, interaction, user=member, enable=enable, role=None)
        from .public_verify_group import verify_set_resident
        await _invoke(verify_set_resident, interaction, user=member, enable=enable, role=None)

    @discord.ui.button(label="Add", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._apply(interaction, True)

    @discord.ui.button(label="Remove", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._apply(interaction, False)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = interaction.guild.get_member(self.member_id) if interaction.guild else None
        if not isinstance(member, discord.Member):
            return await _replace(interaction, embed=_center_embed(), view=VerifyCenterView(self.owner_id))
        await _replace(interaction, embed=_target_embed(member), view=VerifyMemberActionView(self.owner_id, member.id))


class VerifyRoleMappingView(_OwnedView):
    def embed(self) -> discord.Embed:
        return discord.Embed(
            title="🎭 Verification Role Mapping",
            description=(
                "Choose which logical role you want to map, then pick the exact existing server role. "
                "The same manage-role/hierarchy checks used by `/verify` are applied before saving."
            ),
            color=discord.Color.blurple(),
        )

    async def _open(self, interaction: discord.Interaction, logical: str) -> None:
        view = VerifySingleRoleMapView(self.owner_id, logical)
        await _replace(interaction, embed=view.embed(), view=view)

    @discord.ui.button(label="Pending / Unverified", emoji="⏳", style=discord.ButtonStyle.secondary, row=0)
    async def pending(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open(interaction, "pending")

    @discord.ui.button(label="Verified", emoji="✅", style=discord.ButtonStyle.secondary, row=0)
    async def verified(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open(interaction, "verified")

    @discord.ui.button(label="Member / Resident", emoji="🏠", style=discord.ButtonStyle.secondary, row=0)
    async def resident(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open(interaction, "resident")

    @discord.ui.button(label="Staff / Support", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def staff(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open(interaction, "staff")

    @discord.ui.button(label="VC Staff", emoji="🎙️", style=discord.ButtonStyle.secondary, row=1)
    async def vc_staff(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open(interaction, "vc_staff")

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=VerifyCenterView(self.owner_id))


class VerifyRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "VerifySingleRoleMapView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose the exact server role…",
            min_values=1,
            max_values=1,
            custom_id=f"dank:verify:role_map:{parent.logical}:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        if not isinstance(role, discord.Role):
            return await _private(interaction, "❌ Choose a server role.")
        if not await _require_staff(interaction):
            return
        from .public_verify_group import (
            _ROLE_CONFIG_KEYS,
            _ROLE_LABELS,
            _bot_can_manage_role,
            _save_role_config,
        )
        ok, why = _bot_can_manage_role(interaction.guild, role)
        if not ok:
            return await _private(interaction, f"❌ I cannot map {role.mention}: {why}.")
        config_key = _ROLE_CONFIG_KEYS[self.parent_view.logical]
        await _save_role_config(
            interaction.guild,
            config_key,
            role,
            source="verification center role mapping",
            explicit_override=True,
        )
        label = _ROLE_LABELS.get(self.parent_view.logical, self.parent_view.logical.title())
        await _private(interaction, f"✅ **{label}** now uses {role.mention}.")


class VerifySingleRoleMapView(_OwnedView):
    def __init__(self, owner_id: int, logical: str) -> None:
        self.logical = logical
        super().__init__(owner_id)
        self.add_item(VerifyRoleSelect(self))

    def embed(self) -> discord.Embed:
        labels = {
            "pending": "Pending / Unverified",
            "verified": "Verified",
            "resident": "Member / Resident",
            "staff": "Staff / Support",
            "vc_staff": "VC Staff / Support",
        }
        label = labels.get(self.logical, self.logical.title())
        return discord.Embed(
            title=f"🎭 Map {label}",
            description="Pick the exact role Dank Shield should use. The mapping is saved to this server only.",
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = VerifyRoleMappingView(self.owner_id)
        await _replace(interaction, embed=view.embed(), view=view)


async def open_verify_command_center(interaction: discord.Interaction) -> None:
    if not await _require_staff(interaction):
        return
    await _private(
        interaction,
        embed=_center_embed(),
        view=VerifyCenterView(int(interaction.user.id)),
    )


__all__ = ["VerifyCenterView", "open_verify_command_center"]
