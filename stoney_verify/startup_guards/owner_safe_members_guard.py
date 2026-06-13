from __future__ import annotations

from typing import Any

import discord

_KEY = "owner_safe_member_ids"


def _log(message: str) -> None:
    try:
        print(f"🛡️ owner_safe_members_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _ids_from_value(value: Any) -> set[str]:
    if value is None:
        return set()
    raw: list[Any]
    if isinstance(value, str):
        raw = value.replace(";", ",").replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    elif isinstance(value, dict):
        raw = [key for key, enabled in value.items() if enabled is not False]
    else:
        raw = [value]
    out: set[str] = set()
    for item in raw:
        sid = str(_safe_int(item, 0))
        if sid != "0":
            out.add(sid)
    return out


def is_owner(interaction: discord.Interaction) -> bool:
    try:
        return bool(interaction.guild and int(interaction.user.id) == int(interaction.guild.owner_id))
    except Exception:
        return False


async def safe_member_ids(guild_id: int) -> set[str]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild_id, refresh=True)
        ids = _ids_from_value(cfg.get(_KEY))
        ids.update(_ids_from_value(cfg.get("protected_member_ids")))
        return ids
    except Exception:
        return set()


async def is_safe_member(member: Any) -> bool:
    if not isinstance(member, discord.Member):
        return False
    try:
        return str(int(member.id)) in await safe_member_ids(int(member.guild.id))
    except Exception:
        return False


async def _save(guild_id: int, ids: set[str]) -> None:
    from stoney_verify.guild_config import upsert_guild_config

    clean = sorted(str(_safe_int(item, 0)) for item in ids if _safe_int(item, 0) > 0)
    await upsert_guild_config(guild_id, {_KEY: clean, "protected_member_ids": clean})


def _list_text(guild: discord.Guild, ids: set[str]) -> str:
    if not ids:
        return "No members selected."
    rows: list[str] = []
    for sid in sorted(ids):
        member = guild.get_member(_safe_int(sid, 0))
        if isinstance(member, discord.Member):
            rows.append(f"{member.mention} — `{member.id}`")
        else:
            rows.append(f"Unknown member — `{sid}`")
    text = "\n".join(rows)
    return text[:950] + ("\n…" if len(text) > 950 else "")


async def _embed(guild: discord.Guild) -> discord.Embed:
    ids = await safe_member_ids(int(guild.id))
    embed = discord.Embed(
        title="🛡️ Protected Members",
        description=(
            "Owner-only list. Members selected here receive extra safety from staff-side actions through Dank Shield.\n\n"
            "Staff-facing messages stay vague and do not reveal who configured this."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Selected Members", value=_list_text(guild, ids), inline=False)
    return embed


async def _require_owner(interaction: discord.Interaction) -> bool:
    if is_owner(interaction):
        return True
    try:
        await interaction.response.send_message("🛡️ Only the server owner can manage this list.", ephemeral=True)
    except Exception:
        pass
    return False


class AddSafeMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Add selected member(s)…", min_values=1, max_values=5, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_owner(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        ids = await safe_member_ids(int(guild.id))
        for user in list(self.values or []):
            ids.add(str(int(user.id)))
        await _save(int(guild.id), ids)
        await interaction.response.edit_message(embed=await _embed(guild), view=SafeMembersView())


class RemoveSafeMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Remove selected member(s)…", min_values=1, max_values=5, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_owner(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        ids = await safe_member_ids(int(guild.id))
        for user in list(self.values or []):
            ids.discard(str(int(user.id)))
        await _save(int(guild.id), ids)
        await interaction.response.edit_message(embed=await _embed(guild), view=SafeMembersView())


class SafeMembersView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(AddSafeMemberSelect())
        self.add_item(RemoveSafeMemberSelect())

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="owner_safe_members:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_owner(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.commands_ext import public_setup_solid as solid
        embed, view = await solid._build_main_setup_payload(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)


class SafeMembersButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(label="Protected Members", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="owner_safe_members:open", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_owner(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await interaction.response.edit_message(embed=await _embed(interaction.guild), view=SafeMembersView())


def apply() -> bool:
    _log("active; owner-only selected-member safety list available")
    return True


apply()

__all__ = ["apply", "is_owner", "is_safe_member", "SafeMembersButton"]
