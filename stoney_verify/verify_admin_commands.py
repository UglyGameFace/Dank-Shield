from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from .globals import *
from .raidguard import build_member_risk_profile
from .transcripts import ensure_verify_ui_present, find_last_verify_ui_message


def _is_staffish(member: discord.Member) -> bool:
    try:
        if member.guild_permissions.administrator:
            return True
    except Exception:
        pass
    try:
        if member.guild_permissions.manage_guild or member.guild_permissions.manage_channels:
            return True
    except Exception:
        pass
    try:
        staff_role_id = int(STAFF_ROLE_ID or 0)
        if staff_role_id and any(int(r.id) == staff_role_id for r in (member.roles or [])):
            return True
    except Exception:
        pass
    return False


async def _safe_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        pass


async def _safe_defer_ephemeral(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


def _sync_iso_now() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_string_list(value: Any, max_items: int = 20) -> List[str]:
    out: List[str] = []

    try:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
        elif value is not None:
            text = str(value).strip()
            if text:
                out.append(text)
    except Exception:
        pass

    return out[:max_items]


def _member_label(member: discord.Member) -> str:
    try:
        return f"{member.display_name} ({member.name})"
    except Exception:
        return str(member)


def _risk_db_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    now_iso = _sync_iso_now()

    score = max(0, min(100, _as_int(profile.get("score"), 0)))
    level_raw = _normalize_text(profile.get("level")).lower()
    level = level_raw if level_raw in {"low", "medium", "high", "critical"} else "low"

    fingerprint = _normalize_text(profile.get("fingerprint")) or None
    same_fingerprint_count = max(0, _as_int(profile.get("same_fingerprint_count"), 0))
    similar_name_count = max(0, _as_int(profile.get("similar_name_count"), 0))
    same_age_bucket_count = max(0, _as_int(profile.get("same_age_bucket_count"), 0))
    burst_join_count = max(0, _as_int(profile.get("burst_count"), 0))

    alt_cluster_key = _normalize_text(profile.get("alt_cluster_key")) or None
    alt_cluster_size = max(0, _as_int(profile.get("alt_cluster_size"), 0))
    if alt_cluster_size <= 0 and alt_cluster_key:
        alt_cluster_size = 1 + max(
            same_fingerprint_count,
            similar_name_count,
            same_age_bucket_count,
        )

    return {
        "risk_score": score,
        "risk_level": level,
        "risk_reasons": _safe_string_list(profile.get("reasons"), 12),
        "fingerprint": fingerprint,
        "alt_cluster_key": alt_cluster_key,
        "alt_cluster_size": alt_cluster_size,
        "burst_join_count": burst_join_count,
        "same_fingerprint_count": same_fingerprint_count,
        "similar_name_count": similar_name_count,
        "same_age_bucket_count": same_age_bucket_count,
        "suspicious_name_pattern": bool(profile.get("suspicious_name_pattern")),
        "repeated_char_pattern": bool(profile.get("repeated_char_pattern")),
        "default_avatar": bool(profile.get("default_avatar")),
        "account_age_days": _as_int(profile.get("account_age_days"), 0),
        "age_bucket": _normalize_text(profile.get("age_bucket")) or None,
        "digit_ratio": float(profile.get("digit_ratio") or 0.0),
        "underscore_ratio": float(profile.get("underscore_ratio") or 0.0),
        "cluster_members": profile.get("cluster_members") if isinstance(profile.get("cluster_members"), list) else [],
        "suspicion_flags": _safe_string_list(profile.get("suspicion_flags"), 20),
        "risk_last_evaluated_at": now_iso,
        "last_join_risk_score": score,
        "last_join_risk_level": level,
        "last_join_fingerprint": fingerprint,
        "synced_at": now_iso,
        "updated_at": now_iso,
        "last_seen_at": now_iso,
    }


async def _local_update_member_risk(member: discord.Member, profile: Dict[str, Any]) -> None:
    sb = get_supabase()
    if not sb:
        raise RuntimeError("Supabase is not configured.")

    guild_id = str(member.guild.id)
    user_id = str(member.id)
    now_iso = _sync_iso_now()

    payload = {
        "guild_id": guild_id,
        "user_id": user_id,
        "username": _normalize_text(getattr(member, "name", None) or ""),
        "display_name": _normalize_text(getattr(member, "display_name", None) or ""),
        "nickname": _normalize_text(getattr(member, "nick", None) or ""),
        "avatar_url": str(member.display_avatar.url) if getattr(member, "display_avatar", None) else None,
        "in_guild": True,
        "data_health": "ok",
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "synced_at": now_iso,
        "updated_at": now_iso,
        "last_seen_at": now_iso,
        **_risk_db_payload(profile),
    }

    def _update():
        try:
            return (
                sb.table("guild_members")
                .update(payload)
                .eq("guild_id", guild_id)
                .eq("user_id", user_id)
                .execute()
            )
        except Exception:
            return None

    def _upsert():
        return sb.table("guild_members").upsert(payload, on_conflict="guild_id,user_id").execute()

    try:
        await asyncio.to_thread(_update)
    except Exception:
        pass

    await asyncio.to_thread(_upsert)


async def _persist_member_risk(member: discord.Member, profile: Dict[str, Any]) -> None:
    try:
        from .events import _sync_member_to_supabase  # type: ignore

        await _sync_member_to_supabase(member, in_guild=True, risk_profile=profile)
        return
    except Exception:
        pass

    await _local_update_member_risk(member, profile)


def _format_profile_lines(member: discord.Member, profile: Dict[str, Any]) -> List[str]:
    reasons = _safe_string_list(profile.get("reasons"), 8)
    flags = _safe_string_list(profile.get("suspicion_flags"), 12)
    cluster_members = profile.get("cluster_members") if isinstance(profile.get("cluster_members"), list) else []

    lines = [
        f"**Risk recomputed for:** {member.mention}",
        f"**User:** `{_member_label(member)}`",
        f"**User ID:** `{member.id}`",
        f"**Score:** `{_as_int(profile.get('score'), 0)}/100`",
        f"**Level:** `{_normalize_text(profile.get('level')).lower() or 'low'}`",
        f"**Fingerprint:** `{_normalize_text(profile.get('fingerprint')) or 'unknown'}`",
        f"**Age bucket:** `{_normalize_text(profile.get('age_bucket')) or 'unknown'}`",
        f"**Account age:** `{_as_int(profile.get('account_age_days'), 0)} day(s)`",
        f"**Burst count:** `{_as_int(profile.get('burst_count'), 0)}`",
        f"**FP matches:** `{_as_int(profile.get('same_fingerprint_count'), 0)}`",
        f"**Name matches:** `{_as_int(profile.get('similar_name_count'), 0)}`",
        f"**Age-bucket matches:** `{_as_int(profile.get('same_age_bucket_count'), 0)}`",
        f"**Default avatar:** `{'yes' if bool(profile.get('default_avatar')) else 'no'}`",
    ]

    if flags:
        lines.append(f"**Flags:** {', '.join(f'`{x}`' for x in flags[:10])}")

    if reasons:
        lines.append("**Reasons:**")
        lines.extend([f"• {reason}" for reason in reasons[:6]])

    if cluster_members:
        lines.append("**Linked recent members:**")
        for row in cluster_members[:5]:
            username = _normalize_text(row.get("username")) or _normalize_text(row.get("display_name")) or "unknown"
            user_id = _normalize_text(row.get("user_id")) or "unknown"
            reason = _normalize_text(row.get("reason")) or "linked"
            lines.append(f"• `{username}` (`{user_id}`) • {reason}")

    return lines


def _render_top_risky(top_rows: List[Tuple[discord.Member, Dict[str, Any]]], limit: int = 10) -> List[str]:
    lines: List[str] = []

    for member, profile in sorted(
        top_rows,
        key=lambda row: _as_int(row[1].get("score"), 0),
        reverse=True,
    )[:limit]:
        lines.append(
            f"• `{_member_label(member)}` (`{member.id}`) "
            f"score=`{_as_int(profile.get('score'), 0)}` "
            f"level=`{_normalize_text(profile.get('level')).lower() or 'low'}` "
            f"fp=`{_as_int(profile.get('same_fingerprint_count'), 0)}` "
            f"name=`{_as_int(profile.get('similar_name_count'), 0)}` "
            f"burst=`{_as_int(profile.get('burst_count'), 0)}`"
        )

    return lines


async def _get_members_for_recompute(guild: discord.Guild, max_members: int = 0) -> List[discord.Member]:
    members: List[discord.Member]

    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except Exception:
        members = list(getattr(guild, "members", []) or [])

    filtered = [m for m in members if isinstance(m, discord.Member) and not getattr(m, "bot", False)]

    if max_members > 0:
        filtered = filtered[:max_members]

    return filtered


def _register_verify_admin_commands() -> None:
    if bot.tree.get_command("repair_verify_ui") is None:
        @bot.tree.command(
            name="repair_verify_ui",
            description="(Staff) Repair the verification instructions and UI in this ticket.",
        )
        @app_commands.guild_only()
        async def repair_verify_ui(interaction: discord.Interaction):
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            channel = interaction.channel

            if not isinstance(member, discord.Member):
                return await _safe_ephemeral(interaction, "This command must be used inside the server.")

            if not isinstance(channel, discord.TextChannel):
                return await _safe_ephemeral(interaction, "This command can only be used in a text ticket.")

            if not _is_staffish(member):
                return await _safe_ephemeral(interaction, "You do not have permission to use this command.")

            await _safe_defer_ephemeral(interaction)

            try:
                existing = await find_last_verify_ui_message(channel)
            except Exception:
                existing = None

            try:
                await ensure_verify_ui_present(channel, reason="staff_repair_verify_ui")
            except Exception as e:
                return await _safe_ephemeral(
                    interaction,
                    f"Failed to repair verification UI: {repr(e)[:250]}",
                )

            try:
                repaired = await find_last_verify_ui_message(channel)
            except Exception:
                repaired = None

            if repaired:
                if existing and int(getattr(existing, "id", 0) or 0) == int(getattr(repaired, "id", 0) or 0):
                    return await _safe_ephemeral(
                        interaction,
                        f"Verification UI already existed and has been checked in {channel.mention}.",
                    )
                return await _safe_ephemeral(
                    interaction,
                    f"Verification UI repaired successfully in {channel.mention}.",
                )

            return await _safe_ephemeral(
                interaction,
                "Repair attempted, but no verification UI message could be confirmed afterward.",
            )

    if bot.tree.get_command("recompute_member_risk") is None:
        @bot.tree.command(
            name="recompute_member_risk",
            description="(Staff) Recompute and save alt-risk data for one member.",
        )
        @app_commands.guild_only()
        @app_commands.describe(member="The member whose risk profile should be recomputed.")
        async def recompute_member_risk(
            interaction: discord.Interaction,
            member: discord.Member,
        ):
            staff_member = interaction.user if isinstance(interaction.user, discord.Member) else None

            if not isinstance(staff_member, discord.Member):
                return await _safe_ephemeral(interaction, "This command must be used inside the server.")

            if not _is_staffish(staff_member):
                return await _safe_ephemeral(interaction, "You do not have permission to use this command.")

            if getattr(member, "bot", False):
                return await _safe_ephemeral(interaction, "That member is a bot. This command is for human member risk.")

            await _safe_defer_ephemeral(interaction)

            try:
                profile = build_member_risk_profile(member)
                await _persist_member_risk(member, profile)
            except Exception as e:
                return await _safe_ephemeral(
                    interaction,
                    f"Failed to recompute risk for `{member.id}`: {repr(e)[:350]}",
                )

            lines = _format_profile_lines(member, profile)
            return await _safe_ephemeral(interaction, "\n".join(lines[:25]))

    if bot.tree.get_command("recompute_all_member_risk") is None:
        @bot.tree.command(
            name="recompute_all_member_risk",
            description="(Staff) Recompute and save alt-risk data for all current members.",
        )
        @app_commands.guild_only()
        @app_commands.describe(
            max_members="Optional cap. Use 0 to process all current human members.",
        )
        async def recompute_all_member_risk(
            interaction: discord.Interaction,
            max_members: Optional[int] = 0,
        ):
            staff_member = interaction.user if isinstance(interaction.user, discord.Member) else None
            guild = interaction.guild

            if not isinstance(staff_member, discord.Member):
                return await _safe_ephemeral(interaction, "This command must be used inside the server.")

            if guild is None:
                return await _safe_ephemeral(interaction, "Guild context is missing.")

            if not _is_staffish(staff_member):
                return await _safe_ephemeral(interaction, "You do not have permission to use this command.")

            await _safe_defer_ephemeral(interaction)

            safe_cap = max(0, int(max_members or 0))

            try:
                members = await _get_members_for_recompute(guild, max_members=safe_cap)
            except Exception as e:
                return await _safe_ephemeral(
                    interaction,
                    f"Failed to load members for recompute: {repr(e)[:300]}",
                )

            if not members:
                return await _safe_ephemeral(interaction, "No human members were found to recompute.")

            updated = 0
            failed = 0
            level_counts: Dict[str, int] = {
                "low": 0,
                "medium": 0,
                "high": 0,
                "critical": 0,
            }
            total_score = 0
            top_rows: List[Tuple[discord.Member, Dict[str, Any]]] = []
            failures: List[str] = []

            for idx, target in enumerate(members, start=1):
                try:
                    profile = build_member_risk_profile(target)
                    await _persist_member_risk(target, profile)

                    level = _normalize_text(profile.get("level")).lower() or "low"
                    if level not in level_counts:
                        level = "low"

                    level_counts[level] += 1
                    total_score += _as_int(profile.get("score"), 0)
                    updated += 1
                    top_rows.append((target, profile))
                except Exception as e:
                    failed += 1
                    if len(failures) < 8:
                        failures.append(f"`{target.id}` • {repr(e)[:180]}")
                finally:
                    if idx % 10 == 0:
                        await asyncio.sleep(0)

            avg_score = round(total_score / updated, 2) if updated > 0 else 0.0
            summary_lines = [
                "**Member risk recompute complete.**",
                f"**Guild:** `{guild.name}` (`{guild.id}`)",
                f"**Processed:** `{len(members)}`",
                f"**Updated:** `{updated}`",
                f"**Failed:** `{failed}`",
                f"**Average score:** `{avg_score}`",
                f"**Critical:** `{level_counts['critical']}`",
                f"**High:** `{level_counts['high']}`",
                f"**Medium:** `{level_counts['medium']}`",
                f"**Low:** `{level_counts['low']}`",
            ]

            top_lines = _render_top_risky(top_rows, limit=10)
            if top_lines:
                summary_lines.append("")
                summary_lines.append("**Top risky members:**")
                summary_lines.extend(top_lines)

            if failures:
                summary_lines.append("")
                summary_lines.append("**Failures:**")
                summary_lines.extend(failures[:6])

            message = "\n".join(summary_lines)
            if len(message) > 1900:
                message = message[:1890] + "…"

            return await _safe_ephemeral(interaction, message)


def _verify_admin_commands_enabled() -> bool:
    """Legacy top-level verify admin commands are dev/admin-only.

    ``repair_verify_ui``, ``recompute_member_risk`` and
    ``recompute_all_member_risk`` are internal maintenance commands. They stay
    out of the public command surface (which is near Discord's 100 global-command
    cap and should not confuse normal server owners) unless explicitly opted in.

    Semantics mirror ``startup_guards.public_verify_admin_command_skip`` so the
    two stay consistent: enabled only for dev/full/public-admin profiles or when
    ``DANK_EXPOSE_VERIFY_ADMIN_COMMANDS`` is set.
    """
    import os

    raw_expose = os.getenv("DANK_EXPOSE_VERIFY_ADMIN_COMMANDS", "")
    if str(raw_expose or "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return True

    profile = str(os.getenv("DANK_COMMAND_PROFILE", "public")).strip().lower()
    return profile in {"public-admin", "dev", "full"}


if _verify_admin_commands_enabled():
    _register_verify_admin_commands()
else:
    try:
        print(
            "🧭 verify_admin_commands: legacy top-level commands hidden in public "
            "profile (set DANK_COMMAND_PROFILE=dev or "
            "DANK_EXPOSE_VERIFY_ADMIN_COMMANDS=true to enable)"
        )
    except Exception:
        pass