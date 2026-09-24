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


def _legacy_panel_backfill_limit() -> int:
    # Existing installations created before panel-message persistence need one
    # bounded history lookup. New/updated panels persist their message ID and
    # never need this recovery scan again.
    return max(
        1,
        min(
            200,
            _env_int("DANK_BASIC_VERIFY_LEGACY_PANEL_BACKFILL_PER_START", 50),
        ),
    )


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
) -> None:
    gid = _safe_int(guild_id, 0)
    mid = _safe_int(message_id, 0)
    if gid <= 0 or mid <= 0:
        return
    try:
        await upsert_guild_config(
            gid,
            {
                _BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: str(mid),
                "__config_write_mode": "explicit_override",
                "__config_write_source": "basic_verify.panel_message_identity",
            },
        )
    except Exception as exc:
        try:
            print(
                "⚠️ basic_verify panel identity persistence failed "
                f"guild={gid} message={mid} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


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


async def post_basic_verify_panel(
    channel: discord.TextChannel,
    *,
    actor_id: int = 0,
    bot_instance: Any = None,
) -> str:
    if not isinstance(channel, discord.TextChannel):
        return "invalid_channel"
    cfg = await get_guild_config(channel.guild.id, refresh=True)
    if not basic_verify_allowed_for_guild(channel.guild, cfg):
        return "disabled"
    embed = build_basic_verify_embed(channel.guild, cfg)
    view = BasicVerifyView()

    try:
        me = channel.guild.me
        me_id = int(getattr(me, "id", 0) or 0)
        async for msg in channel.history(limit=80):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if not msg.embeds:
                continue
            if is_basic_verify_panel_embed(msg.embeds[0]):
                await msg.edit(embed=embed, view=view)
                await _persist_basic_verify_panel_message_id(
                    int(channel.guild.id),
                    int(msg.id),
                )
                target_bot = bot_instance
                if target_bot is None:
                    try:
                        from stoney_verify.globals import bot as target_bot
                    except Exception:
                        target_bot = None
                if target_bot is not None:
                    _bind_basic_verify_panel_message(target_bot, int(msg.id))
                return "updated"
    except Exception:
        pass

    msg = await channel.send(
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    await _persist_basic_verify_panel_message_id(
        int(channel.guild.id),
        int(msg.id),
    )
    target_bot = bot_instance
    if target_bot is None:
        try:
            from stoney_verify.globals import bot as target_bot
        except Exception:
            target_bot = None
    if target_bot is not None:
        _bind_basic_verify_panel_message(target_bot, int(msg.id))
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
) -> str:
    persisted_mid = _safe_int(
        _cfg_value(cfg, _BASIC_VERIFY_PANEL_MESSAGE_ID_KEY, 0),
        0,
    )
    if persisted_mid > 0:
        return (
            "bound"
            if _bind_basic_verify_panel_message(bot, persisted_mid)
            else "bind_failed"
        )

    channel_id = _safe_int(
        _cfg_value(cfg, "verify_channel_id", 0)
        or _cfg_value(cfg, "verification_channel_id", 0),
        0,
    )
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    if not isinstance(channel, discord.TextChannel):
        return "no_channel"

    # The panel-posting path scans only current-bot messages. If an old panel
    # belongs to a previous application identity, it will post a fresh panel
    # owned by the currently running bot instead of leaving a dead component.
    if basic_verify_allowed_for_guild(guild, cfg):
        try:
            from stoney_verify.startup_guards.discord_api_safety import (
                reserve_recovery_discord_rest_requests,
            )
            await reserve_recovery_discord_rest_requests(
                1,
                label=(
                    "basic verify legacy panel "
                    f"guild={int(guild.id)} channel={int(channel.id)}"
                ),
            )
        except Exception:
            pass
        return await post_basic_verify_panel(
            channel,
            bot_instance=bot,
        )

    # A disabled-mode legacy panel is still worth locating and binding so its
    # button can answer with the canonical disabled reason instead of timing out.
    try:
        me_id = _safe_int(getattr(getattr(guild, "me", None), "id", 0), 0)
        async for msg in channel.history(limit=80):
            if not msg.embeds or not is_basic_verify_panel_embed(msg.embeds[0]):
                continue
            if _safe_int(getattr(getattr(msg, "author", None), "id", 0), 0) != me_id:
                continue
            await _persist_basic_verify_panel_message_id(
                int(guild.id),
                int(msg.id),
            )
            _bind_basic_verify_panel_message(bot, int(msg.id))
            return "bound_disabled"
    except Exception:
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
        legacy_budget = _legacy_panel_backfill_limit()
        legacy_used = 0
        counts: dict[str, int] = {}

        for gid, cfg in rows.items():
            guild = guilds.get(int(gid))
            if not isinstance(guild, discord.Guild):
                continue

            persisted_mid = _safe_int(
                _cfg_value(cfg, _BASIC_VERIFY_PANEL_MESSAGE_ID_KEY, 0),
                0,
            )
            if persisted_mid <= 0:
                if legacy_used >= legacy_budget:
                    counts["legacy_deferred"] = counts.get("legacy_deferred", 0) + 1
                    continue
                legacy_used += 1

            result = await _reconcile_one_basic_verify_panel(bot, guild, cfg)
            counts[result] = counts.get(result, 0) + 1
            await asyncio.sleep(0)

        print(
            "✅ basic_verify panel reconciliation complete "
            f"guilds={len(rows)} legacy_scans={legacy_used} results={counts}"
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
