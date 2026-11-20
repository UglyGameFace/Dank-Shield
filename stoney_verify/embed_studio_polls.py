"""Polls for Post Builder (Embed Studio).

A poll is a first-class Post Builder post kind. Creating one asks for a
question and 2–10 choices, publishes it straight to a chosen channel, and
tracks it as a Studio published record. The poll itself — question,
choices, every member's vote, and its state — lives on that record
(``poll`` dict plus a ``poll_token``), saved through the existing library
CAS path. No new tables.

Vote buttons are persistent: one semantic ``on_interaction`` listener
routes every guild's poll buttons by token, exactly like Studio role
buttons. One vote per member; pressing another choice moves the vote.
Managers pause, resume, or end a poll from Post Builder's Published →
Polls screen. A poll can also be made sticky — the sticky runtime moves
the tracked record, so the same votes ride along and never reset.
"""
from __future__ import annotations

import asyncio
import re
import secrets
from copy import deepcopy
from time import monotonic
from typing import Any, Mapping, Optional

import discord

from .embed_studio_core import StudioValidationError

POLL_PREFIX = "dank:es:poll:v1:"
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
MIN_POLL_OPTIONS = 2
MAX_POLL_OPTIONS = 10
MAX_POLL_QUESTION = 300
MAX_POLL_OPTION = 80
POLL_STATES = frozenset({"active", "paused", "ended"})
_MENTIONS = discord.AllowedMentions.none()
_ATTACHED_BOT_IDS: set[int] = set()
_POLL_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_VOTE_LAST_CLICK: dict[tuple[int, int], float] = {}
_VOTE_COOLDOWN_SECONDS = 1.0
_VOTE_COOLDOWN_MAX_KEYS = 4096


def _log(message: str) -> None:
    try:
        print(f"embed_studio_polls: {message}", flush=True)
    except Exception:
        pass


