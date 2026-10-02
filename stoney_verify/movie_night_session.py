from __future__ import annotations

"""Canonical external cleanup for a Movie Night room termination."""

from dataclasses import dataclass

from stoney_verify.movie_night import MovieNightRoom, get_movie_night_manager, movie_room_lease_key
from stoney_verify.torrent_streaming import get_torrent_manager


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

    return MovieNightTerminationResult(
        had_stream=had_stream,
        lease_released=lease_released,
        cleanup_error=cleanup_error,
    )


__all__ = ["MovieNightTerminationResult", "terminate_movie_night_room"]
