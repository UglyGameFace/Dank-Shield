from __future__ import annotations

"""Bounded, single-host durable intake queue for Share Router Download channels.

Stores Discord snowflake IDs only; video/source URLs never enter the queue DB.
One processing job per member and one per guild at a time. Unacknowledged
running jobs are NOT replayed after a crash, because a DM may already have
been delivered before the process was interrupted.
"""

import asyncio
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import discord

from .share_router_private_downloads import (
    _reply_problem,
    _valid_request_url,
    handle_download_message,
)

MAX_PENDING_TOTAL = 256
MAX_PENDING_PER_GUILD = 10
MAX_WORKERS = 2
MAX_QUEUED_AGE_SECONDS = 6 * 3600
_IDLE_POLL_SECONDS = 2.0

_WORKERS: list[asyncio.Task[None]] = []
_WORKER_BOT: Any = None
_LAST_GUILD_ID = ""
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY_PATH = ""


def _db_path() -> Path:
    return Path(
        os.getenv(
            "DANK_SHARE_DOWNLOAD_QUEUE_DB",
            str(Path(os.getenv("DANK_DATA_DIR", "data")) / "share_download_queue.sqlite3"),
        )
    )


@dataclass(frozen=True)
class DownloadJob:
    id: int
    guild_id: int
    channel_id: int
    member_id: int
    message_id: int


def _connect() -> sqlite3.Connection:
    global _SCHEMA_READY_PATH
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise OSError("Share Router queue cannot use a symlink")
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass  # Another queue worker created it after the existence check.
        else:
            os.close(fd)
    conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        # Schema and WAL setup are intentionally one-time operations. Repeating
        # PRAGMA journal_mode for every 2s idle poll can contend with writers.
        with _SCHEMA_LOCK:
            if _SCHEMA_READY_PATH != str(path):
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS download_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        member_id TEXT NOT NULL,
                        message_id TEXT NOT NULL UNIQUE,
                        created_at REAL NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('queued', 'running'))
                    )
                """)
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS download_queue_one_member "
                    "ON download_queue (guild_id, member_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS download_queue_pick "
                    "ON download_queue (status, created_at)"
                )
                _SCHEMA_READY_PATH = str(path)
        return conn
    except Exception:
        conn.close()
        raise


def _submit(guild_id: int, channel_id: int, member_id: int, message_id: int) -> str:
    if min(guild_id, channel_id, member_id, message_id) <= 0:
        return "invalid"
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        conn.execute(
            "DELETE FROM download_queue WHERE status='queued' AND created_at < ?",
            (now - MAX_QUEUED_AGE_SECONDS,),
        )
        if conn.execute("SELECT 1 FROM download_queue WHERE message_id=?", (str(message_id),)).fetchone():
            conn.commit()
            return "duplicate"
        if conn.execute(
            "SELECT 1 FROM download_queue WHERE guild_id=? AND member_id=?",
            (str(guild_id), str(member_id)),
        ).fetchone():
            conn.commit()
            return "member_busy"
        if conn.execute("SELECT COUNT(*) FROM download_queue").fetchone()[0] >= MAX_PENDING_TOTAL:
            conn.commit()
            return "full"
        if conn.execute(
            "SELECT COUNT(*) FROM download_queue WHERE guild_id=?", (str(guild_id),)
        ).fetchone()[0] >= MAX_PENDING_PER_GUILD:
            conn.commit()
            return "guild_full"
        conn.execute(
            "INSERT INTO download_queue (guild_id,channel_id,member_id,message_id,created_at,status) "
            "VALUES (?,?,?,?,?,'queued')",
            (str(guild_id), str(channel_id), str(member_id), str(message_id), now),
        )
        conn.commit()
        return "queued"
    except sqlite3.IntegrityError:
        conn.rollback()
        return "duplicate"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _claim_next(previous_guild_id: str = "") -> Optional[DownloadJob]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        conn.execute(
            "DELETE FROM download_queue WHERE status='queued' AND created_at < ?",
            (now - MAX_QUEUED_AGE_SECONDS,),
        )
        row = conn.execute(
            """
            SELECT q.id, q.guild_id, q.channel_id, q.member_id, q.message_id
            FROM download_queue q
            WHERE q.status='queued'
              AND NOT EXISTS (
                SELECT 1 FROM download_queue active
                WHERE active.guild_id=q.guild_id AND active.status='running'
              )
            ORDER BY CASE WHEN q.guild_id=? THEN 1 ELSE 0 END,
                     q.created_at, q.id
            LIMIT 1
            """,
            (previous_guild_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute("UPDATE download_queue SET status='running' WHERE id=?", (row[0],))
        conn.commit()
        return DownloadJob(*(int(item) for item in row))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _complete(job_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM download_queue WHERE id=?", (int(job_id),))
    finally:
        conn.close()


def _recover_interrupted() -> int:
    """Fail closed: never automatically replay a potentially delivered DM."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM download_queue WHERE status='running'")
        conn.execute(
            "DELETE FROM download_queue WHERE status='queued' AND created_at < ?",
            (time.time() - MAX_QUEUED_AGE_SECONDS,),
        )
        conn.commit()
        return max(0, cur.rowcount)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _snapshot() -> dict[str, int]:
    conn = _connect()
    try:
        rows = dict(conn.execute(
            "SELECT status, COUNT(*) FROM download_queue GROUP BY status"
        ).fetchall())
        return {
            "queued": int(rows.get("queued", 0)),
            "running": int(rows.get("running", 0)),
            "limit": MAX_PENDING_TOTAL,
            "per_guild_limit": MAX_PENDING_PER_GUILD,
            "workers": MAX_WORKERS,
        }
    finally:
        conn.close()


