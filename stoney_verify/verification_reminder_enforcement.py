from __future__ import annotations

"""Opt-in enforcement for three *delivered* scheduled verification reminders.

The existing reminder worker owns delivery; this module is the only consumer of
successful scheduled-delivery receipts for optional kick/ban. It never counts
manual reminders or attempted/failed DMs and never runs without explicit policy.
A reserved action is never automatically retried after an uncertain Discord
response; staff must review failures.
"""

import asyncio
from datetime import datetime
from typing import Any, Mapping

import discord

from .verification_reminder_postgres import _connect

ENFORCEMENT_TABLE = "public.dank_verify_reminder_enforcement"
MAX_ACTIONS_PER_GUILD_PASS = 2


def enforcement_ready_sync() -> bool:
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select guild_id, user_id, joined_at, delivered_count, "
                    "last_delivered_at, action_claimed_at, action_result "
                    "from public.dank_verify_reminder_enforcement limit 0"
                )
                for privilege in ("SELECT", "INSERT", "UPDATE"):
                    cursor.execute(
                        "select has_table_privilege(current_user, %s, %s)",
                        (ENFORCEMENT_TABLE, privilege),
                    )
                    row = cursor.fetchone()
                    if not row or row[0] is not True:
                        return False
        return True
    except Exception:
        return False


def record_delivery_sync(guild_id: int, user_id: int, joined_at: datetime) -> bool:
    """Record success only AFTER Discord acknowledges a scheduled send.

    Rows are keyed by the exact join generation; old joins cannot cause a
    rejoined member to be removed. Failure undercounts, never overcounts.
    """
    if guild_id <= 0 or user_id <= 0 or not isinstance(joined_at, datetime) or joined_at.tzinfo is None:
        return False
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into public.dank_verify_reminder_enforcement
                        (guild_id, user_id, joined_at, delivered_count, last_delivered_at)
                    values (%s, %s, %s, 1, now())
                    on conflict (guild_id, user_id, joined_at) do update
                    set delivered_count = least(5,
                        public.dank_verify_reminder_enforcement.delivered_count + 1),
                        last_delivered_at = now()
                    where public.dank_verify_reminder_enforcement.action_claimed_at is null
                    returning delivered_count
                    """,
                    (str(guild_id), str(user_id), joined_at),
                )
                return cursor.fetchone() is not None
    except Exception:
        return False


def due_candidates_sync(guild_id: int, *, grace_hours: int, limit: int = MAX_ACTIONS_PER_GUILD_PASS) -> list[tuple[int, datetime]]:
    if guild_id <= 0 or not 1 <= grace_hours <= 720 or not 1 <= limit <= 5:
        return []
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select user_id, joined_at from public.dank_verify_reminder_enforcement
                where guild_id = %s and delivered_count >= 3
                  and last_delivered_at <= now() - make_interval(hours => %s)
                  and action_claimed_at is null
                order by last_delivered_at, user_id limit %s
                """,
                (str(guild_id), grace_hours, limit),
            )
            return [(int(uid), joined) for uid, joined in cursor.fetchall()]


def reserve_action_sync(guild_id: int, user_id: int, joined_at: datetime, *, grace_hours: int) -> bool:
    """Atomic one-shot reservation across workers/shards/restarts."""
    if guild_id <= 0 or user_id <= 0 or not isinstance(joined_at, datetime) or joined_at.tzinfo is None or not 1 <= grace_hours <= 720:
        return False
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update public.dank_verify_reminder_enforcement
                    set action_claimed_at = now(), action_result = 'reserved'
                    where guild_id = %s and user_id = %s and joined_at = %s
                      and delivered_count >= 3
                      and last_delivered_at <= now() - make_interval(hours => %s)
                      and action_claimed_at is null
                    returning 1
                    """,
                    (str(guild_id), str(user_id), joined_at, grace_hours),
                )
                return cursor.fetchone() is not None
    except Exception:
        return False


def finish_action_sync(guild_id: int, user_id: int, joined_at: datetime, outcome: str) -> bool:
    if outcome not in {"kicked", "banned", "verified", "ineligible", "failed"}:
        return False
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update public.dank_verify_reminder_enforcement
                    set action_result = %s
                    where guild_id = %s and user_id = %s and joined_at = %s
                      and action_claimed_at is not null
                    returning 1
                    """,
                    (outcome, str(guild_id), str(user_id), joined_at),
                )
                return cursor.fetchone() is not None
    except Exception:
        return False


