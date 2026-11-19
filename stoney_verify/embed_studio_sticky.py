"""Sticky posts for Post Builder (Embed Studio).

A tracked Post Builder post can be made sticky. While it is sticky, new
human messages in its channel re-post it at the bottom: the old copy is
deleted, a fresh copy with the post's current look is sent, and the tracked
record follows the new message. Editing the post edits the current copy in
place, so the next move carries the edit along.

One sticky per channel, shared with Community Tools Sticky Messages: if a
channel already has an enabled Community Tools sticky, or another Post
Builder post is already sticky there, making a post sticky is refused in
plain words. The two systems never move posts in the same channel.

State lives on the Studio published record itself (a small ``sticky``
dict), saved through the existing library save path — no new tables.
Removing sticky (or the tracked post being deleted) stops the movement;
the last copy is left in place.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import discord

from .community_tools_service import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MESSAGE_THRESHOLD,
    CommunityStorageUnavailable,
    StickyConfig,
    get_sticky,
)
from .community_tools_runtime import should_refresh_sticky
from .embed_studio_core import StudioValidationError, render_embeds, validate_project
from .startup_guards.discord_api_safety import fetch_message_with_api_safety

_RUNTIME_ATTR = "_dank_studio_sticky_runtime"
_MENTIONS = discord.AllowedMentions.none()
STARTUP_RECONCILE_CONCURRENCY = 10

STICKY_INTERVAL_SECONDS = int(DEFAULT_INTERVAL_SECONDS)
STICKY_MESSAGE_THRESHOLD = int(DEFAULT_MESSAGE_THRESHOLD)


def _log(message: str) -> None:
    try:
        print(f"embed_studio_sticky: {message}", flush=True)
    except Exception:
        pass


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def sticky_state(item: Any) -> dict[str, Any]:
    """Normalize the sticky dict stored on a published record."""
    raw = item.get("sticky") if isinstance(item, dict) else None
    if not isinstance(raw, dict):
        return {"enabled": False, "last_sent_at": None}
    return {
        "enabled": bool(raw.get("enabled")),
        "last_sent_at": _parse_ts(raw.get("last_sent_at")),
    }


def post_is_sticky(item: Any) -> bool:
    return bool(sticky_state(item)["enabled"])


def channel_sticky_key(
    published: Any, channel_id: int, *, exclude_key: str = "",
) -> Optional[str]:
    """Return the post_key of another sticky post in this channel, if any."""
    if not isinstance(published, dict):
        return None
    for key, item in published.items():
        if key == exclude_key or not isinstance(item, dict):
            continue
        try:
            item_channel = int(item.get("channel_id") or 0)
        except (TypeError, ValueError):
            continue
        if item_channel == int(channel_id) and post_is_sticky(item):
            return str(key)
    return None


def _throttle_config(entry: "StudioStickyEntry") -> StickyConfig:
    return StickyConfig(
        guild_id=int(entry.guild_id),
        channel_id=int(entry.channel_id),
        enabled=True,
        interval_seconds=STICKY_INTERVAL_SECONDS,
        message_threshold=STICKY_MESSAGE_THRESHOLD,
        last_sent_at=entry.last_sent_at,
    )


@dataclass
class StudioStickyEntry:
    guild_id: int
    channel_id: int
    post_key: str
    last_sent_at: Optional[datetime] = None


async def _load_library(guild_id: int) -> tuple[Any, dict[str, Any]]:
    from .embed_studio_ui import _load_library as load_library

    return await load_library(guild_id)


async def _save_library(guild_id: int, source: Any, lib: dict[str, Any]) -> bool:
    from .embed_studio_ui import _save_library as save_library

    return await save_library(guild_id, source, lib)


async def set_post_sticky(
    guild_id: int,
    post_key: str,
    *,
    enabled: bool,
    client: Any = None,
) -> tuple[bool, str]:
    """Turn sticky on or off for one tracked post. Returns (ok, plain words).

    Refusals change nothing: a channel can hold only one sticky, and a post
    with Live Text updates cannot move (Live Text edits one fixed message).
    """
    try:
        source, lib = await _load_library(int(guild_id))
    except Exception:
        return False, "Post Builder storage is unavailable right now. Nothing was changed."
    item = lib.get("published", {}).get(post_key)
    if not isinstance(item, dict):
        return False, "That tracked post no longer exists. Reopen Edit Posted and try again."
    try:
        channel_id = int(item.get("channel_id") or 0)
    except (TypeError, ValueError):
        channel_id = 0
    if channel_id <= 0:
        return False, "That tracked post has no channel saved. Nothing was changed."

    runtime = studio_sticky_runtime(client) if client is not None else None

    if not enabled:
        if not post_is_sticky(item):
            return True, "Sticky is already off for this post."
        item["sticky"] = {"enabled": False, "last_sent_at": None}
        try:
            saved = await _save_library(int(guild_id), source, lib)
        except Exception:
            return False, "Sticky could not be turned off because storage failed. Nothing was changed."
        if not saved:
            return False, "Another change saved first, so sticky was not turned off. Reopen and try again."
        if runtime is not None:
            runtime.remove_sticky(channel_id)
        return True, (
            "📌 Sticky off. The post stays where it is now — it just will not "
            "keep moving back to the bottom anymore."
        )

    if post_is_sticky(item):
        return True, "This post is already sticky in its channel."
    if str(item.get("live_nonce") or ""):
        return False, (
            "This post has Live Text updates on, and Live Text works by editing "
            "one fixed message in place. A sticky post has to move, so turn "
            "Live Text off for this post first (Edit Posted → Live Text Updates)."
        )
    other_key = channel_sticky_key(lib.get("published", {}), channel_id, exclude_key=post_key)
    if other_key is not None:
        other = lib["published"].get(other_key) or {}
        return False, (
            f"Another Post Builder post (message {other.get('message_id')}) is "
            f"already sticky in <#{channel_id}>. A channel can only have one "
            "sticky. Turn sticky off for that post first, then make this one sticky."
        )
    try:
        community = await get_sticky(channel_id)
    except CommunityStorageUnavailable:
        return False, "Sticky settings could not be checked right now. Nothing was changed."
    if community is not None and community.enabled:
        return False, (
            f"<#{channel_id}> already has a Sticky Message turned on in "
            "Community Tools. A channel can only have one sticky. Turn that "
            "one off first (Community Tools → Sticky Messages), then make "
            "this post sticky."
        )

    now = datetime.now(timezone.utc)
    item["sticky"] = {"enabled": True, "last_sent_at": _format_ts(now)}
    try:
        saved = await _save_library(int(guild_id), source, lib)
    except Exception:
        return False, "Sticky could not be turned on because storage failed. Nothing was changed."
    if not saved:
        return False, "Another change saved first, so sticky was not turned on. Reopen and try again."
    if runtime is not None:
        runtime.set_sticky(StudioStickyEntry(
            guild_id=int(guild_id), channel_id=channel_id,
            post_key=str(post_key), last_sent_at=now,
        ))
    return True, (
        "📌 Sticky on. When people chat in that channel, this post will be "
        "re-posted at the bottom so it does not get buried. It will not move "
        "more than the normal sticky pace, and bot messages never trigger it."
    )


def _message_view(item: dict[str, Any], project: dict[str, Any]) -> Optional[discord.ui.View]:
    """Rebuild the post's buttons exactly as publishing built them."""
    from .embed_studio_core import link_view

    if isinstance(item.get("poll"), dict):
        from .embed_studio_polls import poll_view, validate_poll

        try:
            return poll_view(validate_poll(item["poll"]), str(item.get("poll_token") or ""))
        except Exception:
            return None
    if project.get("role_actions"):
        from .embed_studio_roles import member_role_view

        return member_role_view(project, str(item.get("role_token") or ""))
    if project.get("form"):
        from .embed_studio_forms import StudioFormError, form_view

        try:
            return form_view(project, str(item.get("form_token") or ""))
        except StudioFormError:
            return link_view(project)
    return link_view(project)


