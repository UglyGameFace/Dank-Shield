from __future__ import annotations

"""Public UI for per-guild Search-Safe Naming.

The feature is intentionally separate from Dank Design's visual styling engine:
Dank Design may keep decorative categories and frames, while this reviewed tool
normalizes only styled letter glyphs in roles/channels when native Discord
searchability matters.
"""

from typing import Any

import discord

from stoney_verify.interaction_guard import safe_send_interaction
from stoney_verify.services import naming_identity
from stoney_verify.services import search_safe_naming

_BATCH_SIZE = search_safe_naming.DEFAULT_REPAIR_BATCH_SIZE


def _clip(value: Any, limit: int = 100) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _is_owner(guild: discord.Guild, user: Any) -> bool:
    try:
        return int(getattr(guild, "owner_id", 0) or 0) == int(getattr(user, "id", 0) or 0)
    except Exception:
        return False


async def _require_permission(interaction: discord.Interaction) -> bool:
    from . import public_design_studio_v2 as design_v2

    if not await design_v2._require_design_permission(interaction):  # type: ignore[attr-defined]
        return False

    guild = interaction.guild
    user = interaction.user
    if guild is None:
        return False
    if _is_owner(guild, user):
        return True
    if not isinstance(user, discord.Member):
        return False

    perms = user.guild_permissions
    if perms.administrator or perms.manage_roles:
        return True

    await safe_send_interaction(
        interaction,
        content=(
            "❌ Search-Safe Naming can rename both channels and roles, so enabling or applying it "
            "requires **Manage Roles** in addition to the Server Design permission you already have."
        ),
        ephemeral=True,
        action_name="design.search_safe.manage_roles_required",
    )
    return False


def _mode_label(policy: dict[str, Any]) -> str:
    if policy.get("mode") == naming_identity.NAMING_MODE_SEARCH_SAFE:
        return "Search-Safe · enforced"
    return "Preserve Full Styling"