async def queue_snapshot() -> dict[str, int]:
    return await asyncio.to_thread(_snapshot)


async def enqueue_download_request(message: discord.Message) -> bool:
    """Fast gateway path: validate and queue IDs; never fetch/download media here."""
    guild = message.guild
    if guild is None or not isinstance(message.author, discord.Member):
        return False
    if message.author.bot or getattr(message, "webhook_id", None):
        return False
    try:
        perms = message.channel.permissions_for(message.author)
        if not perms.view_channel or not perms.send_messages or message.channel.is_nsfw():
            return True
    except Exception:
        return True
    if not _valid_request_url(message.content):
        await _reply_problem(message, "Send one supported public video URL per request.")
        return True
    try:
        status = await asyncio.to_thread(
            _submit,
            int(guild.id), int(message.channel.id),
            int(message.author.id), int(message.id),
        )
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"⚠️ share_router download queue unavailable error={type(exc).__name__}")
        await _reply_problem(message, "Download queue is temporarily unavailable. Your source post was preserved.")
        return True
    if status in {"full", "guild_full"}:
        await _reply_problem(message, "Downloads are busy. Queue capacity is limited; please try again later.")
    elif status == "queued":
        # Reaction is best-effort and not needed to preserve the queue job.
        try:
            await message.add_reaction("📥")
        except (AttributeError, discord.HTTPException):
            pass
    return True


async def _process_job(bot: Any, job: DownloadJob) -> None:
    # No cached message or member objects survive a restart. Always verify
    # the actual guild/channel/member and saved opt-in channel at delivery time.
    guild = bot.get_guild(job.guild_id)
    if guild is None:
        return
    from .share_router_runtime import guild_download_channel_id, guild_download_delivery
    if await guild_download_channel_id(job.guild_id) != job.channel_id:
        return
    mode, _ = await guild_download_delivery(job.guild_id)
    if mode == "off":
        return
    channel = guild.get_channel(job.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(job.message_id)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return
    if (
        message.guild is None
        or int(message.author.id) != job.member_id
        or int(message.channel.id) != job.channel_id
        or getattr(message.author, "bot", False)
    ):
        return
    # REST message fetches can return a User even for an existing guild member.
    # The canonical sender-permission gate requires an actual discord.Member.
    if not isinstance(message.author, discord.Member):
        member = guild.get_member(job.member_id)
        if member is None:
            try:
                member = await guild.fetch_member(job.member_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return
        if not isinstance(member, discord.Member) or member.bot:
            return
        message.author = member
    # Preserve the existing size, SSRF, cooldown, per-guild permissions, DM
    # delivery and successful-source-cleanup ownership.
    await handle_download_message(message)


async def _worker(bot: Any, index: int) -> None:
    global _LAST_GUILD_ID
    while True:
        try:
            job = await asyncio.to_thread(_claim_next, _LAST_GUILD_ID)
            if job is None:
                await asyncio.sleep(_IDLE_POLL_SECONDS)
                continue
            _LAST_GUILD_ID = str(job.guild_id)
            try:
                await _process_job(bot, job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ share_router download worker={index} error={type(exc).__name__}")
            finally:
                # Never replay ambiguously delivered DMs after an exception.
                await asyncio.shield(asyncio.to_thread(_complete, job.id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ share_router download queue worker={index} unavailable={type(exc).__name__}")
            await asyncio.sleep(10)


async def start_download_workers(bot: Any) -> None:
    global _WORKERS, _WORKER_BOT, _LAST_GUILD_ID
    if _WORKER_BOT is bot and any(not task.done() for task in _WORKERS):
        return
    if any(not task.done() for task in _WORKERS):
        # Never allow a second bot owner to duplicate the same DB queue.
        return
    try:
        interrupted = await asyncio.to_thread(_recover_interrupted)
    except (sqlite3.Error, OSError) as exc:
        print(f"⚠️ share_router download workers not started: {type(exc).__name__}")
        return
    if interrupted:
        print(f"⚠️ share_router download recovery skipped {interrupted} ambiguous jobs; source posts preserved")
    _WORKER_BOT = bot
    _LAST_GUILD_ID = ""
    _WORKERS = [
        asyncio.create_task(_worker(bot, i), name=f"share-download-worker-{i}")
        for i in range(MAX_WORKERS)
    ]


__all__ = ["enqueue_download_request", "queue_snapshot", "start_download_workers"]
