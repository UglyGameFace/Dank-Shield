from __future__ import annotations

"""Canonical external cleanup for Movie Night room termination and idle expiry."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from stoney_verify.movie_night import MovieNightRoom, get_movie_night_manager, movie_room_lease_key
from stoney_verify.torrent_streaming import get_torrent_manager


log = logging.getLogger(__name__)
_CLEANUP_TASK: Optional[asyncio.Task[None]] = None


@dataclass(frozen=True)
class MovieNightTerminationResult:
    had_stream: bool
    lease_released: bool
    cleanup_error: str = ""


async def terminate_movie_night_room(room: MovieNightRoom) -> MovieNightTerminationResult:
    """Release this room's torrent lease and retire its in-memory media state.

    The room may already be marked ended by a passed vote or host action. Cleanup
    is intentionally idempotent so every termination doorway can use one owner.
    Shared torrents remain alive while another room still holds a lease.
    """

    manager = get_movie_night_manager()
    current = manager.get(room.room_id) or room
    if not current.ended:
        manager.apply_host_action(
            current.room_id,
            host_id=int(current.host_id),
            action="end",
        )

    stream_token = str(current.stream_token or "")
    had_stream = bool(stream_token)
    lease_released = not had_stream
    cleanup_error = ""

    if stream_token:
        try:
            lease_released = bool(
                await get_torrent_manager().release_lease(
                    stream_token,
                    movie_room_lease_key(current.guild_id, current.channel_id),
                    remove_if_unused=True,
                )
            )
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"[:240]

    # Retire all room-owned playback/search state even if the backing torrent
    # manager reports that the session was already gone. An ended room is not
    # considered active, so a fresh Movie Night may start in the same channel.
    current.stream_token = ""
    current.current_candidate_id = ""
    current.current_variant_id = ""
    current.approved_search_query = ""
    current.queue.clear()
    current.candidates.clear()

    for vote in current.votes.values():
        if not vote.resolved:
            vote.resolved = True
            vote.passed = False

    manager.retire_room(current.room_id)

    return MovieNightTerminationResult(
        had_stream=had_stream,
        lease_released=lease_released,
        cleanup_error=cleanup_error,
    )



async def cleanup_inactive_movie_night_rooms() -> int:
    """End rooms that have had no active viewers/presence for the configured TTL.

    Eligibility is re-checked immediately before the room is marked ended. The
    ended state transition is synchronous, so a late join cannot race in after
    the final check while the torrent lease is being released.
    """

    manager = get_movie_night_manager()
    cleaned = 0
    for candidate in manager.inactive_room_candidates():
        current = manager.get(candidate.room_id)
        if current is None or current.ended:
            continue
        if not manager.room_empty_expired(current):
            continue

        try:
            manager.apply_host_action(
                current.room_id,
                host_id=int(current.host_id),
                action="end",
            )
            result = await terminate_movie_night_room(current)
            cleaned += 1
            if result.cleanup_error:
                log.warning(
                    "Dank Cinema inactive-room cleanup completed with media cleanup error "
                    "room=%s guild=%s channel=%s error=%s",
                    current.room_id,
                    current.guild_id,
                    current.channel_id,
                    result.cleanup_error,
                )
            else:
                log.info(
                    "Dank Cinema inactive room auto-ended room=%s guild=%s channel=%s",
                    current.room_id,
                    current.guild_id,
                    current.channel_id,
                )
        except Exception:
            log.exception(
                "Dank Cinema inactive-room cleanup failed room=%s guild=%s channel=%s",
                getattr(current, "room_id", "-"),
                getattr(current, "guild_id", "-"),
                getattr(current, "channel_id", "-"),
            )
    return cleaned


async def _movie_night_cleanup_loop() -> None:
    try:
        while True:
            await asyncio.sleep(60.0)
            await cleanup_inactive_movie_night_rooms()
    except asyncio.CancelledError:
        return


def ensure_movie_night_cleanup_task() -> None:
    global _CLEANUP_TASK
    try:
        if _CLEANUP_TASK is not None and not _CLEANUP_TASK.done():
            return
        _CLEANUP_TASK = asyncio.create_task(
            _movie_night_cleanup_loop(),
            name="movie_night_room_cleanup",
        )
    except RuntimeError:
        # No running event loop yet. Public route registration will call this
        # again once the media server owns a live loop.
        return


__all__ = [
    "MovieNightTerminationResult",
    "cleanup_inactive_movie_night_rooms",
    "ensure_movie_night_cleanup_task",
    "terminate_movie_night_room",
]
