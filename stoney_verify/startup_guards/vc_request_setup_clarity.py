from __future__ import annotations

"""Make VC setup failures and health checks truthful.

This module is intentionally loaded as a startup guard because the public setup,
verification UI, and VC flow live in separate modules. It keeps them aligned:

- /stoney setup health checks the same VC queue/channel permissions that the
  ticket VC button actually needs.
- VC request panels use per-guild saved channels before env/global fallbacks.
- VC failures tell staff exactly which setup area is broken instead of only
  saying that the staff panel could not be posted.
"""

from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

import discord

_PATCHED_CLARITY = False
_PATCHED_VC_FLOW = False
_PATCHED_HEALTH = False


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _cfg_value(cfg: Any, *names: str) -> Any:
    if cfg is None:
        return None
    for name in names:
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(name)  # type: ignore[attr-defined]
                if value not in (None, "", 0, "0"):
                    return value
        except Exception:
            pass
        try:
            value = getattr(cfg, name, None)
            if value not in (None, "", 0, "0"):
                return value
        except Exception:
            pass
    return None


def _cfg_int(cfg: Any, *names: str) -> int:
    return _safe_int(_cfg_value(cfg, *names), 0)


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        if isinstance(guild.me, discord.Member):
            return guild.me
    except Exception:
        pass
    try:
        state = getattr(guild, "_state", None)
        user = getattr(state, "user", None)
        user_id = _safe_int(getattr(user, "id", 0), 0)
        member = guild.get_member(user_id) if user_id else None
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _voice_types() -> tuple[type, ...]:
    items: list[type] = [discord.VoiceChannel]
    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None:
        items.append(stage_type)
    return tuple(items)


def _is_voice_like(channel: Any) -> bool:
    return isinstance(channel, _voice_types())