class StudioStickyRuntime:
    """Moves sticky Post Builder posts to the bottom of their channel.

    Mirrors the Community Tools sticky engine: an in-memory channel index
    (zero database reads on the message hot path), the same throttle
    decision (``should_refresh_sticky``), per-channel locks, send the new
    copy first, and roll the new copy back if the tracking save fails.
    """

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._index: dict[int, StudioStickyEntry] = {}
        self._counts: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._pending: set[int] = set()
        self._ready_lock = asyncio.Lock()

    def set_sticky(self, entry: StudioStickyEntry) -> None:
        self._index[int(entry.channel_id)] = entry
        self._counts[int(entry.channel_id)] = 0

    def remove_sticky(self, channel_id: int) -> None:
        channel_key = int(channel_id)
        self._index.pop(channel_key, None)
        self._counts.pop(channel_key, None)
        self._pending.discard(channel_key)

    def entry_for(self, channel_id: int) -> Optional[StudioStickyEntry]:
        return self._index.get(int(channel_id))

    def entry_for_channel_post(self, post_key: str) -> Optional[StudioStickyEntry]:
        return self._index_by_key(str(post_key))

    async def on_message(self, message: discord.Message) -> None:
        guild = getattr(message, "guild", None)
        if guild is None:
            return
        author = getattr(message, "author", None)
        if bool(getattr(author, "bot", False)):
            return
        if getattr(message, "webhook_id", None):
            return
        channel_id = int(getattr(message.channel, "id", 0) or 0)
        if channel_id <= 0:
            return
        entry = self._index.get(channel_id)
        if entry is None:
            return
        if int(entry.guild_id) != int(getattr(guild, "id", 0) or 0):
            return
        # The tracked message itself is bot-authored, so it can never reach
        # here; a human reply is the only trigger, exactly like Sticky
        # Messages. Counts and the minimum interval keep movement calm.
        count = int(self._counts.get(channel_id, 0)) + 1
        self._counts[channel_id] = count
        if not should_refresh_sticky(_throttle_config(entry), message_count=count):
            return
        self._counts[channel_id] = 0
        if channel_id in self._pending:
            return
        self._pending.add(channel_id)
        asyncio.create_task(
            self._refresh_from_activity(message.channel, entry),
            name=f"dank-studio-sticky-refresh-{channel_id}",
        )

    async def _refresh_from_activity(self, channel: Any, entry: StudioStickyEntry) -> None:
        channel_id = int(getattr(channel, "id", entry.channel_id) or entry.channel_id)
        try:
            await self.refresh_channel(channel, entry=entry, force=True)
        finally:
            self._pending.discard(channel_id)

    async def _clear_sticky_record(
        self, guild_id: int, post_key: str, *, expected_message_id: int = 0,
    ) -> None:
        """Best-effort: mark a tracked post not sticky and drop the index entry."""
        try:
            source, lib = await _load_library(int(guild_id))
            item = lib.get("published", {}).get(post_key)
            if isinstance(item, dict) and post_is_sticky(item):
                if expected_message_id and int(item.get("message_id") or 0) != int(expected_message_id):
                    return
                item["sticky"] = {"enabled": False, "last_sent_at": None}
                await _save_library(int(guild_id), source, lib)
        except Exception:
            pass
        entry = self._index_by_key(post_key)
        if entry is not None:
            self.remove_sticky(entry.channel_id)

    def _index_by_key(self, post_key: str) -> Optional[StudioStickyEntry]:
        for entry in self._index.values():
            if entry.post_key == post_key:
                return entry
        return None

    async def refresh_channel(
        self, channel: Any, *, entry: StudioStickyEntry, force: bool = False,
    ) -> Optional[discord.Message]:
        if not isinstance(channel, discord.TextChannel):
            return None
        channel_id = int(channel.id)
        if int(entry.channel_id) != channel_id:
            return None
        lock = self._locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            try:
                _source, lib = await _load_library(int(entry.guild_id))
            except Exception:
                return None
            item = lib.get("published", {}).get(entry.post_key)
            if not isinstance(item, dict) or not post_is_sticky(item):
                self.remove_sticky(channel_id)
                return None
            if int(item.get("channel_id") or 0) != channel_id:
                self.remove_sticky(channel_id)
                return None
            if str(item.get("live_nonce") or ""):
                # Live Text owns one fixed message; never move it.
                return None
            if not force and not should_refresh_sticky(
                _throttle_config(entry),
                message_count=max(1, self._counts.get(channel_id, 0)),
            ):
                return None
            try:
                community = await get_sticky(channel_id)
            except CommunityStorageUnavailable:
                return None
            if community is not None and community.enabled:
                # A Community Tools sticky owns this channel now; never fight.
                _log(f"channel={channel_id} has a Community Tools sticky; studio sticky stands down")
                return None

            old_message_id = int(item.get("message_id") or 0)
            if old_message_id <= 0:
                await self._clear_sticky_record(int(entry.guild_id), entry.post_key)
                return None
            try:
                message = await fetch_message_with_api_safety(
                    channel, old_message_id, label="studio sticky refresh")
            except discord.NotFound:
                # The post was deleted. Like the publish recovery, a deleted
                # post stays deleted: stop being sticky instead of posting
                # the announcement again unasked.
                await self._clear_sticky_record(
                    int(entry.guild_id), entry.post_key,
                    expected_message_id=old_message_id)
                return None
            except (discord.Forbidden, discord.HTTPException):
                return None
            bot_user = getattr(self.bot, "user", None)
            if bot_user is None or int(getattr(message.author, "id", 0) or 0) != int(bot_user.id):
                await self._clear_sticky_record(
                    int(entry.guild_id), entry.post_key,
                    expected_message_id=old_message_id)
                return None

            from .embed_studio_ui import _from_message

            if isinstance(item.get("poll"), dict):
                # Polls render from the tracked record, never from the
                # message: the record holds the votes, and a sticky move
                # must carry the exact same vote state to the new copy.
                from .embed_studio_polls import poll_embed, poll_view, validate_poll

                try:
                    poll = validate_poll(item["poll"])
                except Exception:
                    return None
                send_content = None
                send_embeds = [poll_embed(poll)]
                send_view = poll_view(poll, str(item.get("poll_token") or ""))
            else:
                try:
                    project = _from_message(message)
                    project["role_mode"] = str(item.get("role_mode") or "buttons")
                    project["role_actions"] = deepcopy(item.get("role_actions") or [])
                    project["form"] = deepcopy(item.get("form"))
                    project = validate_project(project)
                    send_content = project["content"] or None
                    send_embeds = render_embeds(project)
                    send_view = _message_view(item, project)
                except (StudioValidationError, ValueError, TypeError):
                    return None

            try:
                new_message = await channel.send(
                    content=send_content,
                    embeds=send_embeds,
                    view=send_view,
                    allowed_mentions=_MENTIONS,
                )
            except (discord.Forbidden, discord.HTTPException):
                return None

            now = datetime.now(timezone.utc)
            new_key = f"{channel_id}:{int(new_message.id)}"
            try:
                source, fresh = await _load_library(int(entry.guild_id))
                current = fresh.get("published", {}).get(entry.post_key)
                if (
                    not isinstance(current, dict)
                    or not post_is_sticky(current)
                    or int(current.get("message_id") or 0) != old_message_id
                ):
                    await self._delete_message_object(new_message)
                    return None
                record = dict(current)
                record["message_id"] = str(int(new_message.id))
                record["sticky"] = {"enabled": True, "last_sent_at": _format_ts(now)}
                fresh["published"].pop(entry.post_key, None)
                fresh["published"][new_key] = record
                saved = await _save_library(int(entry.guild_id), source, fresh)
            except Exception:
                saved = False
            if not saved:
                # Tracking is authoritative: without it the new copy would be
                # a duplicate nobody can edit, so roll it back.
                await self._delete_message_object(new_message)
                return None

            entry.post_key = new_key
            entry.last_sent_at = now
            self._index[channel_id] = entry
            self._counts[channel_id] = 0
            if int(new_message.id) != old_message_id:
                await self._delete_previous(channel, old_message_id)
            return new_message

    async def _delete_message_object(self, message: Any) -> bool:
        try:
            await message.delete()
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
        except Exception:
            return False

    async def _delete_previous(self, channel: discord.TextChannel, message_id: int) -> bool:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False
        return await self._delete_message_object(message)

    async def on_ready(self) -> None:
        async with self._ready_lock:
            guilds = list(getattr(self.bot, "guilds", None) or [])
            jobs = [self._index_guild(guild) for guild in guilds]
            semaphore = asyncio.Semaphore(STARTUP_RECONCILE_CONCURRENCY)

            async def run(job: Any) -> None:
                async with semaphore:
                    try:
                        await job
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        _log(f"startup index item failed: {type(exc).__name__}: {exc}")

            await asyncio.gather(*(run(job) for job in jobs))

    async def _index_guild(self, guild: Any) -> None:
        guild_id = int(getattr(guild, "id", 0) or 0)
        if guild_id <= 0:
            return
        try:
            _source, lib = await _load_library(guild_id)
        except Exception:
            return
        for key, item in list(lib.get("published", {}).items()):
            if not isinstance(item, dict) or not post_is_sticky(item):
                continue
            try:
                channel_id = int(item.get("channel_id") or 0)
                message_id = int(item.get("message_id") or 0)
            except (TypeError, ValueError):
                continue
            if channel_id <= 0 or message_id <= 0:
                continue
            state = sticky_state(item)
            self.set_sticky(StudioStickyEntry(
                guild_id=guild_id, channel_id=channel_id, post_key=str(key),
                last_sent_at=state["last_sent_at"],
            ))
            channel = guild.get_channel(channel_id) if hasattr(guild, "get_channel") else None
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                await fetch_message_with_api_safety(
                    channel, message_id, label="studio sticky startup", recovery=True)
            except discord.NotFound:
                await self._clear_sticky_record(
                    guild_id, str(key), expected_message_id=message_id)
            except (discord.Forbidden, discord.HTTPException):
                continue


