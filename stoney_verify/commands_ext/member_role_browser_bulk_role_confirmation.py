from __future__ import annotations

from typing import Any

import discord

from .member_role_browser_common import (
    OwnedView,
    action_lock,
    display_name,
    load_protected_role_ids,
    record_member_action,
    reply_ephemeral,
    require_review,
    role_action_blockers,
    trim,
)


def bulk_role_confirmation_phrase(
    action: str,
    count: int,
    role_id: int,
) -> str:
    clean_action = str(action or "").strip().lower()
    clean_count = max(0, int(count))
    clean_role_id = int(role_id)
    if clean_action == "add_role":
        return f"ADD ROLE {clean_role_id} TO {clean_count}"
    if clean_action == "remove_role":
        return f"REMOVE ROLE {clean_role_id} FROM {clean_count}"
    raise ValueError(f"unsupported bulk role action: {action}")


def _confirmation_matches(
    value: object,
    *,
    action: str,
    count: int,
    role_id: int,
) -> bool:
    entered = " ".join(str(value or "").strip().upper().split())
    return entered == bulk_role_confirmation_phrase(action, count, role_id)


async def _execute_confirmed_role_action(
    parent: Any,
    interaction: discord.Interaction,
    *,
    action: str,
    role_id: int,
    reason: str,
) -> None:
    from . import member_role_browser_bulk as bulk

    guild = interaction.guild
    actor = interaction.user
    if not isinstance(guild, discord.Guild) or not isinstance(actor, discord.Member):
        await interaction.followup.send(
            "❌ Bulk role changes must be run inside a server by a current member.",
            ephemeral=True,
        )
        return

    role = guild.get_role(int(role_id))
    if not isinstance(role, discord.Role):
        await interaction.followup.send(
            "❌ That role no longer exists. Nothing was changed.",
            ephemeral=True,
        )
        return

    members = list(parent.members)
    member_ids = [int(member.id) for member in members]
    operation_lock = bulk._bulk_operation_lock(
        guild.id,
        actor.id,
        f"{action}:{role.id}",
        member_ids,
    )
    if operation_lock.locked():
        await interaction.followup.send(
            "⏳ This exact bulk role operation is already running.",
            ephemeral=True,
        )
        return

    protected_role_ids = await load_protected_role_ids(int(guild.id))
    succeeded = 0
    blocked = 0
    failed = 0
    details: list[str] = []

    async with operation_lock:
        for original in members:
            fresh_target = await bulk._fresh_member(guild, int(original.id))
            if not isinstance(fresh_target, discord.Member):
                failed += 1
                details.append(f"❌ `{original.id}` is no longer available.")
                continue

            target_label = display_name(fresh_target)
            async with action_lock(
                guild.id,
                fresh_target.id,
                f"bulk_{action}:{role.id}",
            ):
                # Re-resolve both the target and role after waiting for locks.
                fresh_target = await bulk._fresh_member(guild, int(original.id))
                fresh_role = guild.get_role(int(role.id))
                if not isinstance(fresh_target, discord.Member):
                    failed += 1
                    details.append(f"❌ `{original.id}` left before execution.")
                    continue
                if not isinstance(fresh_role, discord.Role):
                    failed += 1
                    details.append("❌ The selected role was deleted before execution.")
                    break

                blockers = await role_action_blockers(
                    guild,
                    actor,
                    fresh_target,
                    fresh_role,
                    action,
                    protected_role_ids=protected_role_ids,
                )
                if blockers:
                    blocked += 1
                    details.append(
                        f"⛔ {target_label}: {trim('; '.join(blockers), 150)}"
                    )
                    await bulk._record_blocked_action(
                        guild_id=guild.id,
                        actor_id=actor.id,
                        target_id=fresh_target.id,
                        action=action,
                        blockers=blockers,
                    )
                    continue

                try:
                    audit_reason = (
                        f"Dank Shield confirmed bulk role action by {actor} "
                        f"({actor.id}): {reason or 'No additional reason supplied'}"
                    )
                    changed = False
                    if action == "add_role":
                        if fresh_role not in fresh_target.roles:
                            await fresh_target.add_roles(
                                fresh_role,
                                reason=audit_reason[:512],
                            )
                            changed = True
                    else:
                        if fresh_role in fresh_target.roles:
                            await fresh_target.remove_roles(
                                fresh_role,
                                reason=audit_reason[:512],
                            )
                            changed = True

                    succeeded += 1
                    details.append(
                        f"✅ {target_label}: "
                        f"{'updated' if changed else 'already correct'}"
                    )
                    await record_member_action(
                        guild_id=guild.id,
                        actor_id=actor.id,
                        target_id=fresh_target.id,
                        action=f"bulk_{action}",
                        reason=(
                            f"{fresh_role.name} ({fresh_role.id})"
                            + (f" — {reason}" if reason else "")
                        ),
                        metadata={
                            "ok": True,
                            "changed": changed,
                            "confirmed": True,
                            "confirmed_target_count": len(members),
                            "role_id": str(fresh_role.id),
                            "role_name": fresh_role.name,
                        },
                    )
                except Exception as exc:
                    failed += 1
                    details.append(f"❌ {target_label}: {type(exc).__name__}")
                    await record_member_action(
                        guild_id=guild.id,
                        actor_id=actor.id,
                        target_id=fresh_target.id,
                        action=f"bulk_{action}",
                        reason=(
                            f"{fresh_role.name} ({fresh_role.id})"
                            + (f" — {reason}" if reason else "")
                        ),
                        metadata={
                            "ok": False,
                            "confirmed": True,
                            "confirmed_target_count": len(members),
                            "error": type(exc).__name__,
                            "role_id": str(fresh_role.id),
                            "role_name": fresh_role.name,
                        },
                    )

    await interaction.followup.send(
        embed=bulk._result_embed(
            action_label=action.replace("_", " ").title(),
            succeeded=succeeded,
            blocked=blocked,
            failed=failed,
            details=details,
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class ConfirmedBulkRoleModal(discord.ui.Modal):
    def __init__(
        self,
        parent: Any,
        *,
        action: str,
        role: discord.Role,
    ) -> None:
        self.parent = parent
        self.action = str(action)
        self.role_id = int(role.id)
        self.role_name = str(role.name)
        phrase = bulk_role_confirmation_phrase(
            self.action,
            len(parent.members),
            self.role_id,
        )
        self.reason = discord.ui.TextInput(
            label="Optional reason",
            style=discord.TextStyle.paragraph,
            max_length=400,
            required=False,
            placeholder="Why is this role change being applied?",
        )
        self.confirmation = discord.ui.TextInput(
            label="Type the exact confirmation",
            placeholder=phrase,
            max_length=100,
        )
        title_action = "Add Role" if self.action == "add_role" else "Remove Role"
        super().__init__(title=f"Confirm Bulk {title_action}", timeout=300)
        self.add_item(self.reason)
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != int(self.parent.owner_id):
            await reply_ephemeral(
                interaction,
                "❌ This bulk confirmation belongs to another staff member.",
            )
            return
        if not await require_review(interaction):
            return

        expected = bulk_role_confirmation_phrase(
            self.action,
            len(self.parent.members),
            self.role_id,
        )
        if not _confirmation_matches(
            self.confirmation.value,
            action=self.action,
            count=len(self.parent.members),
            role_id=self.role_id,
        ):
            await reply_ephemeral(
                interaction,
                f"❌ Type `{expected}` exactly. Nothing changed.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_confirmed_role_action(
            self.parent,
            interaction,
            action=self.action,
            role_id=self.role_id,
            reason=str(self.reason.value or "").strip(),
        )


class ConfirmedBulkRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "ConfirmedBulkRoleActionView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a role to review and confirm…",
            min_values=1,
            max_values=1,
            custom_id=f"dank_members_browser:bulk_confirmed_{parent.action}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        if not isinstance(role, discord.Role):
            await reply_ephemeral(
                interaction,
                "❌ Discord did not return a valid role.",
            )
            return
        await interaction.response.send_modal(
            ConfirmedBulkRoleModal(
                self.parent_view.parent,
                action=self.parent_view.action,
                role=role,
            )
        )


class ConfirmedBulkRoleActionView(OwnedView):
    def __init__(self, parent: Any, *, action: str) -> None:
        if action not in {"add_role", "remove_role"}:
            raise ValueError(f"unsupported bulk role action: {action}")
        self.parent = parent
        self.action = action
        super().__init__(parent.owner_id)
        self.add_item(ConfirmedBulkRoleSelect(self))

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        embed = discord.Embed(
            title="🧰 Confirmed Bulk Moderation",
            description=f"Selected **{len(self.parent.members)}** member(s).",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self.parent)


def install_confirmed_bulk_role_actions() -> bool:
    from . import member_role_browser_bulk as bulk

    bulk.BulkRoleActionView = ConfirmedBulkRoleActionView
    bulk.BulkRoleSelect = ConfirmedBulkRoleSelect
    return True


__all__ = [
    "ConfirmedBulkRoleActionView",
    "ConfirmedBulkRoleModal",
    "ConfirmedBulkRoleSelect",
    "bulk_role_confirmation_phrase",
    "install_confirmed_bulk_role_actions",
]
