from __future__ import annotations

"""Guild-scoped Modlog Tracking configuration and UI."""

from typing import Any

import discord


CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("messages", "💬", "Messages: deletes/edits"),
    ("members", "👥", "Members: joins/leaves/names"),
    (
        "moderation",
        "🔨",
        "Moderation: bans/kicks/timeouts",
    ),
    ("voice", "🔊", "Voice: VC joins/leaves/moves"),
    (
        "channels",
        "#️⃣",
        "Channels: create/edit/delete",
    ),
    (
        "roles",
        "🎭",
        "Roles: create/edit/delete/member roles",
    ),
    ("threads", "🧵", "Threads: create/edit/delete"),
    (
        "invites",
        "🔗",
        "Invites: create/delete/usage",
    ),
    ("server", "🏠", "Server: name/icon/settings"),
    (
        "assets",
        "😀",
        "Emojis/Stickers: changes",
    ),
    (
        "webhooks",
        "🪝",
        "Webhooks: create/edit/delete",
    ),
)

DETAILS: dict[str, str] = {
    "messages": (
        "Message deletes, edits, purge-style cleanup, "
        "and content changes."
    ),
    "members": (
        "Server joins, server leaves, nickname/name "
        "changes, and member state changes."
    ),
    "moderation": (
        "Bans, unbans, kicks, timeouts, warnings, "
        "and staff moderation actions."
    ),
    "voice": (
        "Voice channel joins, leaves, moves, server "
        "mute/deafen, self mute/deafen, stream, and "
        "video changes."
    ),
    "channels": (
        "Text, voice, category, and forum channel "
        "create, edit, delete, and permission changes "
        "when available."
    ),
    "roles": (
        "Role create, edit, delete, and member role "
        "add/remove events."
    ),
    "threads": (
        "Thread create, archive, unarchive, edit, "
        "and delete events."
    ),
    "invites": (
        "Invite create/delete events and invite-related "
        "attribution when available."
    ),
    "server": (
        "Guild name, icon, moderation level, and major "
        "server-setting changes."
    ),
    "assets": (
        "Emoji and sticker create, edit, and delete "
        "events."
    ),
    "webhooks": (
        "Webhook create, edit, and delete events."
    ),
}

DEFAULT_ON = {
    key
    for key, _emoji, _label in CATEGORIES
}

KEY = "modlog_tracking_categories"


def _as_list(value: Any) -> list[str]:
    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            str(item).strip().lower()
            for item in value
            if str(item).strip()
        ]

    if isinstance(value, str):
        return [
            item.strip().lower()
            for item in value.replace(
                ";",
                ",",
            ).split(",")
            if item.strip()
        ]

    return []


def _cfg_value(
    cfg: Any,
    name: str,
) -> Any:
    try:
        if (
            hasattr(cfg, "get")
            and cfg.get(name) is not None
        ):
            return cfg.get(name)
    except Exception:
        pass

    try:
        value = getattr(
            cfg,
            name,
            None,
        )

        if value is not None:
            return value
    except Exception:
        pass

    for bucket in (
        "settings",
        "config",
        "metadata",
        "meta",
    ):
        try:
            nested = getattr(
                cfg,
                bucket,
                None,
            )

            if (
                isinstance(nested, dict)
                and nested.get(name) is not None
            ):
                return nested.get(name)
        except Exception:
            pass

    return None


def _saved(cfg: Any) -> set[str]:
    """Return defaults only when no setting was saved.

    An explicitly saved empty list means All Off and must remain off.
    """

    allowed = {
        key
        for key, _emoji, _label in CATEGORIES
    }
    raw = _cfg_value(
        cfg,
        KEY,
    )

    if raw is None:
        return set(DEFAULT_ON)

    return set(_as_list(raw)) & allowed


async def _load_cfg(
    guild: discord.Guild,
) -> Any:
    from stoney_verify.guild_config import (
        get_guild_config,
    )

    return await get_guild_config(
        int(guild.id),
        refresh=True,
    )


async def _save_cfg(
    guild: discord.Guild,
    values: set[str],
    actor: discord.abc.User | None = None,
) -> None:
    from stoney_verify.guild_config import (
        invalidate_guild_config,
        upsert_guild_config,
    )

    await upsert_guild_config(
        int(guild.id),
        {
            KEY: sorted(values),
            "modlog_enabled": bool(values),
            "modlog_updated_by_id": str(
                getattr(
                    actor,
                    "id",
                    "",
                )
                or ""
            ),
        },
    )
    invalidate_guild_config(
        int(guild.id)
    )


async def _require(
    interaction: discord.Interaction,
) -> bool:
    try:
        from stoney_verify.commands_ext import (
            public_setup_solid as solid,
        )

        return bool(
            await solid._require_setup_permission(
                interaction
            )
        )
    except Exception:
        return False