def ensure_studio_sticky_runtime(bot: Any) -> StudioStickyRuntime:
    existing = getattr(bot, _RUNTIME_ATTR, None)
    if isinstance(existing, StudioStickyRuntime):
        return existing
    runtime = StudioStickyRuntime(bot)
    setattr(bot, _RUNTIME_ATTR, runtime)
    bot.add_listener(runtime.on_message, "on_message")
    bot.add_listener(runtime.on_ready, "on_ready")
    return runtime


def studio_sticky_runtime(bot: Any) -> Optional[StudioStickyRuntime]:
    runtime = getattr(bot, _RUNTIME_ATTR, None)
    return runtime if isinstance(runtime, StudioStickyRuntime) else None


# ---------------------------------------------------------------------------
# Post Builder UI: Edit Posted → Sticky Posts, plus the publish prompt.
# Every new control uses the house picker framework or a stable custom_id,
# per Felix's standing picker rule.
# ---------------------------------------------------------------------------

from .embed_studio_ui import (  # noqa: E402
    StudioSession,
    _StudioPickerOpenButton,
    _StudioPickerView,
    _StudioView,
    _menu,
    _published,
    _say,
)
from .ui.picker import make_choice  # noqa: E402


def _sticky_label(item: dict[str, Any]) -> str:
    state = "📌 Sticky on" if post_is_sticky(item) else "Not sticky"
    return f"{state} • #{item.get('channel_id')} • message {item.get('message_id')}"


