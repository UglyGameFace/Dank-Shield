from __future__ import annotations

"""Dead Server Clapbacks: snippy auto-replies to "dead server" complaints.

A member who calls the server/chat dead gets one short reply, in the spirit
of: people have lives, this community is being built from a small friend
group, and a server cannot stay busy 24/7 if everyone leaves the moment a
voice channel is quiet -- especially when they never pinged the server's
sesh/stoner role first.

Design notes:

- The listener is deliberately cheap. Message text is matched locally first;
  guild config is only loaded after a textual match, so ordinary chat never
  pays a config read for this feature.
- The feature is DEFAULT OFF per guild (``dead_server_clapbacks_enabled``).
- Replies tag the complainer: the text leads with their ``<@id>`` mention
  and ``allowed_mentions`` permits exactly that one user, so the tag
  notifies them. Role mentions never ping -- the ping-role lines render
  the role *name* as plain text (``@Stoners``), and roles/everyone are
  excluded from ``allowed_mentions``.
- Cooldowns are in-process (per channel and per user), matching the other
  lightweight community runtimes. A restart simply resets them.
- A member who joined within the last 24 hours gets the new-joiner lines
  only: "you just got here" is the most specific fact about them, so it
  wins over every other category, including DIDNT_PING.
"""

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional

import discord

from .community_pings_service import parse_community_pings, toke_role_ids
from .guild_config import get_guild_config


ENABLED_KEY = "dead_server_clapbacks_enabled"
PING_ROLE_KEY = "clapback_ping_role_id"
CHANNEL_COOLDOWN_KEY = "dead_server_clapback_channel_cooldown_seconds"
USER_COOLDOWN_KEY = "dead_server_clapback_user_cooldown_seconds"
LINES_KEY = "dead_server_clapback_lines"

DEFAULT_CHANNEL_COOLDOWN_SECONDS = 10 * 60
DEFAULT_USER_COOLDOWN_SECONDS = 60 * 60
MAX_CHANNEL_COOLDOWN_SECONDS = 24 * 60 * 60
MAX_USER_COOLDOWN_SECONDS = 7 * 24 * 60 * 60

CATEGORY_DIDNT_PING = "didnt_ping"
CATEGORY_LIVES = "lives"
CATEGORY_ORIGINS = "origins"
CATEGORY_VC_PARADOX = "vc_paradox"
CATEGORY_GENERIC = "generic"
CATEGORY_NEW_JOINER = "new_joiner"
CATEGORIES = (
    CATEGORY_DIDNT_PING,
    CATEGORY_LIVES,
    CATEGORY_ORIGINS,
    CATEGORY_VC_PARADOX,
    CATEGORY_GENERIC,
    CATEGORY_NEW_JOINER,
)

# A member whose join is younger than this is a "new joiner": if they
# immediately call the server dead, only the new-joiner lines are eligible.
NEW_JOINER_WINDOW = timedelta(hours=24)

# Built-in lines, verbatim in Felix's voice. ``@Stoners`` is intentional
# plain text: replies are sent with mentions disabled, and the name is
# swapped for the guild's configured ping-role name at render time.
BUILTIN_LINES: dict[str, tuple[str, ...]] = {
    CATEGORY_DIDNT_PING: (
        "dead? you didn't even ping @Stoners. can't complain about an empty room you never knocked on 💀",
        "the @Stoners role is RIGHT there. you typed 'dead server' instead of using it. interesting choice",
        "nobody got pinged, nobody got invited, but somehow the server's the problem. ok",
    ),
    CATEGORY_LIVES: (
        "it's called people having jobs. and lives. and sleep schedules. wild concept",
        "people are at work, asleep, or high on their own couch. the server is fine",
        "not everyone can be in VC 24/7. some of us touch grass professionally",
    ),
    CATEGORY_ORIGINS: (
        "this server started as 6 friends in a group chat. SIX. growth takes a minute, relax",
        "we're building this from a friend group of 6. empires weren't built in a day either",
    ),
    CATEGORY_VC_PARADOX: (
        "everyone wants a packed VC but nobody wants to be the first one in it. be the first one in it",
        "you sat in VC for 40 seconds, saw it empty, and left. you are the reason it's empty 😭",
        "a server can't be poppin 24/7 if everybody bounces the second it's quiet. that's just math",
    ),
    CATEGORY_GENERIC: (
        "saying 'dead server' while actively typing in the server is crazy work",
        "chat's not dead, it's just not performing on demand. start something",
        "when's the last time YOU started a conversation in here? asking for the server",
    ),
    CATEGORY_NEW_JOINER: (
        "you've been here 4 minutes and you're already leaving a yelp review. incredible",
        "you just joined. you haven't even seen the server yet and it's dead? ok",
        "walked in 2 minutes ago and declared the party over. stay awhile, you ARE the party",
        "brand new and already complaining. at least look around first, damn",
    ),
}