def _text(value: Any, limit: int, label: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > limit:
        raise StudioValidationError(f"{label} must be {limit} characters or fewer.")
    return text


def validate_poll(raw: Any) -> dict[str, Any]:
    """Normalize one poll dict stored on a published record."""
    if not isinstance(raw, Mapping):
        raise StudioValidationError("Poll settings are missing or invalid.")
    question = _text(raw.get("question"), MAX_POLL_QUESTION, "Poll question")
    if not question:
        raise StudioValidationError("A poll needs a question.")
    options: list[str] = []
    for value in raw.get("options") or []:
        option = _text(value, MAX_POLL_OPTION, "Poll choice")
        if option and option.casefold() not in {item.casefold() for item in options}:
            options.append(option)
    if not MIN_POLL_OPTIONS <= len(options) <= MAX_POLL_OPTIONS:
        raise StudioValidationError(
            f"Polls need {MIN_POLL_OPTIONS} to {MAX_POLL_OPTIONS} different choices."
        )
    state = str(raw.get("state") or "active").strip().lower()
    if state not in POLL_STATES:
        raise StudioValidationError("Poll state must be active, paused, or ended.")
    votes: dict[str, int] = {}
    raw_votes = raw.get("votes") or {}
    if isinstance(raw_votes, Mapping):
        for user_id, raw_index in raw_votes.items():
            key = str(user_id).strip()
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if key and key.isascii() and key.isdecimal() and 0 <= index < len(options):
                votes[key] = index
    return {"question": question, "options": options, "votes": votes, "state": state}


def poll_counts(poll: Mapping[str, Any]) -> tuple[int, ...]:
    clean = validate_poll(poll)
    counts = [0 for _ in clean["options"]]
    for index in clean["votes"].values():
        counts[int(index)] += 1
    return tuple(counts)


def poll_total(poll: Mapping[str, Any]) -> int:
    return len(validate_poll(poll)["votes"])


def poll_embed(poll: Mapping[str, Any]) -> discord.Embed:
    clean = validate_poll(poll)
    counts = poll_counts(clean)
    total = len(clean["votes"])
    lines: list[str] = []
    for index, option in enumerate(clean["options"]):
        count = counts[index]
        percent = (count / total * 100.0) if total else 0.0
        lines.append(
            f"**{index + 1}. {option}** — {count} vote{'s' if count != 1 else ''} ({percent:.0f}%)"
        )
    state_label = {
        "active": "Voting open",
        "paused": "Voting paused",
        "ended": "Poll ended — final results",
    }.get(clean["state"], clean["state"])
    embed = discord.Embed(
        title="📊 Poll",
        description=clean["question"],
        color=discord.Color.blurple(),
    )
    embed.add_field(name=state_label, value="\n".join(lines) or "No choices.", inline=False)
    embed.set_footer(text=f"{total} total vote{'s' if total != 1 else ''} • one choice per member")
    return embed


def poll_custom_id(token: str, index: int) -> str:
    token = str(token or "")
    if not TOKEN_RE.fullmatch(token):
        raise ValueError("Invalid poll token")
    if not 0 <= int(index) < MAX_POLL_OPTIONS:
        raise ValueError("Invalid poll option index")
    return f"{POLL_PREFIX}{token}:{int(index)}"


def parse_poll_custom_id(value: str) -> tuple[str, int] | None:
    raw = str(value or "")
    if not raw.startswith(POLL_PREFIX):
        return None
    parts = raw[len(POLL_PREFIX):].split(":")
    if len(parts) != 2 or not TOKEN_RE.fullmatch(parts[0]):
        return None
    if not parts[1].isascii() or not parts[1].isdecimal():
        return None
    index = int(parts[1])
    if not 0 <= index < MAX_POLL_OPTIONS:
        return None
    return parts[0], index


class StudioPollVoteButton(discord.ui.Button):
    def __init__(self, token: str, index: int, label: str, *, disabled: bool = False) -> None:
        super().__init__(
            label=f"{index + 1}. {label}"[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=poll_custom_id(token, index),
            row=min(int(index) // 5, 4),
            disabled=bool(disabled),
        )
        self.token = str(token)
        self.option_index = int(index)


def poll_view(poll: Mapping[str, Any], token: str) -> Optional[discord.ui.View]:
    """Buttons for a poll message; ended polls carry no buttons at all."""
    clean = validate_poll(poll)
    if clean["state"] == "ended":
        return None
    if not TOKEN_RE.fullmatch(str(token or "")):
        return None
    view = discord.ui.View(timeout=None)
    for index, option in enumerate(clean["options"]):
        view.add_item(StudioPollVoteButton(
            str(token), index, option, disabled=clean["state"] != "active"))
    return view


def record_is_poll(item: Any) -> bool:
    return isinstance(item, dict) and isinstance(item.get("poll"), dict)


async def _load_library(guild_id: int) -> tuple[Any, dict[str, Any]]:
    from .embed_studio_ui import _load_library as load_library

    return await load_library(guild_id)


async def _save_library(guild_id: int, source: Any, lib: dict[str, Any]) -> bool:
    from .embed_studio_ui import _save_library as save_library

    return await save_library(guild_id, source, lib)


def _find_poll_record(
    lib: Mapping[str, Any], *, channel_id: int, message_id: int, token: str,
) -> tuple[str, dict[str, Any]] | None:
    key = f"{int(channel_id)}:{int(message_id)}"
    item = lib.get("published", {}).get(key)
    if not isinstance(item, dict):
        return None
    if str(item.get("poll_token") or "") != str(token):
        return None
    if not record_is_poll(item):
        return None
    return key, item


def _rate_limited(guild_id: int, member_id: int, *, now: float | None = None) -> bool:
    stamp = monotonic() if now is None else now
    key = (int(guild_id), int(member_id))
    previous = _VOTE_LAST_CLICK.get(key)
    if previous is not None and stamp - previous < _VOTE_COOLDOWN_SECONDS:
        return True
    if len(_VOTE_LAST_CLICK) >= _VOTE_COOLDOWN_MAX_KEYS:
        stale = [k for k, v in _VOTE_LAST_CLICK.items() if stamp - v >= _VOTE_COOLDOWN_SECONDS]
        for item_key in stale:
            _VOTE_LAST_CLICK.pop(item_key, None)
        if len(_VOTE_LAST_CLICK) >= _VOTE_COOLDOWN_MAX_KEYS:
            _VOTE_LAST_CLICK.pop(next(iter(_VOTE_LAST_CLICK)), None)
    _VOTE_LAST_CLICK[key] = stamp
    return False


async def cast_poll_vote(
    guild_id: int, post_key: str, user_id: int, option_index: int,
) -> dict[str, Any]:
    """Move one member's vote on a tracked poll. Returns the saved poll.

    The whole vote rides through the library CAS: on a lost race the
    library is re-read and the same vote re-applied, so two members
    voting at once can never erase each other.
    """
    lock = _POLL_LOCKS.setdefault((int(guild_id), str(post_key)), asyncio.Lock())
    async with lock:
        last_error: Optional[Exception] = None
        for _attempt in range(3):
            source, lib = await _load_library(int(guild_id))
            item = lib.get("published", {}).get(str(post_key))
            if not record_is_poll(item):
                raise StudioValidationError("This poll is no longer tracked. Ask staff to republish it.")
            poll = validate_poll(item["poll"])
            if poll["state"] != "active":
                raise StudioValidationError(
                    "Voting is paused on this poll." if poll["state"] == "paused"
                    else "This poll has ended.")
            if not 0 <= int(option_index) < len(poll["options"]):
                raise StudioValidationError("That poll choice was removed.")
            poll["votes"][str(int(user_id))] = int(option_index)
            item["poll"] = poll
            try:
                if await _save_library(int(guild_id), source, lib):
                    return poll
            except Exception as exc:  # storage backend failure: fail closed
                last_error = exc
        if last_error is not None:
            raise StudioValidationError("Poll storage is unavailable. Your vote was not saved.")
        raise StudioValidationError("The poll changed while you voted. Press the choice again.")


async def set_poll_state(
    guild_id: int, post_key: str, state: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Set a poll's state on its record. Returns (poll, record)."""
    wanted = str(state or "").strip().lower()
    if wanted not in POLL_STATES:
        raise StudioValidationError("Poll state must be active, paused, or ended.")
    lock = _POLL_LOCKS.setdefault((int(guild_id), str(post_key)), asyncio.Lock())
    async with lock:
        for _attempt in range(3):
            source, lib = await _load_library(int(guild_id))
            item = lib.get("published", {}).get(str(post_key))
            if not record_is_poll(item):
                raise StudioValidationError("That tracked poll no longer exists. Reopen Polls and try again.")
            poll = validate_poll(item["poll"])
            poll["state"] = wanted
            item["poll"] = poll
            if await _save_library(int(guild_id), source, lib):
                return poll, item
        raise StudioValidationError("Another change saved first. Reopen Polls and try again.")


async def _answer(interaction: discord.Interaction, text: str) -> None:
    opts = {"ephemeral": True, "allowed_mentions": _MENTIONS}
    if interaction.response.is_done():
        await interaction.followup.send(text, **opts)
    else:
        await interaction.response.send_message(text, **opts)


async def _vote_click(interaction: discord.Interaction, token: str, index: int) -> None:
    guild = interaction.guild
    user = interaction.user
    channel = interaction.channel
    message = interaction.message
    if guild is None or channel is None or message is None or user is None:
        return await _answer(interaction, "This poll only works in its server.")
    if not interaction.client.user or int(getattr(message.author, "id", 0) or 0) != int(interaction.client.user.id):
        return await _answer(interaction, "Only Dank Shield poll posts can take votes.")
    if _rate_limited(int(guild.id), int(user.id)):
        return await _answer(interaction, "You are voting a little fast — give it a second and press again.")
    try:
        source, lib = await _load_library(int(guild.id))
    except Exception:
        return await _answer(interaction, "Poll storage is unavailable. Your vote was not saved.")
    found = _find_poll_record(
        lib, channel_id=int(channel.id), message_id=int(message.id), token=token)
    if found is None:
        return await _answer(
            interaction,
            "This is an older poll card. Use the newest copy of the poll so your vote cannot land on a stale message.",
        )
    post_key, item = found
    poll = validate_poll(item["poll"])
    if poll["state"] != "active":
        return await _answer(
            interaction,
            "Voting is paused on this poll." if poll["state"] == "paused" else "This poll has ended.")
    try:
        saved = await cast_poll_vote(int(guild.id), post_key, int(user.id), int(index))
    except StudioValidationError as exc:
        return await _answer(interaction, str(exc))
    except Exception:
        return await _answer(interaction, "The vote could not be saved.")
    try:
        await interaction.response.edit_message(
            embed=poll_embed(saved), view=poll_view(saved, token),
            allowed_mentions=_MENTIONS,
        )
    except discord.HTTPException:
        await _answer(interaction, "✅ Vote saved. I could not refresh the public poll card yet.")


async def _listener(interaction: discord.Interaction) -> None:
    if interaction.type is not discord.InteractionType.component:
        return
    data = interaction.data if isinstance(interaction.data, dict) else {}
    candidate = str(data.get("custom_id") or "")
    if not candidate.startswith(POLL_PREFIX):
        return
    parsed = parse_poll_custom_id(candidate)
    if parsed is None:
        await _answer(interaction, "This poll button is invalid.")
        return
    await _vote_click(interaction, parsed[0], parsed[1])


def install_embed_studio_poll_runtime(bot: Any, *, strict: bool = False) -> bool:
    """Exactly one listener per client; no guild-wide scan on startup."""
    key = id(bot)
    if key in _ATTACHED_BOT_IDS:
        return True
    try:
        bot.add_listener(_listener, "on_interaction")
        _ATTACHED_BOT_IDS.add(key)
        return True
    except Exception as exc:
        if strict:
            raise RuntimeError("Embed Studio poll runtime is missing") from exc
        return False


# ---------------------------------------------------------------------------
# Migration converters: legacy Community Tools sticky data -> Studio records.
# Pure functions — the tools/migrate script and the tests both use them.
# ---------------------------------------------------------------------------

def studio_poll_from_legacy(poll: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one legacy poll-shaped dict to a Studio poll dict."""
    return validate_poll({
        "question": poll.get("question"),
        "options": list(poll.get("options") or []),
        "votes": dict(poll.get("votes") or {}),
        "state": poll.get("state") or "active",
    })


def studio_record_from_legacy_sticky(
    config: Mapping[str, Any], poll: Optional[Mapping[str, Any]] = None,
) -> tuple[str, dict[str, Any]]:
    """Build the Studio published record for one legacy sticky.

    The record points at the legacy sticky's own live message: that
    message is bot-authored, so the Studio sticky runtime can adopt it
    in place — no repost, no deleted message. Poll stickies carry their
    poll (question, choices, votes, state) across unchanged.
    """
    channel_id = int(config.get("channel_id") or 0)
    message_id = int(config.get("last_message_id") or 0)
    if channel_id <= 0 or message_id <= 0:
        raise StudioValidationError("Legacy sticky has no live message to adopt.")
    record: dict[str, Any] = {
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "by": str(config.get("updated_by") or ""),
        "role_token": "",
        "role_mode": "buttons",
        "role_actions": [],
        "form": None,
        "form_token": "",
        "sticky": {"enabled": True, "last_sent_at": None},
    }
    if poll is not None:
        record["poll"] = studio_poll_from_legacy(poll)
        record["poll_token"] = secrets.token_hex(16)
    return f"{channel_id}:{message_id}", record


__all__ = [
    "MAX_POLL_OPTIONS",
    "MIN_POLL_OPTIONS",
    "POLL_PREFIX",
    "StudioPollVoteButton",
    "cast_poll_vote",
    "install_embed_studio_poll_runtime",
    "parse_poll_custom_id",
    "poll_counts",
    "poll_custom_id",
    "poll_embed",
    "poll_total",
    "poll_view",
    "record_is_poll",
    "set_poll_state",
    "studio_poll_from_legacy",
    "studio_record_from_legacy_sticky",
    "validate_poll",
]


# ---------------------------------------------------------------------------
# Post Builder UI: Published → Polls (create + manage), plus the channel
# picker used at publish time. Every control uses the house picker
# framework or a stable custom_id, per Felix's standing picker rule.
# ---------------------------------------------------------------------------

from .embed_studio_ui import (  # noqa: E402
    StudioSession,
    _StudioPickerOpenButton,
    _StudioPickerView,
    _StudioView,
    _destination_choices,
    _menu,
    _post_blockers,
    _published,
    _say,
)
from .ui.picker import make_choice  # noqa: E402

_PENDING_POLLS: dict[tuple[int, int], dict[str, Any]] = {}


def _poll_state_emoji(item: dict[str, Any]) -> str:
    poll = item.get("poll") if isinstance(item.get("poll"), dict) else {}
    try:
        clean = validate_poll(poll)
        return {"active": "🗳️", "paused": "⏸️", "ended": "🏁"}.get(
            clean["state"], "🗳️")
    except StudioValidationError:
        return "🗳️"


def _poll_votes_text(item: dict[str, Any]) -> str:
    poll = item.get("poll") if isinstance(item.get("poll"), dict) else {}
    try:
        votes = len(validate_poll(poll)["votes"])
    except StudioValidationError:
        votes = 0
    return f"{votes} vote{'s' if votes != 1 else ''}"


def _poll_label(item: dict[str, Any]) -> str:
    """One poll's label text (record-only; pickers use the shared builder)."""
    from .embed_studio_post_labels import build_published_post_choices

    choices = build_published_post_choices(
        [("key", item)], prefix_for=lambda _key, row: _poll_state_emoji(row))
    return choices[0].label if choices else "Untitled poll"


async def _refresh_poll_message(
    interaction: discord.Interaction, post_key: str, poll: dict[str, Any], item: dict[str, Any],
) -> bool:
    """Best-effort re-render of the live poll message after a state change."""
    guild = interaction.guild
    if guild is None:
        return False
    channel = guild.get_channel(int(item.get("channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(int(item.get("message_id") or 0))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False
    if not interaction.client.user or int(message.author.id) != int(interaction.client.user.id):
        return False
    try:
        await message.edit(
            embed=poll_embed(poll),
            view=poll_view(poll, str(item.get("poll_token") or "")),
            allowed_mentions=_MENTIONS,
        )
    except (discord.Forbidden, discord.HTTPException):
        return False
    return True


async def _show_poll_control(
    interaction: discord.Interaction, session: StudioSession, key: str,
    *, notice: str = "",
) -> None:
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        from .embed_studio_ui import _error

        return await _say(interaction, _error(exc))
    item = lib.get("published", {}).get(key)
    if not record_is_poll(item):
        return await _say(interaction, "That tracked poll no longer exists.")
    poll = validate_poll(item["poll"])
    from .embed_studio_sticky import post_is_sticky

    sticky = post_is_sticky(item)
    state_words = {
        "active": "**Voting is open.** Members press a choice to vote; pressing another choice moves their vote.",
        "paused": "**Voting is paused.** The choices are shown greyed out and presses are turned away until you resume.",
        "ended": "**This poll has ended.** The buttons are gone and the final counts stay on the message.",
    }[poll["state"]]
    description = (
        f"**{poll['question']}**\n"
        f"Channel: <#{item.get('channel_id')}> • Message: {item.get('message_id')} • "
        f"{len(poll['votes'])} vote{'s' if len(poll['votes']) != 1 else ''}\n\n"
        f"{state_words}\n"
        f"Sticky: {'on — this poll is re-posted at the bottom as people chat, votes included.' if sticky else 'off.'}"
    )
    if notice:
        description = notice + "\n\n" + description
    if bool(item.get("message_missing")):
        description += (
            "\n\n⚠️ This poll's message was **deleted** in Discord. Repost "
            "starts it fresh (votes start over); Delete Post removes it "
            "from Post Builder for good."
        )
    await _menu(
        interaction,
        heading="📊 Poll",
        description=description,
        view=StudioPollControlView(
            session, key, state=poll["state"], sticky=sticky,
            missing=bool(item.get("message_missing"))),
    )


class StudioPollControlView(_StudioView):
    def __init__(
        self, session: StudioSession, key: str, *, state: str, sticky: bool,
        missing: bool = False,
    ) -> None:
        super().__init__(session)
        self.key = key
        self.state = state
        self.sticky = bool(sticky)
        self.missing = bool(missing)
        self.repost.disabled = not self.missing
        if state == "active":
            self.pause_resume.label = "Pause Voting"
            self.pause_resume.emoji = "⏸️"
        else:
            self.pause_resume.label = "Resume Voting"
            self.pause_resume.emoji = "▶️"
        self.pause_resume.disabled = state == "ended"
        self.end.disabled = state == "ended"
        self.sticky_toggle.label = "Remove Sticky" if self.sticky else "Make Sticky"

    async def _apply_state(self, interaction: discord.Interaction, state: str, verb: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            poll, item = await set_poll_state(self.session.guild_id, self.key, state)
        except StudioValidationError as exc:
            return await _show_poll_control(interaction, self.session, self.key, notice="❌ " + str(exc))
        refreshed = await _refresh_poll_message(interaction, self.key, poll, item)
        note = f"✅ Poll {verb}."
        if not refreshed:
            note += " The saved setting changed, but I could not refresh the public message — check the channel."
        await _show_poll_control(interaction, self.session, self.key, notice=note)

    @discord.ui.button(
        label="Pause Voting", emoji="⏸️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:poll_pause_resume:v1", row=0,
    )
    async def pause_resume(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.state == "active":
            await self._apply_state(interaction, "paused", "paused")
        else:
            await self._apply_state(interaction, "active", "resumed")

    @discord.ui.button(
        label="End Poll", emoji="🏁", style=discord.ButtonStyle.danger,
        custom_id="dank:embed_studio:poll_end:v1", row=0,
    )
    async def end(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply_state(interaction, "ended", "ended — final counts stay on the message")

    @discord.ui.button(
        label="Make Sticky", emoji="📌", style=discord.ButtonStyle.primary,
        custom_id="dank:embed_studio:poll_sticky_toggle:v1", row=1,
    )
    async def sticky_toggle(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_sticky import set_post_sticky

        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await set_post_sticky(
            self.session.guild_id, self.key,
            enabled=not self.sticky, client=interaction.client,
        )
        if ok and not self.sticky:
            from .embed_studio_sticky import studio_sticky_runtime

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
        await _show_poll_control(
            interaction, self.session, self.key, notice=("✅ " if ok else "❌ ") + message)

    @discord.ui.button(
        label="Repost", emoji="📨", style=discord.ButtonStyle.success,
        custom_id="dank:embed_studio:repost:v1", row=2,
    )
    async def repost(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_post_actions import run_repost

        await run_repost(interaction, self.session, self.key)

    @discord.ui.button(
        label="Delete Post", emoji="🗑️", style=discord.ButtonStyle.danger,
        custom_id="dank:embed_studio:delete_post:v1", row=2,
    )
    async def delete_post(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_post_actions import open_delete_post_confirm

        await open_delete_post_confirm(interaction, self.session, self.key)

    @discord.ui.button(
        label="Back to Polls", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:poll_control_back:v1", row=1,
    )
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await open_poll_menu(interaction, self.session)


class PollCreateModal(discord.ui.Modal, title="Create a Poll"):
    question = discord.ui.TextInput(
        label="Poll question", placeholder="What should we play on Friday?",
        min_length=1, max_length=MAX_POLL_QUESTION,
    )
    choices = discord.ui.TextInput(
        label="Choices — one per line (2 to 10)",
        placeholder="Minecraft\nMovie night\nGame tournament",
        style=discord.TextStyle.paragraph, min_length=3, max_length=1000,
    )

    def __init__(self, session: StudioSession) -> None:
        super().__init__()
        self.session = session

    async def on_submit(self, interaction: discord.Interaction) -> None:
        options = [line.strip() for line in str(self.choices.value or "").splitlines() if line.strip()]
        try:
            poll = validate_poll({
                "question": str(self.question.value or ""),
                "options": options, "votes": {}, "state": "active",
            })
        except StudioValidationError as exc:
            return await _say(interaction, f"❌ {exc}")
        _PENDING_POLLS[(self.session.guild_id, self.session.owner_id)] = poll
        guild = interaction.guild
        session = self.session

        async def on_pick(pick_interaction: discord.Interaction, value: str) -> None:
            await _publish_pending_poll(pick_interaction, session, value)

        await _menu(
            interaction,
            heading="📊 Choose a Channel for the Poll",
            description=(
                f"**{poll['question']}**\n"
                + "\n".join(f"{i + 1}. {option}" for i, option in enumerate(poll["options"]))
                + "\n\nPick where the poll goes. Nothing is posted until you choose."
            ),
            view=_StudioPickerView(
                self.session,
                choices=_destination_choices(guild) if guild is not None else [],
                on_pick=on_pick,
                custom_id="dank:embed_studio:poll_channel:v1",
                placeholder="Choose a channel for the poll…",
                title="Poll Channel",
                on_home=lambda home_interaction: open_poll_menu(home_interaction, self.session),
                home_label="Back to Polls",
            ),
        )


async def _publish_pending_poll(
    interaction: discord.Interaction, session: StudioSession, value: str,
) -> None:
    poll = _PENDING_POLLS.get((session.guild_id, session.owner_id))
    if poll is None:
        return await _say(interaction, "This poll setup expired. Start again from Post Builder → Polls.")
    guild = interaction.guild
    try:
        channel = guild.get_channel(int(value)) if guild else None
    except (TypeError, ValueError):
        channel = None
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != session.guild_id:
        return await _say(interaction, "Choose a normal text channel in this server.")
    blockers = _post_blockers(interaction, channel)
    if blockers:
        return await _say(interaction, "Publishing blocked: " + ", ".join(blockers))
    await interaction.response.defer(ephemeral=True, thinking=True)
    token = secrets.token_hex(16)
    try:
        message = await channel.send(
            embed=poll_embed(poll), view=poll_view(poll, token),
            allowed_mentions=_MENTIONS,
        )
    except (discord.Forbidden, discord.HTTPException):
        return await _say(interaction, "Dank Shield could not post the poll in that channel. Nothing was saved.")
    post_key = f"{int(channel.id)}:{int(message.id)}"
    record = {
        "channel_id": str(int(channel.id)), "message_id": str(int(message.id)),
        "by": str(session.owner_id),
        "role_token": "", "role_mode": "buttons", "role_actions": [],
        "form": None, "form_token": "",
        "poll": deepcopy(poll), "poll_token": token,
    }
    tracked = False
    try:
        source, lib = await _load_library(session.guild_id)
        lib["published"][post_key] = record
        from .embed_studio_core import trim_published_history

        lib["published"] = trim_published_history(lib["published"], max_posts=20)
        tracked = post_key in lib["published"] and await _save_library(session.guild_id, source, lib)
    except Exception:
        tracked = False
    if not tracked:
        # Tracking is authoritative: an untracked poll could never record
        # votes, so roll the public message back instead of stranding it.
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return await _say(
            interaction,
            "The poll was posted but could not be saved for vote tracking, so I removed it. Try again in a moment.")
    _PENDING_POLLS.pop((session.guild_id, session.owner_id), None)
    await _show_poll_control(
        interaction, session, post_key,
        notice=f"✅ Poll posted: {message.jump_url}")


class StudioPollMenuView(_StudioView):
    def __init__(
        self, session: StudioSession, lib: dict[str, Any], *,
        channel_names: dict[int, str] | None = None,
        fetched: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(session)
        self.add_item(_StudioPickerOpenButton(
            session, label="Manage a Poll", emoji="🗳️",
            custom_id="dank:embed_studio:poll_post:open:v1", row=1,
            heading="📊 Manage a Poll",
            description="Choose one of this server's Post Builder polls to pause, resume, end, or make sticky.",
            build=lambda interaction: _poll_post_picker_view(
                session, lib, channel_names=channel_names, fetched=fetched),
        ))

    @discord.ui.button(
        label="Create a Poll", emoji="📊", style=discord.ButtonStyle.success,
        custom_id="dank:embed_studio:poll_create:v1", row=0,
    )
    async def create(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PollCreateModal(self.session))

    @discord.ui.button(
        label="Back", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:poll_menu_back:v1", row=2,
    )
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)


def _poll_post_picker_view(
    session: StudioSession, lib: dict[str, Any], *,
    channel_names: dict[int, str] | None = None,
    fetched: dict[str, Any] | None = None,
) -> _StudioPickerView:
    from .embed_studio_post_labels import build_published_post_choices

    records = [
        (key, item) for key, item in list(lib.get("published", {}).items())[-20:]
        if record_is_poll(item)
    ]
    choices = build_published_post_choices(
        records, channel_names=channel_names, fetched=fetched,
        prefix_for=lambda _key, row: _poll_state_emoji(row),
        extra_description_for=lambda _key, row: _poll_votes_text(row),
    ) or [make_choice("No polls yet — create one first", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "No polls yet. Use Create a Poll first.")
        await _show_poll_control(interaction, session, key)

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:poll_post:v1",
        placeholder="Choose a poll to manage",
        title="Manage a Poll",
        on_home=lambda interaction: open_poll_menu(interaction, session),
        home_label="Back to Polls",
    )


async def open_poll_menu(interaction: discord.Interaction, session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        from .embed_studio_ui import _error

        return await _say(interaction, _error(exc))
    from .embed_studio_post_labels import (
        channel_names_from_guild, collect_published_post_info,
    )

    records = [
        (key, item) for key, item in list(lib.get("published", {}).items())[-20:]
        if record_is_poll(item)
    ]
    fetched = await collect_published_post_info(interaction.guild, records)
    await _menu(
        interaction, heading="📊 Polls",
        description=(
            "Create a poll and post it to a channel, or manage one you "
            "already posted. Members vote with the buttons on the poll — "
            "one choice each, and pressing another choice moves their "
            "vote. Polls can be made sticky, and their votes move with "
            "them when they are re-posted at the bottom of the chat."
        ),
        view=StudioPollMenuView(
            session, lib,
            channel_names=channel_names_from_guild(interaction.guild),
            fetched=fetched,
        ),
    )