async def _show_sticky_choice(
    interaction: discord.Interaction, session: StudioSession, key: str,
    *, notice: str = "",
) -> None:
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        from .embed_studio_ui import _error

        return await _say(interaction, _error(exc))
    item = lib.get("published", {}).get(key)
    if not isinstance(item, dict):
        return await _say(interaction, "That tracked post no longer exists.")
    sticky = post_is_sticky(item)
    channel_id = item.get("channel_id")
    if sticky:
        status = (
            "This post is **sticky**. When people chat in its channel, it is "
            "re-posted at the bottom so it does not get buried. Bot messages "
            "never trigger a move, and it will not move faster than the "
            "normal sticky pace."
        )
    else:
        status = (
            "This post is **not sticky**. Make it sticky and it will be "
            "re-posted at the bottom of its channel as people chat, so it "
            "does not get buried. A channel can only have one sticky."
        )
    description = (
        f"Channel: <#{channel_id}>\nMessage: {item.get('message_id')}\n\n{status}"
    )
    if notice:
        description = notice + "\n\n" + description
    await _menu(
        interaction,
        heading="📌 Sticky Post",
        description=description,
        view=StudioStickyChoiceView(session, key, sticky=sticky),
    )


class StudioStickyChoiceView(_StudioView):
    def __init__(self, session: StudioSession, key: str, *, sticky: bool) -> None:
        super().__init__(session)
        self.key = key
        self.sticky = bool(sticky)
        self.toggle.label = "Remove Sticky" if self.sticky else "Make Sticky"
        self.toggle.style = (
            discord.ButtonStyle.danger if self.sticky else discord.ButtonStyle.success
        )

    @discord.ui.button(
        label="Make Sticky", emoji="📌", style=discord.ButtonStyle.success,
        custom_id="dank:embed_studio:sticky_toggle:v1", row=0,
    )
    async def toggle(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await set_post_sticky(
            self.session.guild_id, self.key,
            enabled=not self.sticky, client=interaction.client,
        )
        if ok and not self.sticky:
            # Making a buried post sticky should put it at the bottom now,
            # not only after the next burst of chat.
            runtime = studio_sticky_runtime(interaction.client)
            entry = runtime.entry_for_channel_post(self.key) if runtime is not None else None
            channel = None
            if entry is not None and interaction.guild is not None:
                channel = interaction.guild.get_channel(int(entry.channel_id))
            if runtime is not None and entry is not None and isinstance(channel, discord.TextChannel):
                try:
                    await runtime.refresh_channel(channel, entry=entry, force=True)
                except Exception:
                    pass
        await _show_sticky_choice(
            interaction, self.session, self.key, notice=("✅ " if ok else "❌ ") + message,
        )

    @discord.ui.button(
        label="Back to Sticky Posts", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:sticky_choice_back:v1", row=0,
    )
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await open_sticky_menu(interaction, self.session)


class StudioStickyMenuView(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(_StudioPickerOpenButton(
            session, label="Choose a Published Post", emoji="📌",
            custom_id="dank:embed_studio:sticky_post:open:v1", row=0,
            heading="📌 Sticky Posts",
            description="Choose one of the tracked Dank Shield posts to make it sticky or remove sticky.",
            build=lambda interaction: _sticky_post_picker_view(session, lib),
        ))

    @discord.ui.button(
        label="Back", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:sticky_menu_back:v1", row=1,
    )
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)


def _sticky_post_picker_view(
    session: StudioSession, lib: dict[str, Any],
) -> _StudioPickerView:
    choices = [
        make_choice(_sticky_label(item), key)
        for key, item in list(lib.get("published", {}).items())[-20:]
        if isinstance(item, dict)
    ] or [make_choice("No tracked messages yet", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "Publish a message first; it will appear here.")
        await _show_sticky_choice(interaction, session, key)

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:sticky_post:v1",
        placeholder="Choose a previously published post",
        title="Sticky Posts",
        on_home=lambda interaction: open_sticky_menu(interaction, session),
        home_label="Back",
    )


async def open_sticky_menu(interaction: discord.Interaction, session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        from .embed_studio_ui import _error

        return await _say(interaction, _error(exc))
    await _menu(
        interaction, heading="📌 Sticky Posts",
        description=(
            "Make one tracked post per channel sticky. A sticky post is "
            "re-posted at the bottom of its channel as people chat, so it "
            "does not get buried. If a channel already has a Sticky Message "
            "from Community Tools, that one wins — a channel can only have "
            "one sticky."
        ),
        view=StudioStickyMenuView(session, lib),
    )


class StudioStickyPromptView(_StudioView):
    """One-tap sticky toggle attached to the publish confirmation."""

    def __init__(self, session: StudioSession, post_key: str, *, sticky: bool) -> None:
        super().__init__(session, timeout=300)
        self.post_key = str(post_key)
        self.sticky = bool(sticky)
        self.toggle.label = "Remove Sticky" if self.sticky else "Make Sticky"

    @discord.ui.button(
        label="Make Sticky", emoji="📌", style=discord.ButtonStyle.primary,
        custom_id="dank:embed_studio:sticky_prompt:v1", row=0,
    )
    async def toggle(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        ok, message = await set_post_sticky(
            self.session.guild_id, self.post_key,
            enabled=not self.sticky, client=interaction.client,
        )
        await _say(interaction, ("✅ " if ok else "❌ ") + message)


__all__ = [
    "STICKY_INTERVAL_SECONDS",
    "STICKY_MESSAGE_THRESHOLD",
    "StudioStickyChoiceView",
    "StudioStickyEntry",
    "StudioStickyMenuView",
    "StudioStickyPromptView",
    "StudioStickyRuntime",
    "channel_sticky_key",
    "ensure_studio_sticky_runtime",
    "open_sticky_menu",
    "post_is_sticky",
    "set_post_sticky",
    "sticky_state",
    "studio_sticky_runtime",
]