_RUNTIME_ATTR = "_dank_dead_server_clapback_runtime"

_ALLOWED_CHANNEL_TYPES = frozenset(
    {
        discord.ChannelType.text,
        discord.ChannelType.public_thread,
        discord.ChannelType.private_thread,
        discord.ChannelType.news_thread,
    }
)
_THREAD_TYPES = frozenset(
    {
        discord.ChannelType.public_thread,
        discord.ChannelType.private_thread,
        discord.ChannelType.news_thread,
    }
)


def _log(message: str) -> None:
    try:
        print(f"dead_server_clapbacks: {message}", flush=True)
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

_SUBJECT_PATTERNS = (
    # "dead server / dead chat / dead vc"
    re.compile(r"\bdead\s+(?:server|chat|vc)\b"),
    # "server dead", "server is dead", "server's dead", "server is so dead",
    # "chat dead", "vc is dead", ...
    re.compile(
        r"\b(?:server|chat|vc)(?:'s|\s+is(?:\s+(?:so|really|super))?|\s+(?:so|really|super))?\s+dead\b"
    ),
    # "this place is dead" / "place is dead"
    re.compile(r"\b(?:this\s+)?place\s+is\s+(?:so\s+)?dead\b"),
)

_STANDALONE_PATTERNS = (
    re.compile(r"\bdead\s+af\b"),
    re.compile(r"\bdead\s+in\s+here\b"),
)

# Slang / unrelated uses that must veto the *standalone* patterns. Subject
# patterns ("server is dead") still win when both appear in one message.
_EXCLUSION_PATTERN = re.compile(
    r"\bdeadass\b|\bdead\s+ass\b|\bdrop\s+dead\b|\bdead\s+tired\b"
)


def normalize_message_text(content: Any) -> str:
    text = str(content or "").replace("’", "'").replace("‘", "'").lower()
    return " ".join(text.split())


def matches_dead_server_complaint(content: Any) -> bool:
    """Return True when the text is a "this server/chat is dead" complaint."""
    text = normalize_message_text(content)
    if not text or "dead" not in text:
        return False
    if any(pattern.search(text) for pattern in _SUBJECT_PATTERNS):
        return True
    if _EXCLUSION_PATTERN.search(text):
        return False
    return any(pattern.search(text) for pattern in _STANDALONE_PATTERNS)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def resolve_ping_role_id(config: Mapping[str, Any]) -> int:
    """Resolve the guild's stoner/sesh ping role without ever guessing.

    Precedence: the clapback-specific override, the legacy Stoners role,
    the Community & Pings toke roles, then the legacy sesh ping role.
    Returns 0 when nothing is configured -- DIDNT_PING lines are then
    skipped entirely.
    """
    explicit = _safe_int(config.get(PING_ROLE_KEY), 0)
    if explicit > 0:
        return explicit
    stoner = _safe_int(config.get("stoner_role_id"), 0)
    if stoner > 0:
        return stoner
    try:
        parsed = parse_community_pings(config)
        starter, notify = toke_role_ids(parsed, config)
        if starter > 0:
            return int(starter)
        if notify > 0:
            return int(notify)
    except Exception:
        pass
    return _safe_int(config.get("sesh_ping_role_id"), 0)


def _custom_lines(raw: Any) -> dict[str, tuple[str, ...]]:
    """Parse the optional guild override for reply lines.

    Accepts ``{category: [lines]}`` or a flat list (treated as GENERIC).
    A non-empty custom list replaces the built-ins for that category only.
    """
    out: dict[str, tuple[str, ...]] = {}

    def clean(items: Any) -> tuple[str, ...]:
        if not isinstance(items, (list, tuple)):
            return ()
        lines = []
        for item in items:
            text = " ".join(str(item or "").split()).strip()
            if text:
                lines.append(text[:500])
            if len(lines) >= 25:
                break
        return tuple(lines)

    if isinstance(raw, Mapping):
        for category in CATEGORIES:
            lines = clean(raw.get(category))
            if lines:
                out[category] = lines
    elif isinstance(raw, (list, tuple)):
        lines = clean(raw)
        if lines:
            out[CATEGORY_GENERIC] = lines
    return out


