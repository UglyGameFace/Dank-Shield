from __future__ import annotations

"""Basic Discord-button verification flow.

This is the default public-server verification flow:
- new members keep the configured Unverified role
- the verification channel shows a Verify button
- clicking Verify grants Verified / effective Member access and removes Unverified
- no ID upload website, token, ticket, or old Dank Shield panel is involved
"""

import asyncio
import os
from typing import Any, Mapping, Optional

import discord

from stoney_verify.guild_config import (
    GUILD_CONFIG_TABLE_FALLBACKS,
    get_guild_config,
    upsert_guild_config,
)
from stoney_verify.globals import get_supabase
from stoney_verify.setup_engine.loader import snapshot_from_config
from stoney_verify.setup_engine.verification_modes import (
    BASIC_VERIFY_CUSTOM_ID,
    BASIC_VERIFY_FOOTER,
    basic_verify_allowed_for_guild,
    basic_verify_disabled_reason,
)

_BASIC_VERIFY_LOCKS: dict[str, asyncio.Lock] = {}
_RUNTIME_VIEW_REGISTERED = False
_RUNTIME_FALLBACK_LISTENER_REGISTERED = False
_RUNTIME_REGISTRATION_ERROR: str = ""
_BASIC_VERIFY_FALLBACK_GRACE_SECONDS = 0.15
_BASIC_VERIFY_PANEL_MESSAGE_ID_KEY = "basic_verify_panel_message_id"
_BASIC_VERIFY_PANEL_APPLICATION_ID_KEY = "basic_verify_panel_application_id"
_BASIC_VERIFY_PANEL_COMPONENT_ID_KEY = "basic_verify_panel_component_id"
_RUNTIME_READY_RECONCILER_REGISTERED = False
_RUNTIME_READY_RECONCILE_STARTED = False
_BOUND_PANEL_MESSAGE_IDS: set[int] = set()


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        if not raw:
            return int(default)
        return max(0, int(raw))
    except Exception:
        return int(default)


def _legacy_panel_backfill_wave_size() -> int:
    # Existing installations created before panel-message persistence need one
    # bounded history lookup. Process those migrations in fair background waves,
    # but never permanently skip a guild just because an earlier wave was full.
    configured = _env_int("DANK_BASIC_VERIFY_LEGACY_PANEL_BACKFILL_PER_WAVE", 0)
    if configured <= 0:
        # Backward-compatible interpretation of the old per-start setting:
        # it now controls wave size rather than becoming a permanent skip cap.
        configured = _env_int("DANK_BASIC_VERIFY_LEGACY_PANEL_BACKFILL_PER_START", 50)
    return max(1, min(200, configured))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _channel_from_cfg(guild: discord.Guild, cfg: Any, *keys: str) -> Optional[discord.TextChannel]:
    for key in keys:
        cid = _safe_int(_cfg_value(cfg, key, 0), 0)
        if cid <= 0:
            continue
        channel = guild.get_channel(cid)
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


def _clean_name(value: str) -> str:
    return str(value or "").lower().replace("_", "-").replace(" ", "-")


def _channel_by_name(guild: discord.Guild, *tokens: str) -> Optional[discord.TextChannel]:
    wanted = tuple(_clean_name(token) for token in tokens if str(token or "").strip())
    if not wanted:
        return None
    try:
        for channel in list(getattr(guild, "text_channels", []) or []):
            if not isinstance(channel, discord.TextChannel):
                continue
            name = _clean_name(getattr(channel, "name", ""))
            if any(token in name for token in wanted):
                return channel
    except Exception:
        pass
    return None


def _reconcile_verify_channel(
    guild: discord.Guild,
    cfg: Any,
) -> Optional[discord.TextChannel]:
    """Resolve the one Basic Verify channel without REST or all-channel scans."""
    return (
        _channel_from_cfg(
            guild,
            cfg,
            "verify_channel_id",
            "verification_channel_id",
        )
        or _channel_by_name(guild, "verification", "verify")
    )


def _role(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        role = guild.get_role(int(role_id or 0)) if int(role_id or 0) > 0 else None
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def _bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not me.guild_permissions.manage_roles and not me.guild_permissions.administrator:
            return False, "Dank Shield is missing Manage Roles."
        if role >= me.top_role:
            return False, f"Dank Shield's role must be above {role.mention}."
    except Exception:
        return False, "Discord role hierarchy could not be checked."
    return True, ""


def _dedupe_roles(roles: list[Optional[discord.Role]]) -> list[discord.Role]:
    out: list[discord.Role] = []
    seen: set[int] = set()
    for role in roles:
        if not isinstance(role, discord.Role) or role.is_default():
            continue
        if int(role.id) in seen:
            continue
        seen.add(int(role.id))
        out.append(role)
    return out


def _lock_for(guild_id: int, user_id: int) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(user_id)}"
    lock = _BASIC_VERIFY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _BASIC_VERIFY_LOCKS[key] = lock
    return lock