async def _tracking_embed(
    guild: discord.Guild,
) -> discord.Embed:
    cfg = await _load_cfg(guild)
    enabled = _saved(cfg)
    channel_text = "⚠️ No saved modlog channel"

    try:
        from stoney_verify.commands_ext import (
            public_modlog_group as group,
        )

        channel = group._modlog_channel(
            guild,
            cfg,
        )

        if isinstance(
            channel,
            discord.TextChannel,
        ):
            channel_text = channel.mention
    except Exception:
        pass

    on_lines: list[str] = []
    off_lines: list[str] = []
    detail_lines: list[str] = []

    for key, emoji, label in CATEGORIES:
        line = f"{emoji} {label}"

        if key in enabled:
            on_lines.append(line)
            detail_lines.append(
                f"{emoji} **{label}** — "
                f"{DETAILS.get(key, 'Tracked event family.')}"
            )
        else:
            off_lines.append(line)

    embed = discord.Embed(
        title="🧾 Modlog Tracking",
        description=(
            "Choose exactly which event families Dank Shield "
            "records. Every change is saved for this server."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Saved Channel",
        value=channel_text,
        inline=False,
    )
    embed.add_field(
        name=(
            f"Tracked Now "
            f"({len(on_lines)}/{len(CATEGORIES)})"
        ),
        value="\n".join(on_lines) or "None",
        inline=False,
    )
    embed.add_field(
        name="Ignored Now",
        value="\n".join(off_lines) or "None",
        inline=False,
    )
    embed.add_field(
        name="What These Choices Mean",
        value=(
            "\n".join(detail_lines[:8])[:1024]
            or "Nothing is currently enabled."
        ),
        inline=False,
    )

    return embed


class TrackButton(discord.ui.Button):
    def __init__(
        self,
        key: str,
        emoji: str,
        label: str,
        enabled: bool,
        row: int,
    ) -> None:
        short = label.split(
            ":",
            1,
        )[0]

        super().__init__(
            label=(
                f"{short}: "
                f"{'ON' if enabled else 'OFF'}"
            ),
            emoji=emoji,
            style=(
                discord.ButtonStyle.success
                if enabled
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"dank_modlog_track:{key}",
            row=row,
        )
        self.key = key

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await _require(interaction):
            return

        guild = interaction.guild

        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        cfg = await _load_cfg(guild)
        enabled = _saved(cfg)

        if self.key in enabled:
            enabled.remove(self.key)
        else:
            enabled.add(self.key)

        await _save_cfg(
            guild,
            enabled,
            interaction.user,
        )
        await interaction.response.edit_message(
            embed=await _tracking_embed(guild),
            view=ModlogTrackingView(
                guild,
                enabled,
            ),
        )


class ModlogTrackingView(discord.ui.View):
    def __init__(
        self,
        guild: discord.Guild,
        enabled: set[str] | None = None,
    ) -> None:
        super().__init__(timeout=900)

        selected = set(
            DEFAULT_ON
            if enabled is None
            else enabled
        )

        for index, (
            key,
            emoji,
            label,
        ) in enumerate(CATEGORIES):
            self.add_item(
                TrackButton(
                    key,
                    emoji,
                    label,
                    key in selected,
                    row=min(
                        3,
                        index // 3,
                    ),
                )
            )

    @discord.ui.button(
        label="All On",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=4,
    )
    async def all_on(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        guild = interaction.guild

        if (
            guild is None
            or not await _require(interaction)
        ):
            return

        enabled = set(DEFAULT_ON)

        await _save_cfg(
            guild,
            enabled,
            interaction.user,
        )
        await interaction.response.edit_message(
            embed=await _tracking_embed(guild),
            view=ModlogTrackingView(
                guild,
                enabled,
            ),
        )

    @discord.ui.button(
        label="All Off",
        emoji="⏸️",
        style=discord.ButtonStyle.danger,
        row=4,
    )
    async def all_off(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        guild = interaction.guild

        if (
            guild is None
            or not await _require(interaction)
        ):
            return

        enabled: set[str] = set()

        await _save_cfg(
            guild,
            enabled,
            interaction.user,
        )
        await interaction.response.edit_message(
            embed=await _tracking_embed(guild),
            view=ModlogTrackingView(
                guild,
                enabled,
            ),
        )

    @discord.ui.button(
        label="Health",
        emoji="🩺",
        style=discord.ButtonStyle.primary,
        row=4,
    )
    async def health(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button

        if not await _require(interaction):
            return

        from stoney_verify.commands_ext import (
            public_modlog_group as modlog,
        )

        await modlog.open_modlog_health(
            interaction
        )

    @discord.ui.button(
        label="Send Test",
        emoji="📨",
        style=discord.ButtonStyle.primary,
        row=4,
    )
    async def test(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button

        if not await _require(interaction):
            return

        from stoney_verify.commands_ext import (
            public_modlog_group as modlog,
        )

        await modlog.send_modlog_test(
            interaction
        )

    @discord.ui.button(
        label="Back to Advanced",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def back_advanced(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button

        from stoney_verify.commands_ext import (
            public_setup_recommend as recommend,
        )

        await recommend._open_manage_setup(
            interaction
        )


async def open_modlog_tracking(
    interaction: discord.Interaction,
) -> None:
    if not await _require(interaction):
        return

    guild = interaction.guild

    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    cfg = await _load_cfg(guild)

    await interaction.response.edit_message(
        embed=await _tracking_embed(guild),
        view=ModlogTrackingView(
            guild,
            _saved(cfg),
        ),
    )


__all__ = [
    "CATEGORIES",
    "DETAILS",
    "DEFAULT_ON",
    "KEY",
    "ModlogTrackingView",
    "open_modlog_tracking",
]