@dataclass(frozen=True)
class ClapbackConfig:
    enabled: bool = False
    ping_role_id: int = 0
    channel_cooldown_seconds: int = DEFAULT_CHANNEL_COOLDOWN_SECONDS
    user_cooldown_seconds: int = DEFAULT_USER_COOLDOWN_SECONDS
    lines: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def lines_for(self, category: str) -> tuple[str, ...]:
        return tuple(self.lines.get(category) or BUILTIN_LINES.get(category) or ())


def parse_clapback_config(config: Optional[Mapping[str, Any]]) -> ClapbackConfig:
    source: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    channel_cooldown = _safe_int(
        source.get(CHANNEL_COOLDOWN_KEY), DEFAULT_CHANNEL_COOLDOWN_SECONDS
    )
    user_cooldown = _safe_int(source.get(USER_COOLDOWN_KEY), DEFAULT_USER_COOLDOWN_SECONDS)
    return ClapbackConfig(
        enabled=_safe_bool(source.get(ENABLED_KEY), False),
        ping_role_id=resolve_ping_role_id(source),
        channel_cooldown_seconds=max(
            0, min(int(channel_cooldown), MAX_CHANNEL_COOLDOWN_SECONDS)
        ),
        user_cooldown_seconds=max(0, min(int(user_cooldown), MAX_USER_COOLDOWN_SECONDS)),
        lines=_custom_lines(source.get(LINES_KEY)),
    )


# ---------------------------------------------------------------------------
# Line selection
# ---------------------------------------------------------------------------


def message_mentions_ping_role(message: Any, role_id: int) -> bool:
    rid = int(role_id or 0)
    if rid <= 0:
        return False
    for role in getattr(message, "role_mentions", None) or ():
        if _safe_int(getattr(role, "id", 0), 0) == rid:
            return True
    content = str(getattr(message, "content", "") or "")
    return f"<@&{rid}>" in content


def is_new_joiner(author: Any, *, now: Optional[datetime] = None) -> bool:
    """True when the author's guild join is within ``NEW_JOINER_WINDOW``.

    Reads ``joined_at`` off the member object (discord.py ``Member``).
    A missing/None ``joined_at`` (uncached or partial member) is NOT a
    new joiner -- category selection then behaves exactly as before.
    """
    joined_at = getattr(author, "joined_at", None)
    if joined_at is None:
        return False
    moment = now or datetime.now(timezone.utc)
    try:
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return timedelta(0) <= moment - joined_at <= NEW_JOINER_WINDOW
    except Exception:
        return False


def eligible_categories(
    *,
    ping_role_id: int,
    did_ping: bool,
    new_joiner: bool = False,
) -> tuple[str, ...]:
    """Categories a reply may draw from for this triggering message.

    A new joiner (joined within ``NEW_JOINER_WINDOW``) draws ONLY from
    NEW_JOINER -- being brand new is the most specific fact and wins
    over everything else. Otherwise DIDNT_PING is only eligible when a
    ping role is configured AND the complainer did not use it -- the
    whole point of those lines.
    """
    if new_joiner:
        return (CATEGORY_NEW_JOINER,)
    categories: list[str] = []
    if int(ping_role_id or 0) > 0 and not did_ping:
        categories.append(CATEGORY_DIDNT_PING)
    categories.extend(
        (CATEGORY_LIVES, CATEGORY_ORIGINS, CATEGORY_VC_PARADOX, CATEGORY_GENERIC)
    )
    return tuple(categories)


def render_line(template: str, *, ping_role_name: str = "") -> str:
    name = " ".join(str(ping_role_name or "").split()).strip()
    if name and name != "Stoners":
        return str(template).replace("Stoners", name)
    return str(template)


def pick_clapback_line(
    config: ClapbackConfig,
    *,
    did_ping: bool,
    ping_role_name: str = "",
    last_line: str = "",
    rng: Optional[random.Random] = None,
    new_joiner: bool = False,
) -> Optional[tuple[str, str]]:
    """Pick (category, rendered line); never repeats ``last_line`` if avoidable."""
    picker = rng or random
    categories = [
        category
        for category in eligible_categories(
            ping_role_id=config.ping_role_id, did_ping=did_ping, new_joiner=new_joiner
        )
        if config.lines_for(category)
    ]
    if not categories:
        return None
    category = picker.choice(categories)
    lines = config.lines_for(category)
    choices = [line for line in lines if line != last_line] or list(lines)
    return category, render_line(picker.choice(choices), ping_role_name=ping_role_name)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