def _unique_ints(values: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        item = _safe_int(value, 0)
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _dedupe_lines(lines: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _configured_text_channel_ids(cfg: Any, *, include_fallbacks: bool = True) -> list[int]:
    primary = [
        _cfg_int(
            cfg,
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_verify_requests_channel_id",
            "vc_requests_channel_id",
            "vc_status_channel_id",
            "vc_verify_status_channel_id",
        ),
    ]
    if include_fallbacks:
        primary.extend(
            [
                _cfg_int(cfg, "modlog_channel_id", "mod_log_channel_id", "raidlog_channel_id"),
                _cfg_int(cfg, "transcripts_channel_id", "transcript_channel_id"),
                _cfg_int(cfg, "status_channel_id", "bot_status_channel_id"),
            ]
        )
    return _unique_ints(primary)


def _configured_vc_channel_id_from_cfg(cfg: Any) -> int:
    return _cfg_int(cfg, "vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id")


def _configured_staff_role_ids(cfg: Any) -> list[int]:
    return _unique_ints(
        [
            _cfg_int(cfg, "staff_role_id"),
            _cfg_int(cfg, "ticket_staff_role_id"),
            _cfg_int(cfg, "support_role_id"),
            _cfg_int(cfg, "vc_staff_role_id"),
            _cfg_int(cfg, "server_control_role_id"),
            _cfg_int(cfg, "control_role_id"),
            _cfg_int(cfg, "perm_role_id"),
            _cfg_int(cfg, "bot_manager_role_id"),
        ]
    )


def _text_missing_perms(channel: discord.TextChannel, bot_member: discord.Member) -> list[str]:
    try:
        perms = channel.permissions_for(bot_member)
    except Exception:
        return ["permission check failed"]
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.read_message_history:
        missing.append("Read Message History")
    if not perms.embed_links:
        missing.append("Embed Links")
    return missing


def _voice_missing_perms(channel: discord.abc.GuildChannel, bot_member: discord.Member) -> list[str]:
    try:
        perms = channel.permissions_for(bot_member)
    except Exception:
        return ["permission check failed"]
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not getattr(perms, "connect", False):
        missing.append("Connect")
    if not perms.manage_channels:
        missing.append("Manage Channels")
    if not getattr(perms, "move_members", False):
        missing.append("Move Members")
    return missing


def _channel_name(channel: Any) -> str:
    try:
        mention = getattr(channel, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    try:
        return f"#{getattr(channel, 'name', 'unknown')}"
    except Exception:
        return "channel"


def _role_name(role: Any) -> str:
    try:
        mention = getattr(role, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    try:
        return f"@{getattr(role, 'name', 'role')}"
    except Exception:
        return "role"


async def _get_guild_config_safe(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        return None


def _extract_guild_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[discord.Guild]:
    for key in ("guild", "server"):
        value = kwargs.get(key)
        if isinstance(value, discord.Guild):
            return value
    for key in ("channel", "ticket_channel", "interaction", "member", "requester", "owner"):
        value = kwargs.get(key)
        guild = getattr(value, "guild", None)
        if isinstance(guild, discord.Guild):
            return guild
    for value in args:
        if isinstance(value, discord.Guild):
            return value
        guild = getattr(value, "guild", None)
        if isinstance(guild, discord.Guild):
            return guild
    return None


# ---------------------------------------------------------------------------
# Health check truth layer
# ---------------------------------------------------------------------------


def _append_vc_runtime_health(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> None:
    bot_member = _bot_member(guild)
    if bot_member is None:
        blockers.append("VC verification runtime check could not resolve the bot member.")
        return

    vc_channel_id = _configured_vc_channel_id_from_cfg(cfg)
    if vc_channel_id <= 0:
        warnings.append("VC verification channel is not set. The **Verify in VC** button should stay disabled until you choose one.")
        return

    raw_vc_channel = guild.get_channel(vc_channel_id)
    if raw_vc_channel is None:
        blockers.append(f"VC verification channel is saved but missing/deleted: `{vc_channel_id}`.")
        return
    if not _is_voice_like(raw_vc_channel):
        blockers.append(f"VC verification channel must be a voice/stage channel, but saved channel is {_channel_name(raw_vc_channel)}.")
        return

    vc_channel = raw_vc_channel  # type: ignore[assignment]
    missing_vc = _voice_missing_perms(vc_channel, bot_member)  # type: ignore[arg-type]
    if missing_vc:
        blockers.append(f"VC verification channel {_channel_name(vc_channel)} is missing bot permissions: {', '.join(missing_vc)}.")
    else:
        ok.append(f"VC verification channel can be controlled by Stoney: {_channel_name(vc_channel)}.")

    try:
        everyone_perms = vc_channel.permissions_for(guild.default_role)  # type: ignore[attr-defined]
        if everyone_perms.view_channel or getattr(everyone_perms, "connect", False):
            blockers.append(
                f"VC verification channel {_channel_name(vc_channel)} is not locked. `@everyone` can "
                f"{'view' if everyone_perms.view_channel else ''}{' and ' if everyone_perms.view_channel and getattr(everyone_perms, 'connect', False) else ''}"
                f"{'connect' if getattr(everyone_perms, 'connect', False) else ''}. Lock it in setup before testing VC verify."
            )
        else:
            ok.append("VC verification channel is locked from @everyone.")
    except Exception:
        warnings.append("Could not verify whether @everyone is locked out of the VC verification channel.")

    waiting_role_id = _cfg_int(cfg, "unverified_role_id")
    waiting_role = guild.get_role(waiting_role_id) if waiting_role_id > 0 else None
    if waiting_role is not None:
        try:
            waiting_perms = vc_channel.permissions_for(waiting_role)  # type: ignore[attr-defined]
            if getattr(waiting_perms, "connect", False):
                blockers.append(f"Waiting/unverified role {_role_name(waiting_role)} can connect to VC verification without staff approval.")
            elif waiting_perms.view_channel:
                ok.append(f"Waiting/unverified role can see VC verification but cannot connect: {_role_name(waiting_role)}.")
            else:
                warnings.append(f"Waiting/unverified role {_role_name(waiting_role)} cannot see the VC verification channel. That is safe, but less clear for users.")
        except Exception:
            warnings.append("Could not verify waiting/unverified role VC permissions.")

    staff_connected = False
    for role_id in _configured_staff_role_ids(cfg):
        role = guild.get_role(role_id)
        if role is None:
            continue
        try:
            perms = vc_channel.permissions_for(role)  # type: ignore[attr-defined]
            if perms.view_channel and getattr(perms, "connect", False):
                staff_connected = True
                break
        except Exception:
            continue
    if staff_connected:
        ok.append("At least one configured staff/control role can access the VC verification channel.")
    elif _configured_staff_role_ids(cfg):
        warnings.append("No configured staff/control role clearly has VC verification channel access. Admins may still work, but staff flow may be confusing.")

    candidate_ids = _configured_text_channel_ids(cfg, include_fallbacks=True)
    if not candidate_ids:
        blockers.append("VC staff request panel has nowhere to post. Save a **VC queue/status text channel**, modlog, or transcript channel.")
        return

    writable_targets: list[str] = []
    checked_any = False
    for channel_id in candidate_ids:
        channel = guild.get_channel(channel_id)
        if channel is None:
            warnings.append(f"VC staff panel target is saved but missing/deleted: `{channel_id}`.")
            continue
        if not isinstance(channel, discord.TextChannel):
            warnings.append(f"VC staff panel target must be a text channel, but {_channel_name(channel)} is not text.")
            continue
        checked_any = True
        missing = _text_missing_perms(channel, bot_member)
        if missing:
            blockers.append(f"VC staff panel target {_channel_name(channel)} is missing bot permissions: {', '.join(missing)}.")
        else:
            writable_targets.append(_channel_name(channel))

    if writable_targets:
        ok.append(f"VC staff request panel can post to: {', '.join(writable_targets[:3])}.")
    elif checked_any:
        blockers.append("No saved VC staff panel target is writable. Fix the VC queue/status channel or fallback log channel permissions.")
    else:
        blockers.append("No saved VC staff panel target could be checked. Pick a real VC queue/status text channel in setup.")


def patch_setup_health_truth() -> bool:
    global _PATCHED_HEALTH
    if _PATCHED_HEALTH:
        return True

    try:
        from stoney_verify.commands_ext import public_setup_group
    except Exception as e:
        try:
            print(f"⚠️ vc_request_setup_clarity: public_setup_group import failed: {e!r}")
        except Exception:
            pass
        return False

    original = getattr(public_setup_group, "_build_setup_health", None)
    if not callable(original):
        return False

    if getattr(original, "_stoney_vc_truth_wrapped", False):
        _PATCHED_HEALTH = True
        return True

    def wrapped_build_setup_health(guild: discord.Guild, cfg: Any):  # type: ignore[no-untyped-def]
        blockers, warnings, ok = original(guild, cfg)
        try:
            blockers = list(blockers or [])
            warnings = list(warnings or [])
            ok = list(ok or [])
            _append_vc_runtime_health(guild, cfg, blockers, warnings, ok)
            return _dedupe_lines(blockers), _dedupe_lines(warnings), _dedupe_lines(ok)
        except Exception as e:
            blockers = list(blockers or [])
            blockers.append(f"VC runtime health check failed: {type(e).__name__}: {str(e)[:250]}")
            return _dedupe_lines(blockers), _dedupe_lines(warnings or []), _dedupe_lines(ok or [])

    try:
        setattr(wrapped_build_setup_health, "_stoney_vc_truth_wrapped", True)
    except Exception:
        pass

    setattr(public_setup_group, "_build_setup_health", wrapped_build_setup_health)

    # public_setup_solid imports _build_setup_health directly. If it is already
    # imported for any reason, update its local reference too.
    try:
        from stoney_verify.commands_ext import public_setup_solid

        setattr(public_setup_solid, "_build_setup_health", wrapped_build_setup_health)
    except Exception:
        pass

    _PATCHED_HEALTH = True
    try:
        print("✅ vc_request_setup_clarity: VC runtime health checks active")
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Per-guild VC flow alignment
# ---------------------------------------------------------------------------


async def _resolve_text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    if channel_id <= 0:
        return None
    try:
        channel = guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass
    try:
        fetched = await guild.fetch_channel(int(channel_id))
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass
    return None


async def _resolve_vc_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    if channel_id <= 0:
        return None
    try:
        channel = guild.get_channel(int(channel_id))
        if _is_voice_like(channel):
            return channel  # type: ignore[return-value]
    except Exception:
        pass
    try:
        fetched = await guild.fetch_channel(int(channel_id))
        if _is_voice_like(fetched):
            return fetched  # type: ignore[return-value]
    except Exception:
        pass
    return None


def _build_staff_panel_embed(guild: discord.Guild, cfg: Any, *, requester_id: int, requester_mention: str, ticket_channel_id: int, token: str) -> discord.Embed:
    member = guild.get_member(int(requester_id))
    display = f"{member.mention} — **{member.display_name}**" if member else (requester_mention or f"<@{requester_id}>")
    vc_id = _configured_vc_channel_id_from_cfg(cfg)

    embed = discord.Embed(
        title="🎙️ VC Verification Requested",
        description="Staff-only panel — choose how to handle this VC request.",
        color=discord.Color.dark_green(),
    )
    embed.add_field(name="User", value=f"{display}\n`{requester_id}`", inline=False)
    embed.add_field(name="Ticket", value=f"<#{int(ticket_channel_id)}>\n`{ticket_channel_id}`", inline=True)
    embed.add_field(name="VC Channel", value=(f"<#{vc_id}>\n`{vc_id}`" if vc_id > 0 else "`Not configured`"), inline=True)
    embed.add_field(name="Token", value=f"`{token}`", inline=False)
    embed.set_footer(text="Stoney Verify • VC staff panel")
    return embed


def _build_staff_panel_view(vc_flow: Any, token: str) -> discord.ui.View:
    builder = getattr(vc_flow, "_build_staff_vc_request_view", None)
    if callable(builder):
        try:
            return builder(str(token))
        except Exception:
            pass

    make_custom_id = getattr(vc_flow, "make_custom_id", None)
    if not callable(make_custom_id):
        def make_custom_id(action: str, raw_token: str) -> str:
            return f"{action}:{raw_token}"

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="✅ Accept VC Verify", style=discord.ButtonStyle.success, custom_id=make_custom_id("vc_accept", str(token))))
    view.add_item(discord.ui.Button(label="🔁 Ask for Upload Instead", style=discord.ButtonStyle.secondary, custom_id=make_custom_id("vc_upload", str(token))))
    view.add_item(discord.ui.Button(label="♻️ Reissue Token", style=discord.ButtonStyle.secondary, custom_id=make_custom_id("vc_reissue", str(token))))
    return view


async def _post_staff_panel_per_guild(vc_flow: Any, *, guild: discord.Guild, token: str, requester_id: int, requester_mention: str, ticket_channel_id: int) -> Optional[int]:
    cfg = await _get_guild_config_safe(guild)
    bot_member = _bot_member(guild)
    view = _build_staff_panel_view(vc_flow, str(token))
    embed = _build_staff_panel_embed(
        guild,
        cfg,
        requester_id=int(requester_id),
        requester_mention=str(requester_mention or ""),
        ticket_channel_id=int(ticket_channel_id),
        token=str(token),
    )

    ping = ""
    try:
        staff_ping_text = getattr(vc_flow, "_staff_ping_text", None)
        if callable(staff_ping_text):
            ping = str(staff_ping_text() or "").strip()
    except Exception:
        ping = ""

    candidate_ids = _configured_text_channel_ids(cfg, include_fallbacks=True)

    # Keep the old global/env fallback last for legacy servers, but never use a
    # channel from another guild because _resolve_text_channel checks this guild.
    for attr in ("VC_VERIFY_QUEUE_CHANNEL_ID", "VC_VERIFY_REQUESTS_CHANNEL_ID", "MODLOG_CHANNEL_ID", "TRANSCRIPTS_CHANNEL_ID"):
        try:
            import stoney_verify.globals as g

            candidate_ids.append(_safe_int(getattr(g, attr, 0), 0))
        except Exception:
            pass
    candidate_ids = _unique_ints(candidate_ids)

    tried_any = False
    for channel_id in candidate_ids:
        channel = await _resolve_text_channel(guild, channel_id)
        if channel is None:
            continue
        tried_any = True
        if bot_member is not None:
            missing = _text_missing_perms(channel, bot_member)
            if missing:
                try:
                    print(f"⚠️ vc_request_setup_clarity: VC staff panel target {channel.id} missing {', '.join(missing)}")
                except Exception:
                    pass
                continue
        try:
            msg = await channel.send(
                content=ping or None,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=False, roles=True, everyone=False),
            )
            try:
                cache_setter = getattr(vc_flow, "_set_vc_request_cache", None)
                if callable(cache_setter):
                    cache_setter(
                        str(token),
                        {
                            "staff_panel_msg_id": int(msg.id),
                            "staff_panel_channel_id": int(channel.id),
                            "staff_msg_ids": [int(msg.id)],
                            "staff_msg_refs": [{"channel_id": int(channel.id), "message_id": int(msg.id)}],
                        },
                    )
            except Exception:
                pass
            try:
                vc_sessions_mod = getattr(vc_flow, "_vc_sessions_mod", None)
                if vc_sessions_mod and hasattr(vc_sessions_mod, "set_queue_message"):
                    vc_sessions_mod.set_queue_message(token=str(token), queue_message_id=int(msg.id))
            except Exception:
                pass
            return int(msg.id)
        except Exception as e:
            try:
                print(f"⚠️ vc_request_setup_clarity: failed to post VC staff panel guild={guild.id} channel={channel.id}: {e!r}")
            except Exception:
                pass
            continue

    try:
        if tried_any:
            print(f"⚠️ vc_request_setup_clarity: no writable VC staff panel target guild={guild.id}")
        else:
            print(f"⚠️ vc_request_setup_clarity: no VC staff panel target resolved guild={guild.id}")
    except Exception:
        pass
    return None


def patch_vc_flow_per_guild_config() -> bool:
    global _PATCHED_VC_FLOW
    if _PATCHED_VC_FLOW:
        return True

    try:
        from stoney_verify.commands_ext import vc_flow
    except Exception as e:
        try:
            print(f"⚠️ vc_request_setup_clarity: vc_flow import failed: {e!r}")
        except Exception:
            pass
        return False

    if getattr(vc_flow, "_stoney_per_guild_vc_wrapped", False):
        _PATCHED_VC_FLOW = True
        return True

    original_get_vc_channel = getattr(vc_flow, "_get_vc_channel", None)
    original_get_queue = getattr(vc_flow, "_get_vc_queue_channel", None)
    original_get_staff_alert = getattr(vc_flow, "_get_staff_alert_channel", None)
    original_post_staff_panel = getattr(vc_flow, "_post_staff_vc_request_panel", None)

    def get_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        try:
            # Fast cache path. The async fetch fallback happens inside staff actions
            # when needed; this sync helper mirrors the original signature.
            cfg = None
            try:
                from stoney_verify.guild_config import guild_config_cache_snapshot

                snap = guild_config_cache_snapshot(int(guild.id))
                cfg = snap if snap is not None else None
            except Exception:
                cfg = None
            vc_id = _configured_vc_channel_id_from_cfg(cfg)
            if vc_id > 0:
                ch = guild.get_channel(vc_id)
                if _is_voice_like(ch):
                    return ch  # type: ignore[return-value]
        except Exception:
            pass
        if callable(original_get_vc_channel):
            try:
                return original_get_vc_channel(guild)
            except Exception:
                return None
        return None

    async def get_vc_queue_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
        cfg = await _get_guild_config_safe(guild)
        for channel_id in _configured_text_channel_ids(cfg, include_fallbacks=False):
            ch = await _resolve_text_channel(guild, channel_id)
            if ch is not None:
                return ch
        if callable(original_get_queue):
            try:
                old = await original_get_queue(guild)
                return old if isinstance(old, discord.TextChannel) else None
            except Exception:
                return None
        return None

    async def get_staff_alert_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
        cfg = await _get_guild_config_safe(guild)
        for channel_id in _configured_text_channel_ids(cfg, include_fallbacks=True):
            ch = await _resolve_text_channel(guild, channel_id)
            if ch is not None:
                return ch
        if callable(original_get_staff_alert):
            try:
                old = await original_get_staff_alert(guild)
                return old if isinstance(old, discord.TextChannel) else None
            except Exception:
                return None
        return None

    async def post_staff_panel(*args: Any, **kwargs: Any) -> Optional[int]:
        try:
            guild = kwargs.get("guild")
            if not isinstance(guild, discord.Guild):
                guild = _extract_guild_from_call(tuple(args), dict(kwargs))
            if isinstance(guild, discord.Guild):
                return await _post_staff_panel_per_guild(
                    vc_flow,
                    guild=guild,
                    token=str(kwargs.get("token") or ""),
                    requester_id=_safe_int(kwargs.get("requester_id"), 0),
                    requester_mention=str(kwargs.get("requester_mention") or ""),
                    ticket_channel_id=_safe_int(kwargs.get("ticket_channel_id"), 0),
                )
        except Exception as e:
            try:
                print(f"⚠️ vc_request_setup_clarity: per-guild staff panel wrapper failed: {e!r}")
            except Exception:
                pass
        if callable(original_post_staff_panel):
            return await original_post_staff_panel(*args, **kwargs)
        return None

    setattr(vc_flow, "_get_vc_channel", get_vc_channel)
    setattr(vc_flow, "_get_vc_queue_channel", get_vc_queue_channel)
    setattr(vc_flow, "_get_staff_alert_channel", get_staff_alert_channel)
    setattr(vc_flow, "_post_staff_vc_request_panel", post_staff_panel)
    setattr(vc_flow, "_stoney_per_guild_vc_wrapped", True)

    # If voice_verify has already imported these symbols, update its references.
    try:
        from stoney_verify.verification_new import voice_verify

        setattr(voice_verify, "_get_vc_channel", get_vc_channel)
        setattr(voice_verify, "_get_vc_queue_channel", get_vc_queue_channel)
        setattr(voice_verify, "_get_staff_alert_channel", get_staff_alert_channel)
        setattr(voice_verify, "_post_staff_vc_request_panel", post_staff_panel)
    except Exception:
        pass

    _PATCHED_VC_FLOW = True
    try:
        print("✅ vc_request_setup_clarity: per-guild VC queue/channel routing active")
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Clear error wording
# ---------------------------------------------------------------------------


def _looks_like_staff_panel_post_failure(message: Any) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return (
        "staff panel" in text
        and (
            "could not be posted" in text
            or "couldn't be posted" in text
            or "post" in text
            or "routing failed" in text
        )
    )


def _clear_vc_setup_message(original: Any = "") -> str:
    original_text = str(original or "").strip()
    details = (
        "VC verification is not ready yet because Stoney could not post the **staff VC request panel**.\n\n"
        "Staff should run `/stoney setup` → **Run Health Check**. It now checks the same VC queue/channel permissions this button needs.\n\n"
        "Fix the first blocker shown under Health Check, especially:\n"
        "• **VC queue/status text channel** exists and is writable.\n"
        "• Stoney has **View Channel**, **Send Messages**, **Embed Links**, and **Read Message History** in that channel.\n"
        "• The **VC verification voice channel** is locked from `@everyone`.\n"
        "• Stoney has **Manage Channels** on the VC verification voice channel."
    )
    if original_text:
        details += f"\n\nOriginal error: `{original_text[:500]}`"
    return details


def patch_vc_request_setup_clarity() -> bool:
    global _PATCHED_CLARITY
    if _PATCHED_CLARITY:
        return True

    try:
        from stoney_verify.commands_ext import vc_flow
    except Exception as e:
        try:
            print(f"⚠️ vc_request_setup_clarity: vc_flow import failed: {e!r}")
        except Exception:
            pass
        return False

    original: Callable[..., Awaitable[Dict[str, Any]]] | None = getattr(
        vc_flow,
        "create_vc_request_for_ticket",
        None,
    )
    if original is None or not callable(original):
        try:
            print("⚠️ vc_request_setup_clarity: create_vc_request_for_ticket not found")
        except Exception:
            pass
        return False

    if getattr(original, "_stoney_setup_clarity_wrapped", False):
        _PATCHED_CLARITY = True
        return True

    async def wrapped_create_vc_request_for_ticket(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = await original(*args, **kwargs)
        try:
            if not isinstance(result, dict):
                return result
            if bool(result.get("ok")):
                return result
            if _looks_like_staff_panel_post_failure(result.get("message")):
                patched = dict(result)
                patched["message"] = _clear_vc_setup_message(result.get("message"))
                patched["setup_hint"] = "Run /stoney setup -> Run Health Check. Fix VC queue/status, fallback log channel, and VC channel permissions."
                return patched
        except Exception:
            return result
        return result

    try:
        setattr(wrapped_create_vc_request_for_ticket, "_stoney_setup_clarity_wrapped", True)
    except Exception:
        pass

    setattr(vc_flow, "create_vc_request_for_ticket", wrapped_create_vc_request_for_ticket)
    _PATCHED_CLARITY = True

    try:
        print("✅ vc_request_setup_clarity: clearer VC setup failure messages active")
    except Exception:
        pass
    return True


patch_setup_health_truth()
patch_vc_flow_per_guild_config()
patch_vc_request_setup_clarity()


__all__ = [
    "patch_setup_health_truth",
    "patch_vc_flow_per_guild_config",
    "patch_vc_request_setup_clarity",
]