def _eligible(member: discord.Member, guild: discord.Guild, cfg: Mapping[str, Any], action: str, joined_at: datetime) -> bool:
    from .verification_reminders import pending_for_reminder, verification_channel, _as_int

    me = guild.me
    if not me or not getattr(member, "joined_at", None) == joined_at:
        return False
    if member.id == guild.owner_id or member.id == me.id or member.bot:
        return False
    if not pending_for_reminder(member, cfg):
        return False
    if not guild.get_role(_as_int(cfg.get("unverified_role_id"), 0)) or not guild.get_role(_as_int(cfg.get("verified_role_id"), 0)):
        return False
    channel = verification_channel(guild, cfg)
    if channel is None or not channel.permissions_for(member).view_channel:
        return False
    member_permissions = member.guild_permissions
    if any((
        member_permissions.administrator,
        member_permissions.manage_guild,
        member_permissions.kick_members,
        member_permissions.ban_members,
    )):
        return False
    if action == "kick" and not me.guild_permissions.kick_members:
        return False
    if action == "ban" and not me.guild_permissions.ban_members:
        return False
    return bool(me.top_role > member.top_role)


async def _write_modlog(guild: discord.Guild, cfg: Mapping[str, Any], member_id: int, action: str) -> None:
    try:
        channel = guild.get_channel(int(str(cfg.get("modlog_channel_id") or "0")))
        me = guild.me
        if not isinstance(channel, discord.TextChannel) or me is None:
            return
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return
        card = discord.Embed(
            title=f"Verification auto-{action}",
            description=(
                f"Member ID: `{member_id}`\\n"
                "Reason: three confirmed scheduled verification reminders, "
                "grace period elapsed, member still unverified.\\n"
                "Actor: Dank Shield automated verification policy."
            ),
            color=discord.Color.orange(),
        )
        await channel.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        print(f"⚠️ verify enforcement modlog unavailable guild={guild.id} kind={type(exc).__name__}")


async def enforce_guild(guild: discord.Guild) -> int:
    """Bounded, fail-closed enforcement pass; never triggered by manual sends."""
    from .guild_config import get_guild_config
    from .setup_engine.verification_modes import basic_verify_allowed_for_guild
    from .verification_reminders import parse_policy

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        policy = parse_policy(cfg)
        if (not policy.enabled or policy.max_reminders != 3
                or policy.after_max_action not in {"kick", "ban"}
                or str(cfg.get("source") or "").startswith(("unavailable:", "unconfigured:"))
                or not basic_verify_allowed_for_guild(guild, cfg)
                or not await asyncio.to_thread(enforcement_ready_sync)):
            return 0
        rows = await asyncio.to_thread(
            due_candidates_sync, guild.id, grace_hours=policy.after_max_grace_hours,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ verify enforcement scan unavailable guild={guild.id} kind={type(exc).__name__}")
        return 0

    completed = 0
    for user_id, joined in rows:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            continue
        except (discord.Forbidden, discord.HTTPException):
            continue
        if not _eligible(member, guild, cfg, policy.after_max_action, joined):
            continue

        # Re-read both guild policy and member truth immediately before claiming.
        try:
            fresh = await get_guild_config(guild.id, refresh=True)
            latest = parse_policy(fresh)
            if latest != policy or not basic_verify_allowed_for_guild(guild, fresh):
                continue
            member = await guild.fetch_member(user_id)
            if not _eligible(member, guild, fresh, policy.after_max_action, joined):
                continue
            reserved = await asyncio.to_thread(
                reserve_action_sync, guild.id, user_id, joined,
                grace_hours=policy.after_max_grace_hours,
            )
            if not reserved:
                continue
        except asyncio.CancelledError:
            raise
        except (discord.Forbidden, discord.HTTPException, Exception):
            continue

        outcome = "failed"
        try:
            # A reservation survives restarts and uncertain HTTP responses.
            # A newly verified member must never be punished.
            fresh = await get_guild_config(guild.id, refresh=True)
            if parse_policy(fresh) != policy:
                outcome = "ineligible"
                continue
            current = await guild.fetch_member(user_id)
            if not _eligible(current, guild, fresh, policy.after_max_action, joined):
                outcome = "verified"
                continue
            reason = "Dank Shield: 3 delivered verification reminders; grace period expired"
            if policy.after_max_action == "kick":
                await guild.kick(current, reason=reason)
                outcome = "kicked"
            else:
                await guild.ban(current, reason=reason, delete_message_seconds=0)
                outcome = "banned"
            completed += 1
            await _write_modlog(guild, fresh, user_id, outcome)
        except asyncio.CancelledError:
            raise
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ verify enforcement denied guild={guild.id} member={user_id} kind={type(exc).__name__}")
        except Exception as exc:
            print(f"⚠️ verify enforcement failed guild={guild.id} member={user_id} kind={type(exc).__name__}")
        finally:
            saved = await asyncio.to_thread(finish_action_sync, guild.id, user_id, joined, outcome)
            if not saved:
                print(f"⚠️ verify enforcement outcome ledger unavailable guild={guild.id} member={user_id}")
        await asyncio.sleep(0)
    return completed