def _has_manage_messages(message: Any) -> bool:
    author = getattr(message, "author", None)
    guild_perms = getattr(author, "guild_permissions", None)
    if guild_perms is not None and (
        bool(getattr(guild_perms, "manage_messages", False))
        or bool(getattr(guild_perms, "administrator", False))
    ):
        return True
    channel = getattr(message, "channel", None)
    permissions_for = getattr(channel, "permissions_for", None)
    if callable(permissions_for) and author is not None:
        try:
            perms = permissions_for(author)
        except Exception:
            perms = None
        if perms is not None and (
            bool(getattr(perms, "manage_messages", False))
            or bool(getattr(perms, "administrator", False))
        ):
            return True
    return False


def _is_ticket_context(message: Any, config: Mapping[str, Any]) -> bool:
    """Cheap ticket check: configured ticket category, or a ticket parent.

    Covers ticket channels themselves and threads inside them, using only
    ids/names already on the message objects -- no database or API calls.
    """
    channel = getattr(message, "channel", None)
    guild = getattr(message, "guild", None)
    ticket_category_id = _safe_int(config.get("ticket_category_id"), 0)

    def category_matches(category_id: Any) -> bool:
        return bool(
            ticket_category_id > 0 and _safe_int(category_id, 0) == ticket_category_id
        )

    if category_matches(getattr(channel, "category_id", None)):
        return True

    channel_type = getattr(channel, "type", None)
    if channel_type in _THREAD_TYPES:
        parent = getattr(channel, "parent", None)
        if parent is None and guild is not None:
            parent_id = _safe_int(getattr(channel, "parent_id", None), 0)
            if parent_id > 0:
                getter = getattr(guild, "get_channel", None)
                if callable(getter):
                    try:
                        parent = getter(parent_id)
                    except Exception:
                        parent = None
        if parent is not None:
            if category_matches(getattr(parent, "category_id", None)):
                return True
            parent_category = getattr(parent, "category", None)
            names = (
                str(getattr(parent, "name", "") or "").lower(),
                str(getattr(parent_category, "name", "") or "").lower(),
            )
            if any("ticket" in name for name in names):
                return True
    return False