def _home_embed(
    guild: discord.Guild,
    policy: dict[str, Any],
    rows: list[dict[str, Any]],
) -> discord.Embed:
    _ = guild
    editable = [row for row in rows if row.get("editable")]
    blocked = [row for row in rows if not row.get("editable")]
    role_count = sum(1 for row in rows if row.get("kind") == "role")
    channel_count = sum(1 for row in rows if row.get("kind") == "channel")
    enabled = policy.get("mode") == naming_identity.NAMING_MODE_SEARCH_SAFE

    embed = discord.Embed(
        title="🔎 Search-Safe Naming",
        description=(
            "Keep the server's decoration while fixing the Unicode letter glyphs that can make "
            "Discord's own role/channel search miss ordinary text.\n\n"
            "**This is a per-server policy.** Categories keep their full visual styling. "
            "Role/channel emojis, separators, brackets, and frames stay; only stylized letters "
            "are normalized when Search-Safe mode is enabled."
        ),
        color=discord.Color.green() if enabled else discord.Color.blurple(),
    )
    embed.add_field(name="Current mode", value=f"**{_mode_label(policy)}**", inline=True)
    embed.add_field(
        name="Current scan",
        value=(
            f"Roles with risky letters: **{role_count}**\n"
            f"Channels with risky letters: **{channel_count}**\n"
            f"Ready to repair: **{len(editable)}**\n"
            f"Blocked by bot access/hierarchy: **{len(blocked)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="What still stays styled",
        value=(
            "🌿・𝕊𝕥𝕠𝕟𝕖𝕣 → 🌿・Stoner\n"
            "【🎥】𝖛𝖎𝖉𝖊𝖔𝖘 → 【🎥】videos\n"
            "Category fonts remain untouched."
        ),
        inline=False,
    )
    embed.add_field(
        name="Two search layers",
        value=(
            "**Dank Shield search:** /role Verified can resolve a styled current name or a bounded saved previous name.\n"
            "**Discord native search:** only the live name is searchable, so Search-Safe mode must use ordinary letters "
            "for the searchable word. Discord exposes no hidden alias field."
        ),
        inline=False,
    )
    if enabled:
        embed.add_field(
            name="Automatic enforcement",
            value=(
                "New or renamed styled roles/channels are normalized from gateway events only. "
                "There is no startup sweep or continuous 300,000-server polling."
            ),
            inline=False,
        )
    embed.set_footer(text="Preview first • Repair is capped at 25 existing names per reviewed batch")
    return embed


def _preview_embed(
    guild: discord.Guild,
    policy: dict[str, Any],
    rows: list[dict[str, Any]],
    reviewed_rows: list[dict[str, Any]],
) -> discord.Embed:
    _ = guild
    editable = [row for row in rows if row.get("editable")]
    blocked = [row for row in rows if not row.get("editable")]
    reviewed_ids = {
        (str(row.get("kind") or ""), int(row.get("id") or 0))
        for row in reviewed_rows
    }
    deferred_ready = [
        row
        for row in editable
        if (str(row.get("kind") or ""), int(row.get("id") or 0)) not in reviewed_ids
    ]
    examples = [
        f"• {_clip(row.get('before'), 85)} → {_clip(row.get('after'), 85)}"
        for row in reviewed_rows[:10]
    ]
    blockers = [
        f"• {_clip(row.get('before'), 65)} · {_clip(row.get('blocker'), 110)}"
        for row in blocked[:6]
    ]

    embed = discord.Embed(
        title="🔎 Search-Safe Repair Preview",
        description=(
            "**Nothing has been renamed yet.** Confirming enables/keeps the persistent Search-Safe policy "
            f"and applies only the **{len(reviewed_rows)} exact resource(s)** captured by this preview. "
            "Every ID, current name, permission boundary, and Search-Safe output is rechecked before its Discord edit. "
            "If more remain, the result screen builds a fresh reviewed preview for the next batch."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="Scope",
        value=(
            f"Reviewed in this batch: **{len(reviewed_rows)}**\n"
            f"Ready after this batch: **{len(deferred_ready)}**\n"
            f"Blocked: **{len(blocked)}**\n"
            f"Current policy: **{_mode_label(policy)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Categories",
        value="**Preserved** · full Unicode category styling is intentionally not normalized.",
        inline=True,
    )
    if examples:
        embed.add_field(name="Reviewed changes", value="\n".join(examples)[:1024], inline=False)
    if blockers:
        embed.add_field(name="Blocked examples", value="\n".join(blockers)[:1024], inline=False)
    embed.add_field(
        name="Important",
        value=(
            "Disabling Search-Safe later stops future enforcement but does **not** guess or recreate old font glyphs. "
            "Dank Design's own saved rules/Undo remain responsible for visual redesign history."
        ),
        inline=False,
    )
    return embed


def _result_embed(result: dict[str, Any], policy: dict[str, Any]) -> discord.Embed:
    changed = list(result.get("changed") or [])
    failed = list(result.get("failed") or [])
    lines = [
        f"• {_clip(row.get('before'), 80)} → {_clip(row.get('after'), 80)}"
        for row in changed[:8]
    ]
    fail_lines = [
        f"• {_clip(row.get('before'), 65)} · {_clip(row.get('error'), 110)}"
        for row in failed[:5]
    ]

    embed = discord.Embed(
        title="✅ Search-Safe Naming Applied",
        description=(
            "The per-server Search-Safe policy is now enabled. Future styled role/channel renames "
            "are normalized event-by-event; categories remain fully styled."
        ),
        color=discord.Color.green() if not failed else discord.Color.orange(),
    )
    embed.add_field(
        name="This batch",
        value=(
            f"Changed: **{len(changed)}**\n"
            f"Failed during apply: **{len(failed)}**\n"
            f"Remaining editable: **{int(result.get('remaining_editable') or 0)}**\n"
            f"Remaining blocked: **{int(result.get('remaining_blocked') or 0)}**"
        ),
        inline=True,
    )
    embed.add_field(name="Mode", value=f"**{_mode_label(policy)}**", inline=True)
    if lines:
        embed.add_field(name="Changed examples", value="\n".join(lines)[:1024], inline=False)
    if fail_lines:
        embed.add_field(name="Apply failures", value="\n".join(fail_lines)[:1024], inline=False)
    embed.set_footer(text="The 25-name batch cap prevents one server from creating a giant Discord API burst")
    return embed


class SearchSafeHomeView(discord.ui.View):
    def __init__(self, *, enabled: bool) -> None:
        super().__init__(timeout=900)
        self.disable_policy.disabled = not enabled

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        _ = item
        try:
            print(f"⚠️ Search-Safe Naming UI failed: {type(error).__name__}: {error}")
        except Exception:
            pass
        await safe_send_interaction(
            interaction,
            content="❌ Search-Safe Naming stopped safely before making an unreviewed change.",
            ephemeral=True,
            action_name="design.search_safe.component_error",
        )

    @discord.ui.button(
        label="Preview Search-Safe Repair",
        emoji="🔎",
        style=discord.ButtonStyle.primary,
        custom_id="dank_design_search_safe:preview",
        row=0,
    )
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        policy = await naming_identity.get_naming_policy(guild.id)
        rows = search_safe_naming.scan_search_safe_targets(guild, actor=interaction.user)
        reviewed_rows = search_safe_naming.reviewed_search_safe_batch(
            rows,
            limit=_BATCH_SIZE,
        )
        await interaction.edit_original_response(
            embed=_preview_embed(guild, policy, rows, reviewed_rows),
            view=SearchSafePreviewView(reviewed_rows=reviewed_rows),
        )

    @discord.ui.button(
        label="Preserve Full Styling",
        emoji="🎨",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_design_search_safe:preserve",
        row=0,
    )
    async def disable_policy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        policy = await naming_identity.set_naming_mode(guild.id, naming_identity.NAMING_MODE_PRESERVE)
        rows = search_safe_naming.scan_search_safe_targets(guild, actor=interaction.user)
        embed = _home_embed(guild, policy, rows)
        embed.add_field(
            name="Policy changed",
            value=(
                "Future external renames are no longer normalized automatically. Existing names were not changed. "
                "Dank Shield semantic /role lookup still understands styled current names and saved aliases."
            ),
            inline=False,
        )
        await interaction.edit_original_response(
            embed=embed,
            view=SearchSafeHomeView(enabled=False),
        )

    @discord.ui.button(
        label="Back to Server Design",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_design_search_safe:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from . import public_design_studio_v2 as design_v2

        await design_v2._go_home(interaction)  # type: ignore[attr-defined]


class SearchSafePreviewView(discord.ui.View):
    def __init__(self, *, reviewed_rows: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.reviewed_rows = [dict(row) for row in reviewed_rows]
        self.apply.disabled = not bool(self.reviewed_rows)

    @discord.ui.button(
        label="Enable + Repair Next 25",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="dank_design_search_safe:apply",
        row=0,
    )
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)

        policy = await naming_identity.set_naming_mode(guild.id, naming_identity.NAMING_MODE_SEARCH_SAFE)
        result = await search_safe_naming.apply_search_safe_batch(
            guild,
            actor=interaction.user,
            reviewed_rows=self.reviewed_rows,
            limit=_BATCH_SIZE,
        )
        remaining = int(result.get("remaining_editable") or 0)
        await interaction.edit_original_response(
            embed=_result_embed(result, policy),
            view=SearchSafeResultView(has_more=remaining > 0),
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_design_search_safe:cancel",
        row=0,
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_search_safe_naming(interaction)


class SearchSafeResultView(discord.ui.View):
    def __init__(self, *, has_more: bool) -> None:
        super().__init__(timeout=900)
        self.next_batch.disabled = not has_more

    @discord.ui.button(
        label="Preview Next 25",
        emoji="🔁",
        style=discord.ButtonStyle.primary,
        custom_id="dank_design_search_safe:next_batch",
        row=0,
    )
    async def next_batch(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        policy = await naming_identity.get_naming_policy(guild.id)
        if policy.get("mode") != naming_identity.NAMING_MODE_SEARCH_SAFE:
            return await safe_send_interaction(
                interaction,
                content="❌ Search-Safe policy is no longer enabled. Reopen the naming screen before applying more changes.",
                ephemeral=True,
                action_name="design.search_safe.next_policy_changed",
            )

        rows = search_safe_naming.scan_search_safe_targets(
            guild,
            actor=interaction.user,
        )
        reviewed_rows = search_safe_naming.reviewed_search_safe_batch(
            rows,
            limit=_BATCH_SIZE,
        )
        if not reviewed_rows:
            await interaction.edit_original_response(
                embed=_home_embed(guild, policy, rows),
                view=SearchSafeHomeView(enabled=True),
            )
            return
        await interaction.edit_original_response(
            embed=_preview_embed(guild, policy, rows, reviewed_rows),
            view=SearchSafePreviewView(reviewed_rows=reviewed_rows),
        )

    @discord.ui.button(
        label="Back to Search-Safe Naming",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_design_search_safe:result_back",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_search_safe_naming(interaction)


async def open_search_safe_naming(interaction: discord.Interaction) -> None:
    if not await _require_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=False)
    policy = await naming_identity.get_naming_policy(guild.id)
    rows = search_safe_naming.scan_search_safe_targets(guild, actor=interaction.user)
    await interaction.edit_original_response(
        embed=_home_embed(guild, policy, rows),
        view=SearchSafeHomeView(
            enabled=policy.get("mode") == naming_identity.NAMING_MODE_SEARCH_SAFE
        ),
    )


__all__ = ["open_search_safe_naming"]