def build_basic_verify_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    rules = _channel_from_cfg(guild, cfg, "rules_channel_id", "rule_channel_id", "rules_text_channel_id") or _channel_by_name(guild, "rules", "rule")
    verify = _channel_from_cfg(guild, cfg, "verify_channel_id", "verification_channel_id") or _channel_by_name(guild, "verification", "verify")
    support = _channel_from_cfg(guild, cfg, "ticket_panel_channel_id", "support_channel_id", "panel_channel_id") or _channel_by_name(guild, "support", "ticket")

    rules_text = rules.mention if rules else "the rules channel"
    verify_text = verify.mention if verify else "this verification channel"
    support_text = support.mention if support else "the support/ticket channel"

    embed = discord.Embed(
        title="✅ Verify to unlock server access",
        description=(
            "This button verifies that you are ready to enter the server. "
            "It is separate from the welcome message and only controls access roles."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Before you tap Verify",
        value=f"Please read {rules_text} first so you understand the server rules.",
        inline=False,
    )
    embed.add_field(
        name="What happens when you verify",
        value=(
            f"1. Stay in {verify_text}.\n"
            "2. Tap **Verify** below.\n"
            "3. Dank Shield gives you the server access role and removes the Unverified role."
        ),
        inline=False,
    )
    embed.add_field(
        name="Need help?",
        value=f"If the button does not work, go to {support_text} and open a ticket.",
        inline=False,
    )
    embed.set_footer(text=f"{BASIC_VERIFY_FOOTER} • access only")
    return embed


def is_basic_verify_panel_embed(embed: discord.Embed) -> bool:
    """Return true for old or current Basic Verify embeds.

    This lets Dank Shield update stale panels instead of posting duplicates.
    """

    try:
        footer_text = str(getattr(getattr(embed, "footer", None), "text", "") or "")
        if BASIC_VERIFY_FOOTER in footer_text:
            return True
        if footer_text.strip() in {"Dank Shield Basic Verify", "Dank Shield Basic Verify • access only"}:
            return True

        title_text = str(getattr(embed, "title", "") or "").lower()
        desc_text = str(getattr(embed, "description", "") or "").lower()
        data_text = str(embed.to_dict()).lower()

        if "basic verify" in footer_text.lower():
            return True

        # Old panel looked like a welcome message but talked about Verify/Unverified.
        if "welcome to" in title_text and ("verify" in data_text or "unverified" in data_text):
            return True

        # New panel is verify-only.
        if "verify" in title_text and ("server access" in title_text or "server access" in desc_text):
            return True
    except Exception:
        pass
    return False


async def _ack(interaction: discord.Interaction) -> bool:
    """Claim and acknowledge one Verify click before any DB/role work."""
    try:
        # A completed response means another dispatcher already claimed this
        # Discord interaction. Never perform the role mutation a second time.
        if interaction.response.is_done():
            return False
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True
    except Exception as exc:
        try:
            print(
                "basic_verify ack failed "
                f"guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


async def _reply(interaction: discord.Interaction, message: str, *, ok: bool) -> None:
    prefix = "✅ " if ok else "❌ "
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(prefix + message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(prefix + message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            print(f"basic_verify reply failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass


class BasicVerifyButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id=BASIC_VERIFY_CUSTOM_ID)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        # One canonical interaction handler owns acknowledgement + role mutation.
        # The persistent view and emergency fallback both delegate here instead
        # of maintaining competing copies of the Verify workflow.
        await maybe_handle_basic_verify_interaction(interaction)


class BasicVerifyView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(BasicVerifyButton())


async def _basic_verify_fallback_listener(
    interaction: discord.Interaction,
) -> None:
    """Recover Basic Verify clicks missed by persistent-view dispatch.

    discord.py dispatches component views before the public on_interaction
    event. When the persistent view is registered, give its callback a short
    grace window to acknowledge first. Only an interaction that is still
    unanswered after that window is allowed to enter the canonical handler.

    When persistent-view registration failed entirely, there is nothing to wait
    for and the listener handles the click immediately.
    """
    try:
        if interaction.type is not discord.InteractionType.component:
            return

        data = (
            interaction.data
            if isinstance(interaction.data, dict)
            else {}
        )
        custom_id = str(data.get("custom_id") or "")
        if custom_id != BASIC_VERIFY_CUSTOM_ID:
            return

        if interaction.response.is_done():
            return

        if _RUNTIME_VIEW_REGISTERED:
            await asyncio.sleep(_BASIC_VERIFY_FALLBACK_GRACE_SECONDS)
            if interaction.response.is_done():
                return

        try:
            print(
                "⚠️ basic_verify delayed fallback claimed click "
                f"interaction={getattr(interaction, 'id', 0)} "
                f"guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)}"
            )
        except Exception:
            pass

        await maybe_handle_basic_verify_interaction(interaction)
    except Exception as exc:
        try:
            print(
                "❌ basic_verify: fallback interaction handler failed "
                f"guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

def basic_verify_runtime_status() -> dict[str, Any]:
    return {
        "persistent_view_registered": bool(
            _RUNTIME_VIEW_REGISTERED
        ),
        "fallback_listener_registered": bool(
            _RUNTIME_FALLBACK_LISTENER_REGISTERED
        ),
        "ready": bool(
            _RUNTIME_VIEW_REGISTERED
            or _RUNTIME_FALLBACK_LISTENER_REGISTERED
        ),
        "error": str(_RUNTIME_REGISTRATION_ERROR or ""),
    }


def install_basic_verify_runtime(
    bot: Any,
    *,
    strict: bool = False,
) -> bool:
    """Install the restart-safe Basic Verify view plus delayed safety listener.

    The persistent view is the primary owner. The global on_interaction
    listener never races it immediately: discord.py emits the interaction event
    after scheduling component-view dispatch, so the listener waits briefly and
    only claims a still-unanswered Basic Verify click.

    Both routes delegate to the same canonical handler and the handler
    acknowledges before database or role work, so there is still only one role
    mutation path.
    """
    global _RUNTIME_VIEW_REGISTERED
    global _RUNTIME_FALLBACK_LISTENER_REGISTERED
    global _RUNTIME_REGISTRATION_ERROR
    global _RUNTIME_READY_RECONCILER_REGISTERED

    if (
        _RUNTIME_VIEW_REGISTERED
        and _RUNTIME_FALLBACK_LISTENER_REGISTERED
        and _RUNTIME_READY_RECONCILER_REGISTERED
    ):
        return True

    errors: list[str] = []

    if not _RUNTIME_VIEW_REGISTERED:
        try:
            add_view = getattr(bot, "add_view", None)
            if not callable(add_view):
                raise RuntimeError(
                    "Discord client has no callable add_view"
                )

            add_view(BasicVerifyView())
            _RUNTIME_VIEW_REGISTERED = True
        except Exception as exc:
            errors.append(
                "persistent view: "
                f"{type(exc).__name__}: {exc}"
            )

    if not _RUNTIME_FALLBACK_LISTENER_REGISTERED:
        try:
            add_listener = getattr(bot, "add_listener", None)
            if not callable(add_listener):
                raise RuntimeError(
                    "Discord client has no callable add_listener"
                )

            add_listener(
                _basic_verify_fallback_listener,
                "on_interaction",
            )
            _RUNTIME_FALLBACK_LISTENER_REGISTERED = True
        except Exception as exc:
            errors.append(
                "fallback listener: "
                f"{type(exc).__name__}: {exc}"
            )

    if not _RUNTIME_READY_RECONCILER_REGISTERED:
        try:
            add_listener = getattr(bot, "add_listener", None)
            if not callable(add_listener):
                raise RuntimeError(
                    "Discord client has no callable add_listener"
                )

            async def _ready_reconciler() -> None:
                await _basic_verify_ready_listener(bot)

            add_listener(_ready_reconciler, "on_ready")
            _RUNTIME_READY_RECONCILER_REGISTERED = True
        except Exception as exc:
            errors.append(
                "panel reconciler: "
                f"{type(exc).__name__}: {exc}"
            )

    ready = bool(
        _RUNTIME_VIEW_REGISTERED
        or _RUNTIME_FALLBACK_LISTENER_REGISTERED
    )

    _RUNTIME_REGISTRATION_ERROR = " | ".join(errors)

    if _RUNTIME_VIEW_REGISTERED and _RUNTIME_FALLBACK_LISTENER_REGISTERED:
        print(
            "✅ basic_verify runtime ready "
            "owner=persistent_view delayed_fallback=True "
            f"panel_reconciler={_RUNTIME_READY_RECONCILER_REGISTERED}"
        )
    elif ready:
        print(
            "⚠️ basic_verify runtime degraded but operational "
            f"persistent_view={_RUNTIME_VIEW_REGISTERED} "
            f"delayed_fallback={_RUNTIME_FALLBACK_LISTENER_REGISTERED} "
            f"error={_RUNTIME_REGISTRATION_ERROR}"
        )
    else:
        message = (
            "Basic Verify has no registered interaction handler"
        )
        if _RUNTIME_REGISTRATION_ERROR:
            message += f": {_RUNTIME_REGISTRATION_ERROR}"

        print(f"❌ basic_verify runtime unavailable: {message}")

        if strict:
            raise RuntimeError(message)

    return ready

def register_basic_verify_runtime(bot: Any) -> bool:
    """Backward-compatible alias for older callers."""
    return install_basic_verify_runtime(bot, strict=False)


async def _persist_basic_verify_panel_message_id(
    guild_id: int,
    message_id: int,
    *,
    application_id: int = 0,
) -> None:
    gid = _safe_int(guild_id, 0)
    mid = _safe_int(message_id, 0)
    aid = _safe_int(application_id, 0)
    if gid <= 0 or mid <= 0:
        return
    patch = {
        _BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: str(mid),
        _BASIC_VERIFY_PANEL_COMPONENT_ID_KEY: BASIC_VERIFY_CUSTOM_ID,
        "__config_write_mode": "explicit_override",
        "__config_write_source": "basic_verify.panel_identity",
    }
    if aid > 0:
        patch[_BASIC_VERIFY_PANEL_APPLICATION_ID_KEY] = str(aid)
    try:
        await upsert_guild_config(gid, patch)
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify panel identity persistence failed "
                f"guild={gid} message={mid} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


def _message_custom_ids(message: Any) -> set[str]:
    found: set[str] = set()

    def visit(component: Any) -> None:
        try:
            custom_id = str(getattr(component, "custom_id", "") or "").strip()
            if custom_id:
                found.add(custom_id)
        except Exception:
            pass
        try:
            for child in list(getattr(component, "children", None) or []):
                visit(child)
        except Exception:
            pass

    try:
        for component in list(getattr(message, "components", None) or []):
            visit(component)
    except Exception:
        pass
    return found


def _message_has_strict_basic_verify_signature(message: Any) -> bool:
    """Require Dank Shield-specific proof before treating a foreign message as ours."""
    if BASIC_VERIFY_CUSTOM_ID in _message_custom_ids(message):
        return True
    try:
        for embed in list(getattr(message, "embeds", None) or []):
            footer_text = str(
                getattr(getattr(embed, "footer", None), "text", "") or ""
            ).strip()
            if BASIC_VERIFY_FOOTER in footer_text:
                return True
            if footer_text in {
                "Dank Shield Basic Verify",
                "Dank Shield Basic Verify • access only",
            }:
                return True
    except Exception:
        pass
    return False


def _message_looks_like_basic_verify_panel(message: Any) -> bool:
    if BASIC_VERIFY_CUSTOM_ID in _message_custom_ids(message):
        return True
    try:
        for embed in list(getattr(message, "embeds", None) or []):
            if is_basic_verify_panel_embed(embed):
                return True
    except Exception:
        pass
    return False


async def _reserve_basic_verify_recovery_request(*, label: str) -> None:
    try:
        from stoney_verify.startup_guards.discord_api_safety import (
            reserve_recovery_discord_rest_requests,
        )
        await reserve_recovery_discord_rest_requests(1, label=label)
    except Exception:
        pass


async def _delete_stale_foreign_basic_verify_panel(message: Any) -> bool:
    channel = getattr(message, "channel", None)
    guild = getattr(message, "guild", None)
    if not isinstance(channel, discord.TextChannel) or not isinstance(guild, discord.Guild):
        return False
    if not _message_has_strict_basic_verify_signature(message):
        return False
    me = getattr(guild, "me", None)
    try:
        if me is None or not bool(channel.permissions_for(me).manage_messages):
            return False
    except Exception:
        return False
    try:
        await _reserve_basic_verify_recovery_request(
            label=(
                "basic verify stale foreign delete "
                f"guild={int(guild.id)} channel={int(channel.id)}"
            )
        )
        await message.delete()
        return True
    except Exception:
        return False


def _bind_basic_verify_panel_message(
    bot: Any,
    message_id: int,
) -> bool:
    mid = _safe_int(message_id, 0)
    if mid <= 0:
        return False
    if mid in _BOUND_PANEL_MESSAGE_IDS:
        return True
    try:
        add_view = getattr(bot, "add_view", None)
        if not callable(add_view):
            return False
        add_view(BasicVerifyView(), message_id=mid)
        _BOUND_PANEL_MESSAGE_IDS.add(mid)
        return True
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify message-bound view registration failed "
                f"message={mid} error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


async def _scan_basic_verify_panels(
    channel: discord.TextChannel,
    *,
    limit: int = 80,
) -> tuple[list[Any], list[Any]]:
    """Return current-app and foreign-app Basic Verify messages newest first."""
    current: list[Any] = []
    foreign: list[Any] = []
    me_id = _safe_int(getattr(getattr(channel.guild, "me", None), "id", 0), 0)

    async for message in channel.history(limit=max(1, int(limit))):
        author_id = _safe_int(
            getattr(getattr(message, "author", None), "id", 0),
            0,
        )
        if author_id > 0 and author_id == me_id:
            if _message_looks_like_basic_verify_panel(message):
                current.append(message)
            continue

        # Foreign messages require the strict Dank Shield signature before they
        # are even candidates for cleanup. The broad historical-title heuristic
        # is only safe for messages authored by the current bot.
        if _message_has_strict_basic_verify_signature(message):
            foreign.append(message)

    return current, foreign


async def _cleanup_foreign_basic_verify_panels(messages: list[Any]) -> int:
    removed = 0
    for message in list(messages or []):
        try:
            if await _delete_stale_foreign_basic_verify_panel(message):
                removed += 1
        except Exception:
            continue
    return removed


async def post_basic_verify_panel(
    channel: discord.TextChannel,
    *,
    actor_id: int = 0,
    bot_instance: Any = None,
    require_history_scan_for_post: bool = False,
) -> str:
    if not isinstance(channel, discord.TextChannel):
        return "invalid_channel"
    cfg = await get_guild_config(channel.guild.id, refresh=True)
    if not basic_verify_allowed_for_guild(channel.guild, cfg):
        return "disabled"
    embed = build_basic_verify_embed(channel.guild, cfg)
    view = BasicVerifyView()

    history_scan_completed = False
    current_panels: list[Any] = []
    foreign_panels: list[Any] = []
    try:
        current_panels, foreign_panels = await _scan_basic_verify_panels(
            channel,
            limit=80,
        )
        history_scan_completed = True
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify panel history scan failed "
                f"guild={getattr(channel.guild, 'id', 0)} "
                f"channel={getattr(channel, 'id', 0)} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

    me_id = _safe_int(getattr(getattr(channel.guild, "me", None), "id", 0), 0)
    target_bot = bot_instance
    if target_bot is None:
        try:
            from stoney_verify.globals import bot as target_bot
        except Exception:
            target_bot = None

    if current_panels:
        msg = current_panels[0]
        await msg.edit(embed=embed, view=view)
        await _persist_basic_verify_panel_message_id(
            int(channel.guild.id),
            int(msg.id),
            application_id=me_id,
        )
        if target_bot is not None:
            _bind_basic_verify_panel_message(target_bot, int(msg.id))
        removed = await _cleanup_foreign_basic_verify_panels(foreign_panels)
        if foreign_panels:
            print(
                "♻️ basic_verify legacy foreign cleanup "
                f"guild={channel.guild.id} channel={channel.id} "
                f"found={len(foreign_panels)} removed={removed} "
                "replacement=current_existing"
            )
        return "updated"

    if require_history_scan_for_post and not history_scan_completed:
        return "scan_failed"

    msg = await channel.send(
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    await _persist_basic_verify_panel_message_id(
        int(channel.guild.id),
        int(msg.id),
        application_id=_safe_int(
            getattr(getattr(msg, "author", None), "id", 0),
            _safe_int(getattr(getattr(channel.guild, "me", None), "id", 0), 0),
        ),
    )
    if target_bot is not None:
        _bind_basic_verify_panel_message(target_bot, int(msg.id))
    removed = await _cleanup_foreign_basic_verify_panels(foreign_panels)
    if foreign_panels:
        print(
            "♻️ basic_verify legacy foreign cleanup "
            f"guild={channel.guild.id} channel={channel.id} "
            f"found={len(foreign_panels)} removed={removed} "
            "replacement=current_posted"
        )
    _ = actor_id
    return "posted"


def _row_basic_verify_config(row: Mapping[str, Any]) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = row.get(bucket)
            if isinstance(nested, Mapping):
                cfg.update(dict(nested))
        for key, value in row.items():
            if key not in {"settings", "config", "metadata", "meta"} and value is not None:
                cfg[key] = value
    except Exception:
        pass
    return cfg


async def _discover_basic_verify_panel_rows(
    guild_ids: list[int],
) -> dict[int, dict[str, Any]]:
    if not guild_ids:
        return {}
    sb = get_supabase()
    if sb is None:
        return {}

    def _read() -> dict[int, dict[str, Any]]:
        found: dict[int, dict[str, Any]] = {}
        for start in range(0, len(guild_ids), 100):
            batch = guild_ids[start : start + 100]
            rows = None
            for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
                try:
                    response = (
                        sb.table(table_name)
                        .select(
                            "guild_id,verify_channel_id,settings,config,metadata,meta"
                        )
                        .in_("guild_id", [str(gid) for gid in batch])
                        .execute()
                    )
                    rows = list(getattr(response, "data", None) or [])
                    break
                except Exception:
                    continue
            for row in list(rows or []):
                if not isinstance(row, Mapping):
                    continue
                gid = _safe_int(row.get("guild_id"), 0)
                if gid <= 0:
                    continue
                found[gid] = _row_basic_verify_config(row)
        return found

    try:
        return await asyncio.to_thread(_read)
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify panel discovery failed "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return {}


async def _reconcile_one_basic_verify_panel(
    bot: Any,
    guild: discord.Guild,
    cfg: Mapping[str, Any],
    *,
    allow_legacy_rest: bool = True,
) -> str:
    persisted_mid = _safe_int(
        _cfg_value(cfg, _BASIC_VERIFY_PANEL_MESSAGE_ID_KEY, 0),
        0,
    )
    saved_application_id = _safe_int(
        _cfg_value(cfg, _BASIC_VERIFY_PANEL_APPLICATION_ID_KEY, 0),
        0,
    )
    saved_component_id = str(
        _cfg_value(cfg, _BASIC_VERIFY_PANEL_COMPONENT_ID_KEY, "") or ""
    ).strip()
    current_application_id = _safe_int(
        getattr(getattr(guild, "me", None), "id", 0),
        0,
    )

    # Only zero-REST bind when persisted ownership AND the exact component
    # contract are both proven current. Message/application identity alone is
    # insufficient because an older Basic Verify message can be authored by the
    # same bot application while carrying a retired custom_id. Binding today's
    # View to that message ID would look healthy at startup but Discord would
    # never route the old button to today's (component_type, custom_id) key.
    if (
        persisted_mid > 0
        and saved_application_id > 0
        and saved_application_id == current_application_id
        and saved_component_id == BASIC_VERIFY_CUSTOM_ID
    ):
        return (
            "bound"
            if _bind_basic_verify_panel_message(bot, persisted_mid)
            else "bind_failed"
        )

    channel = _reconcile_verify_channel(guild, cfg)
    if not isinstance(channel, discord.TextChannel):
        return "no_channel"
    channel_id = int(channel.id)

    if persisted_mid > 0:
        if not allow_legacy_rest:
            return "legacy_deferred"
        try:
            await _reserve_basic_verify_recovery_request(
                label=(
                    "basic verify identity fetch "
                    f"guild={int(guild.id)} channel={int(channel.id)}"
                )
            )
            message = await channel.fetch_message(persisted_mid)
        except discord.NotFound:
            return "missing_message"
        except Exception as exc:
            try:
                print(
                    "⚠️ basic_verify saved panel fetch failed "
                    f"guild={guild.id} channel={channel.id} message={persisted_mid} "
                    f"error={type(exc).__name__}: {exc}"
                )
            except Exception:
                pass
            return "fetch_failed"

        if not _message_looks_like_basic_verify_panel(message):
            return "saved_message_not_basic_verify_panel"

        author_id = _safe_int(
            getattr(getattr(message, "author", None), "id", 0),
            0,
        )
        if author_id == current_application_id and current_application_id > 0:
            custom_ids = _message_custom_ids(message)
            if BASIC_VERIFY_CUSTOM_ID not in custom_ids:
                try:
                    await message.edit(
                        embed=build_basic_verify_embed(guild, cfg),
                        view=BasicVerifyView(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except TypeError:
                    # Compatibility with lightweight test doubles and Discord
                    # message implementations that do not expose
                    # allowed_mentions on edit().
                    try:
                        await message.edit(
                            embed=build_basic_verify_embed(guild, cfg),
                            view=BasicVerifyView(),
                        )
                    except Exception as exc:
                        try:
                            print(
                                "⚠️ basic_verify component contract repair failed "
                                f"guild={guild.id} channel={channel.id} message={message.id} "
                                f"found={sorted(custom_ids)} expected={BASIC_VERIFY_CUSTOM_ID!r} "
                                f"error={type(exc).__name__}: {exc}"
                            )
                        except Exception:
                            pass
                        return "component_repair_failed"
                except Exception as exc:
                    try:
                        print(
                            "⚠️ basic_verify component contract repair failed "
                            f"guild={guild.id} channel={channel.id} message={message.id} "
                            f"found={sorted(custom_ids)} expected={BASIC_VERIFY_CUSTOM_ID!r} "
                            f"error={type(exc).__name__}: {exc}"
                        )
                    except Exception:
                        pass
                    return "component_repair_failed"

                await _persist_basic_verify_panel_message_id(
                    int(guild.id),
                    int(message.id),
                    application_id=current_application_id,
                )
                bound = _bind_basic_verify_panel_message(bot, int(message.id))
                try:
                    print(
                        "♻️ basic_verify repaired legacy component contract "
                        f"guild={guild.id} channel={channel.id} message={message.id} "
                        f"old_ids={sorted(custom_ids)} new_id={BASIC_VERIFY_CUSTOM_ID!r} "
                        f"bound={bound}"
                    )
                except Exception:
                    pass
                return "repaired_component" if bound else "repair_bind_failed"

            await _persist_basic_verify_panel_message_id(
                int(guild.id),
                int(message.id),
                application_id=current_application_id,
            )
            return (
                "migrated_current"
                if _bind_basic_verify_panel_message(bot, int(message.id))
                else "bind_failed"
            )

        if author_id > 0 and author_id != current_application_id:
            # Preserve the stale panel until a current-application replacement
            # is confirmed. A transient send/history failure must never turn a
            # broken interaction into a missing panel.
            result = await post_basic_verify_panel(
                channel,
                bot_instance=bot,
                require_history_scan_for_post=True,
            )
            if result in {"posted", "updated"}:
                deleted = await _delete_stale_foreign_basic_verify_panel(message)
                return (
                    "replaced_foreign_deleted"
                    if deleted
                    else "replaced_foreign"
                )
            return f"foreign_replacement_{result}"

        return "unknown_message_owner"

    # Legacy rows without a saved message ID need one bounded history lookup.
    if not allow_legacy_rest:
        return "legacy_deferred"

    # The panel-posting path scans only current-bot messages. If an old panel
    # belongs to a previous application identity, it will post a fresh panel
    # owned by the currently running bot instead of leaving that as the only
    # usable route.
    if basic_verify_allowed_for_guild(guild, cfg):
        await _reserve_basic_verify_recovery_request(
            label=(
                "basic verify legacy panel "
                f"guild={int(guild.id)} channel={int(channel.id)}"
            )
        )
        return await post_basic_verify_panel(
            channel,
            bot_instance=bot,
            require_history_scan_for_post=True,
        )

    # A disabled-mode legacy panel is still worth locating, but it must obey
    # the same component-identity contract. Otherwise startup would persist
    # today's component proof for a visibly old button and recreate the exact
    # dead-panel failure this reconciler is meant to eliminate.
    try:
        me_id = current_application_id
        async for msg in channel.history(limit=80):
            if not msg.embeds or not is_basic_verify_panel_embed(msg.embeds[0]):
                continue
            if _safe_int(getattr(getattr(msg, "author", None), "id", 0), 0) != me_id:
                continue

            custom_ids = _message_custom_ids(msg)
            repaired = False
            if BASIC_VERIFY_CUSTOM_ID not in custom_ids:
                try:
                    await msg.edit(
                        view=BasicVerifyView(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except TypeError:
                    await msg.edit(view=BasicVerifyView())
                repaired = True

            await _persist_basic_verify_panel_message_id(
                int(guild.id),
                int(msg.id),
                application_id=me_id,
            )
            bound = _bind_basic_verify_panel_message(bot, int(msg.id))
            if not bound:
                return "disabled_bind_failed"
            return "repaired_disabled_component" if repaired else "bound_disabled"
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify disabled-panel component repair failed "
                f"guild={guild.id} channel={channel.id} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return "scan_failed"
    return "not_found"


async def _reconcile_basic_verify_panels_after_ready(bot: Any) -> None:
    global _RUNTIME_READY_RECONCILE_STARTED
    if _RUNTIME_READY_RECONCILE_STARTED:
        return
    _RUNTIME_READY_RECONCILE_STARTED = True

    try:
        guilds = {
            int(guild.id): guild
            for guild in list(getattr(bot, "guilds", []) or [])
            if isinstance(guild, discord.Guild)
        }
        rows = await _discover_basic_verify_panel_rows(sorted(guilds))
        legacy_wave_size = _legacy_panel_backfill_wave_size()
        legacy_used = 0
        legacy_in_wave = 0
        counts: dict[str, int] = {}

        for gid, cfg in rows.items():
            guild = guilds.get(int(gid))
            if not isinstance(guild, discord.Guild):
                continue

            persisted_mid = _safe_int(
                _cfg_value(cfg, _BASIC_VERIFY_PANEL_MESSAGE_ID_KEY, 0),
                0,
            )
            saved_application_id = _safe_int(
                _cfg_value(cfg, _BASIC_VERIFY_PANEL_APPLICATION_ID_KEY, 0),
                0,
            )
            saved_component_id = str(
                _cfg_value(cfg, _BASIC_VERIFY_PANEL_COMPONENT_ID_KEY, "") or ""
            ).strip()
            current_application_id = _safe_int(
                getattr(getattr(guild, "me", None), "id", 0),
                0,
            )
            needs_legacy_rest = not (
                persisted_mid > 0
                and saved_application_id > 0
                and saved_application_id == current_application_id
                and saved_component_id == BASIC_VERIFY_CUSTOM_ID
            )

            # Every legacy panel is eventually reconciled. The exact-message/
            # history request path already reserves through the shared recovery
            # REST budget, so this background worker must not convert a wave
            # size into a permanent "never repair this guild" cap.
            if needs_legacy_rest:
                legacy_used += 1
                legacy_in_wave += 1

            result = await _reconcile_one_basic_verify_panel(
                bot,
                guild,
                cfg,
                allow_legacy_rest=True,
            )
            counts[result] = counts.get(result, 0) + 1

            # Give foreground interactions regular event-loop turns during a
            # large one-time migration. Discord REST pacing remains owned by
            # reserve_recovery_discord_rest_requests()/discord.py.
            if needs_legacy_rest and legacy_in_wave >= legacy_wave_size:
                legacy_in_wave = 0
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0)

        print(
            "✅ basic_verify panel reconciliation complete "
            f"guilds={len(rows)} legacy_rest={legacy_used} "
            f"wave_size={legacy_wave_size} results={counts}"
        )
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify panel reconciliation failed "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


async def _basic_verify_ready_listener(bot: Any) -> None:
    # Never block Discord on_ready. Reconcile in the background after the bot is
    # fully connected so existing interactions remain responsive.
    try:
        asyncio.create_task(
            _reconcile_basic_verify_panels_after_ready(bot),
            name="basic-verify-panel-reconcile",
        )
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify could not schedule panel reconciliation "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


async def apply_basic_verification(member: discord.Member) -> tuple[bool, str]:
    guild = member.guild
    cfg = await get_guild_config(guild.id, refresh=True)

    # The persistent Basic Verify view is globally registered so old Discord
    # messages keep dispatching after restart. Authorization therefore belongs
    # here, at the role-mutation boundary, not only in panel posting or callers.
    if not basic_verify_allowed_for_guild(guild, cfg):
        return False, basic_verify_disabled_reason(guild, cfg)

    snap = snapshot_from_config(guild.id, cfg)

    verified = _role(guild, snap.verified_role_id)
    member_access = _role(guild, snap.effective_member_role_id)
    unverified = _role(guild, snap.unverified_role_id)

    roles_to_add = _dedupe_roles([verified, member_access])
    if not roles_to_add:
        return False, "Verified role is not configured. Staff should run `/dank setup` → Use My Existing Server → Roles."

    for role in roles_to_add + ([unverified] if isinstance(unverified, discord.Role) else []):
        if not isinstance(role, discord.Role):
            continue
        ok, why = _bot_can_manage_role(guild, role)
        if not ok:
            return False, why

    async with _lock_for(int(guild.id), int(member.id)):
        try:
            fresh = guild.get_member(member.id) or await guild.fetch_member(member.id)
        except Exception:
            fresh = member
        if not isinstance(fresh, discord.Member):
            return False, "Could not refresh your server member profile. Try again in a moment."

        add_now = [role for role in roles_to_add if role not in fresh.roles]
        remove_now = [unverified] if isinstance(unverified, discord.Role) and unverified in fresh.roles else []

        if not add_now and not remove_now:
            return True, "You are already verified. Welcome back!"

        try:
            final_roles = [role for role in fresh.roles if isinstance(role, discord.Role) and not role.is_default() and role not in remove_now]
            for role in add_now:
                if role not in final_roles:
                    final_roles.append(role)
            await fresh.edit(roles=final_roles, reason="Dank Shield basic button verification")
        except discord.Forbidden:
            return False, "Discord blocked the role update. Staff should move Dank Shield above Verified/Unverified and grant Manage Roles."
        except Exception:
            try:
                if add_now:
                    await fresh.add_roles(*add_now, reason="Dank Shield basic button verification")
                if remove_now:
                    await fresh.remove_roles(*remove_now, reason="Dank Shield basic button verification cleanup")
            except discord.Forbidden:
                return False, "Discord blocked the role update. Staff should move Dank Shield above Verified/Unverified and grant Manage Roles."
            except Exception as exc:
                return False, f"Verification failed: {type(exc).__name__}. Staff should check bot role hierarchy and setup."

        added = ", ".join(role.mention for role in add_now) if add_now else "already had access role"
        removed = f" Removed {unverified.mention}." if remove_now and isinstance(unverified, discord.Role) else ""
        return True, f"You are verified! Added {added}.{removed}"


async def maybe_handle_basic_verify_interaction(interaction: discord.Interaction) -> bool:
    try:
        data = getattr(interaction, "data", None) or {}
        custom_id = str(data.get("custom_id") or "")
        if custom_id != BASIC_VERIFY_CUSTOM_ID:
            return False
        try:
            print(
                "✅ basic_verify click "
                f"interaction={getattr(interaction, 'id', 0)} "
                f"guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"channel={getattr(getattr(interaction, 'channel', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)}"
            )
        except Exception:
            pass
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "This only works inside the server.", ok=False)
            return True
        if not await _ack(interaction):
            return True
        ok, message = await apply_basic_verification(interaction.user)
        await _reply(interaction, message, ok=ok)
        return True
    except Exception as exc:
        try:
            print(f"basic_verify interaction failed guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} user={getattr(getattr(interaction, 'user', None), 'id', 0)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass
        try:
            await _reply(interaction, f"Basic verification failed: {type(exc).__name__}. Staff should check setup health.", ok=False)
        except Exception:
            pass
        return True


__all__ = [
    "BasicVerifyView",
    "apply_basic_verification",
    "basic_verify_runtime_status",
    "install_basic_verify_runtime",
    "maybe_handle_basic_verify_interaction",
    "post_basic_verify_panel",
    "register_basic_verify_runtime",
]