class DeadServerClapbackRuntime:
    """Owns the on_message listener for Dead Server Clapbacks."""

    def __init__(
        self,
        bot: Any,
        *,
        config_loader: Optional[Callable[[int], Awaitable[Mapping[str, Any]]]] = None,
        rng: Optional[random.Random] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.bot = bot
        self._config_loader = config_loader
        self._rng = rng or random.Random()
        self._clock = clock or time.monotonic
        self._channel_last: dict[tuple[int, int], float] = {}
        self._user_last: dict[tuple[int, int], float] = {}
        self._last_line: dict[int, str] = {}

    async def _load_config(self, guild_id: int) -> Mapping[str, Any]:
        if self._config_loader is not None:
            return await self._config_loader(int(guild_id))
        return await get_guild_config(int(guild_id))

    def _cooldown_blocked(
        self, config: ClapbackConfig, *, guild_id: int, channel_id: int, user_id: int
    ) -> bool:
        now = float(self._clock())
        channel_at = self._channel_last.get((guild_id, channel_id))
        if (
            channel_at is not None
            and int(config.channel_cooldown_seconds) > 0
            and now - channel_at < int(config.channel_cooldown_seconds)
        ):
            return True
        user_at = self._user_last.get((guild_id, user_id))
        if (
            user_at is not None
            and int(config.user_cooldown_seconds) > 0
            and now - user_at < int(config.user_cooldown_seconds)
        ):
            return True
        return False

    def _record_fire(self, *, guild_id: int, channel_id: int, user_id: int, line: str) -> None:
        now = float(self._clock())
        self._channel_last[(guild_id, channel_id)] = now
        self._user_last[(guild_id, user_id)] = now
        self._last_line[guild_id] = line

    async def on_message(self, message: Any) -> bool:
        """Return True when a clapback reply was sent."""
        guild = getattr(message, "guild", None)
        if guild is None:
            return False
        author = getattr(message, "author", None)
        if author is None or bool(getattr(author, "bot", False)):
            return False
        if getattr(message, "webhook_id", None):
            return False
        message_type = getattr(message, "type", None)
        if message_type is not None and message_type not in (
            discord.MessageType.default,
            discord.MessageType.reply,
        ):
            return False

        channel = getattr(message, "channel", None)
        channel_type = getattr(channel, "type", None)
        if channel_type is not None:
            if channel_type not in _ALLOWED_CHANNEL_TYPES:
                return False
        elif not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False

        content = str(getattr(message, "content", "") or "")
        if not matches_dead_server_complaint(content):
            return False

        guild_id = _safe_int(getattr(guild, "id", 0), 0)
        author_id = _safe_int(getattr(author, "id", 0), 0)
        channel_id = _safe_int(getattr(channel, "id", 0), 0)
        if guild_id <= 0 or author_id <= 0 or channel_id <= 0:
            return False

        # Exempt the server owner and anyone who can moderate the channel.
        if _safe_int(getattr(guild, "owner_id", 0), 0) == author_id:
            return False
        owner = getattr(guild, "owner", None)
        if owner is not None and _safe_int(getattr(owner, "id", 0), 0) == author_id:
            return False
        if _has_manage_messages(message):
            return False

        try:
            raw_config = await self._load_config(guild_id)
        except Exception as exc:
            _log(f"config load failed guild={guild_id}: {type(exc).__name__}: {exc}")
            return False
        if not isinstance(raw_config, Mapping):
            return False
        if str(raw_config.get("source") or "").startswith("unavailable:"):
            return False

        config = parse_clapback_config(raw_config)
        if not config.enabled:
            return False
        if _is_ticket_context(message, raw_config):
            return False

        did_ping = message_mentions_ping_role(message, config.ping_role_id)
        new_joiner = is_new_joiner(author)
        if self._cooldown_blocked(
            config, guild_id=guild_id, channel_id=channel_id, user_id=author_id
        ):
            return False

        role_name = ""
        if config.ping_role_id > 0:
            get_role = getattr(guild, "get_role", None)
            if callable(get_role):
                try:
                    role = get_role(int(config.ping_role_id))
                except Exception:
                    role = None
                if role is not None:
                    role_name = str(getattr(role, "name", "") or "")

        picked = pick_clapback_line(
            config,
            did_ping=did_ping,
            ping_role_name=role_name,
            last_line=self._last_line.get(guild_id, ""),
            rng=self._rng,
            new_joiner=new_joiner,
        )
        if picked is None:
            return False
        _category, line = picked

        # Record before sending so a burst of complaints cannot race
        # multiple replies past the cooldowns.
        self._record_fire(
            guild_id=guild_id, channel_id=channel_id, user_id=author_id, line=line
        )
        try:
            await message.reply(
                content=f"<@{author_id}> {line}",
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=[author],
                    roles=False,
                    replied_user=False,
                ),
            )
        except Exception as exc:
            _log(f"reply failed guild={guild_id}: {type(exc).__name__}: {exc}")
            return False
        return True


def ensure_dead_server_clapback_runtime(bot: Any) -> DeadServerClapbackRuntime:
    existing = getattr(bot, _RUNTIME_ATTR, None)
    if isinstance(existing, DeadServerClapbackRuntime):
        return existing
    runtime = DeadServerClapbackRuntime(bot)
    setattr(bot, _RUNTIME_ATTR, runtime)
    bot.add_listener(runtime.on_message, "on_message")
    return runtime


def dead_server_clapback_runtime(bot: Any) -> Optional[DeadServerClapbackRuntime]:
    runtime = getattr(bot, _RUNTIME_ATTR, None)
    return runtime if isinstance(runtime, DeadServerClapbackRuntime) else None


__all__ = [
    "BUILTIN_LINES",
    "CATEGORIES",
    "CATEGORY_DIDNT_PING",
    "CATEGORY_GENERIC",
    "CATEGORY_LIVES",
    "CATEGORY_NEW_JOINER",
    "CATEGORY_ORIGINS",
    "CATEGORY_VC_PARADOX",
    "CHANNEL_COOLDOWN_KEY",
    "ClapbackConfig",
    "DEFAULT_CHANNEL_COOLDOWN_SECONDS",
    "DEFAULT_USER_COOLDOWN_SECONDS",
    "DeadServerClapbackRuntime",
    "ENABLED_KEY",
    "LINES_KEY",
    "NEW_JOINER_WINDOW",
    "PING_ROLE_KEY",
    "USER_COOLDOWN_KEY",
    "dead_server_clapback_runtime",
    "eligible_categories",
    "ensure_dead_server_clapback_runtime",
    "is_new_joiner",
    "matches_dead_server_complaint",
    "message_mentions_ping_role",
    "normalize_message_text",
    "parse_clapback_config",
    "pick_clapback_line",
    "render_line",
    "resolve_ping_role_id",
]
