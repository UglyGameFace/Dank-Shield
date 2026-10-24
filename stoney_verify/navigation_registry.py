from __future__ import annotations

"""Canonical navigation metadata for the public Dank Shield UI.

The registry owns discoverability, category placement, labels, and search aliases.
Feature modules continue to own authorization, persistence, and business logic.
"""

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class NavigationCategory:
    key: str
    label: str
    emoji: str
    description: str


@dataclass(frozen=True)
class NavigationFeature:
    key: str
    label: str
    emoji: str
    category: str
    description: str
    aliases: tuple[str, ...] = ()
    manager_only: bool = False


CATEGORIES: tuple[NavigationCategory, ...] = (
    NavigationCategory(
        "setup",
        "Setup & Server Settings",
        "⚙️",
        "Start or change server settings, including channels, roles, and who can use them.",
    ),
    NavigationCategory(
        "access",
        "Onboarding & Access",
        "🚪",
        "Help new members join: rules, verification, welcome messages, and access.",
    ),
    NavigationCategory(
        "actions",
        "Actions & Notifications",
        "📋",
        "See your to-dos, send optional reminders, and manage server notifications.",
    ),
    NavigationCategory(
        "safety",
        "Safety & Moderation",
        "🛡️",
        "Stop spam, block unwanted invites, moderate members, and set up AI Voice Safety.",
    ),
    NavigationCategory(
        "people",
        "Members, Roles & Profiles",
        "👥",
        "Manage member roles, profile options, and member information.",
    ),
    NavigationCategory(
        "community",
        "Community & Engagement",
        "🌿",
        "Share Router downloads, Movie Night, /toke sessions, event pings, and games.",
    ),
    NavigationCategory(
        "tickets",
        "Tickets & Support",
        "🎫",
        "Help members with support tickets, forms, and ticket settings.",
    ),
    NavigationCategory(
        "design",
        "Design & Branding",
        "🎨",
        "Make announcement embeds, style server names, and manage welcome artwork.",
    ),
    NavigationCategory(
        "voice",
        "Voice & Accessibility",
        "🔊",
        "Live Captions and optional AI Voice Safety for voice chats.",
    ),
    NavigationCategory(
        "ops",
        "Logs, Stats & Diagnostics",
        "📊",
        "View server activity, member counts, ticket counts, and bot health.",
    ),
    NavigationCategory(
        "my",
        "My Dank Shield",
        "👤",
        "Your profile, optional roles, reminder choices, and Member Setup progress.",
    ),
    NavigationCategory(
        "utilities",
        "Utilities & Help",
        "🧰",
        "Find tools and learn how to use Dank Shield.",
    ),
)


FEATURES: tuple[NavigationFeature, ...] = (
    NavigationFeature(
        "setup_center",
        "Setup & Settings",
        "⚙️",
        "setup",
        "Choose server channels, roles, and settings in one place.",
        ("setup", "configure server", "server settings", "fix access", "permissions", "roles channels"),
        True,
    ),
    NavigationFeature(
        "verification",
        "Verification",
        "✅",
        "access",
        "Set the rules and steps members complete to join your server.",
        ("verify", "unverified", "verified", "verification setup", "role mapping", "verify panel"),
        True,
    ),
    NavigationFeature(
        "action_reminder_center",
        "Action & Reminder Center",
        "📋",
        "actions",
        "See what needs attention and open reminder settings. Messages send only when enabled.",
        ("my actions", "action inbox", "reminder center", "actions and notifications", "actions & notifications", "tasks", "pending actions", "required actions", "to do"),
    ),
    NavigationFeature(
        "action_notification_systems",
        "Other Notification Settings",
        "🧭",
        "actions",
        "Find the settings for verification, member activity, tickets, events, and other messages.",
        ("existing reminders", "notification owners", "notification systems", "notification directory", "reminder systems", "other notification settings"),
        True,
    ),
    NavigationFeature(
        "action_builder",
        "Reminder Rules (Preview)",
        "🛠️",
        "actions",
        "Review which members should receive a reminder. Preview rules before using them.",
        ("action builder", "reminder builder", "action rules", "draft actions", "preview reminder rules", "who gets reminders"),
        True,
    ),
    NavigationFeature(
        "action_safety_schedule",
        "Reminder Times & Limits",
        "🕓",
        "actions",
        "Choose safe reminder times and limits. Staff can enable automatic Member Setup messages after setup.",
        ("safety schedule", "reminder schedule", "delivery readiness", "quiet hours reminder", "reminder timing", "reminder times", "automatic setup messages"),
        True,
    ),
    NavigationFeature(
        "action_my_preferences",
        "My Reminder Preferences",
        "🔔",
        "actions",
        "Choose whether you want optional Member Setup reminders, and check your status.",
        ("my reminder preferences", "setup dm opt in", "stop setup dms", "reminder opt out", "my notification preferences"),
    ),
    NavigationFeature(
        "verification_reminders",
        "Verification Reminders",
        "📩",
        "access",
        "Remind members who have not verified yet. They do not need to opt in individually.",
        ("verify reminders", "unverified reminders", "verification notices", "verification database", "pending members"),
        True,
    ),
    NavigationFeature(
        "welcome",
        "Welcome, Join & Exit",
        "👋",
        "access",
        "Configure join/leave messages, cards, channels, and onboarding presentation.",
        ("welcome", "join", "leave", "exit", "welcome card", "exit card", "join card"),
        True,
    ),
    NavigationFeature(
        "member_setup_manager",
        "Member Setup Manager",
        "🧭",
        "access",
        "Configure Member Setup, eligibility prerequisites, access role, revisions, and Strict Gate.",
        (
            "member setup manager",
            "access gate",
            "strict gate",
            "member access",
            "eligibility prerequisite",
            "protected categories",
            "setup revision",
        ),
        True,
    ),
    NavigationFeature(
        "protection",
        "Protection",
        "🛡️",
        "safety",
        "Manage Spam Guard, Invite Shield, AntiNuke, filters, and AI Voice Safety.",
        ("spam guard", "spamguard", "invite shield", "antinuke", "anti nuke", "automod", "link shield", "filters", "voice safety protection"),
        True,
    ),
    NavigationFeature(
        "members_moderation",
        "Members & Moderation",
        "👮",
        "safety",
        "Moderate members, clean up messages, review activity, and use recovery tools.",
        ("members", "moderation", "mod", "ban", "timeout", "cleanup", "purge", "member activity"),
        True,
    ),
    NavigationFeature(
        "roles_profiles",
        "Roles & Profiles",
        "🎭",
        "people",
        "Create and manage server roles and member profiles.",
        ("roles", "role editor", "create role", "role health", "profiles", "server role editor"),
        True,
    ),
    NavigationFeature(
        "profile_builder",
        "Profile Builder",
        "🌿",
        "people",
        "Choose the profile options and badges members can use.",
        ("profile builder", "profile panel", "profile roles", "cosmetic roles", "pronouns", "interests"),
        True,
    ),
    NavigationFeature(
        "community_pings_manager",
        "Community & Pings Manager",
        "🌿",
        "community",
        "Choose optional server roles that members can join for events and notifications.",
        ("community pings", "ping roles", "notification roles", "stoner", "sesh pings", "toke setup"),
        True,
    ),
    NavigationFeature(
        "community_tools",
        "Community Tools",
        "🧰",
        "community",
        "Create polls or pinned messages, check permissions, and use handy server tools.",
        (
            "sticky",
            "stickies",
            "poll",
            "server tools",
            "permission check",
            "weather",
            "wikipedia",
            "wikihow",
            "urban dictionary",
            "dice",
            "coin flip",
        ),
    ),
    NavigationFeature(
        "share_router",
        "Share Router",
        "🔗",
        "community",
        "Choose which channels share videos, photos, or links. Keep original posts if you prefer.",
        ("share", "share route", "share router", "memes", "share sheet", "proxy channel", "facebook videos", "inline media"),
        True,
    ),
    NavigationFeature(
        "share_downloads",
        "Share Router Downloads",
        "📥",
        "community",
        "Manage video downloads, private viewing links, and test troublesome social links.",
        ("downloads", "download channel", "download settings", "watch link", "download link", "facebook download", "video hosting", "download expiry", "share router download"),
        True,
    ),
    NavigationFeature(
        "toke_settings",
        "Toke Sesh Settings",
        "🍃",
        "community",
        "Configure meetup cards, live voice presence, session limits, and timing.",
        ("toke", "toke settings", "toke session", "sesh", "sesh card", "cheers", "who's coming", "meetup", "live attendance"),
        True,
    ),
    NavigationFeature(
        "somewhere_420",
        "It's 4:20 Somewhere",
        "🌎",
        "community",
        "Explore live 4:20 world clocks, share a safe celebration, or start a /toke sesh.",
        ("420", "4:20", "four twenty", "somewhere", "world clock", "worldwide 420", "420 time"),
    ),
    NavigationFeature(
        "community_hub",
        "Community Hub",
        "🎮",
        "community",
        "Help people find friends, games, and other players.",
        ("hub", "lfg", "party", "cross server", "community hub"),
    ),
    NavigationFeature(
        "movie_night",
        "Dank Cinema & Movie Night",
        "🎬",
        "community",
        "Open Dank Cinema watch parties, source providers, subtitles, and synced viewing.",
        (
            "movie",
            "movie night",
            "watch party",
            "watch together",
            "torrent streaming",
            "group streaming",
            "movies",
            "cinema",
            "dank cinema",
            "subtitles",
            "subtitle providers",
            "watch party sync",
            "automatic source",
        ),
    ),
    NavigationFeature(
        "tickets",
        "Tickets",
        "🎫",
        "tickets",
        "See open support tickets and choose how people ask for help.",
        ("ticket", "tickets", "support", "ticket panel", "ticket category", "intake", "routing", "forms"),
        True,
    ),
    NavigationFeature(
        "embed_studio",
        "Embed Studio",
        "🧱",
        "design",
        "Create styled announcements with media links, smart text, brand themes, templates and saved drafts.",
        ("embed", "embed builder", "embed studio", "announcement builder", "create announcement", "message designer", "design a message", "embed editor", "custom embed", "json embed", "save embed", "brand themes", "server themes", "branding", "embed color palettes", "media library", "saved images", "smart text", "embed placeholders"),
        True,
    ),
    NavigationFeature(
        "server_design",
        "Server Design",
        "🎨",
        "design",
        "Style channel names and the look of your server.",
        ("design", "server design", "search safe", "search-safe", "channel fonts", "channel names", "category names"),
        True,
    ),
    NavigationFeature(
        "card_assets",
        "Card Assets",
        "📎",
        "design",
        "Choose the pictures and fonts used in welcome and goodbye messages.",
        ("card assets", "background", "join background", "exit background", "font", "custom font"),
        True,
    ),
    NavigationFeature(
        "live_captions",
        "Live Captions",
        "📝",
        "voice",
        "Show live text captions for voice chat, where enabled.",
        ("captions", "live captions", "voice captions", "transcription", "transcribe"),
    ),
    NavigationFeature(
        "voice_safety",
        "AI Voice Safety",
        "🎙️",
        "voice",
        "Configure voice safety, where monitored chats are listed, and staff alerts.",
        ("voice safety", "voice moderation", "ai voice moderation", "voice patrol", "voicepatrol", "sexual talk in voice", "sexual content", "sexual harassment in voice", "voice harassment", "voice chat protection", "voice protection", "voice monitor", "voice consent", "safety voice", "moderate voice"),
        True,
    ),
    NavigationFeature(
        "logs_activity",
        "Logs & Activity",
        "🧾",
        "ops",
        "Choose where join, leave, and moderation logs appear.",
        ("logs", "modlog", "activity", "member logs", "audit log"),
        True,
    ),
    NavigationFeature(
        "server_stats",
        "Server Stats",
        "📊",
        "ops",
        "Set up member and ticket counters. Repair counters if a channel was deleted.",
        ("stats", "server stats", "counters", "member count", "ticket count"),
        True,
    ),
    NavigationFeature(
        "status",
        "Status",
        "📡",
        "ops",
        "See whether Dank Shield is online and running.",
        ("status", "bot status", "online", "uptime"),
    ),
    NavigationFeature(
        "diagnostics",
        "Diagnostics",
        "🩺",
        "ops",
        "Check what is working and what needs attention; no changes are made.",
        ("diagnostics", "health", "errors", "troubleshoot", "startup"),
        True,
    ),
    NavigationFeature(
        "my_profile",
        "My Profile",
        "🪪",
        "my",
        "Edit your own profile, privacy settings, and appearance.",
        ("my profile", "profile", "signature", "privacy", "platforms", "appearance"),
    ),
    NavigationFeature(
        "my_member_setup",
        "My Member Setup",
        "🧭",
        "my",
        "See what you still need to finish to access the server.",
        ("my setup", "member setup", "review setup", "account setup", "access setup"),
    ),
    NavigationFeature(
        "profile_tags",
        "Profile Tags & Cosmetics",
        "🎭",
        "my",
        "Choose optional badges and roles for your profile.",
        ("profile tags", "cosmetics", "pronouns", "identity", "interests"),
    ),
    NavigationFeature(
        "my_community_pings",
        "My Community & Pings",
        "🌿",
        "my",
        "Choose which community activities and alerts to join.",
        ("my pings", "notifications", "community roles", "ping preferences", "sesh pings"),
    ),
    NavigationFeature(
        "help",
        "Help",
        "❓",
        "utilities",
        "Find a feature or learn what a command does.",
        ("help", "commands", "where is", "how do i", "find feature"),
    ),
)


_CATEGORY_BY_KEY = {item.key: item for item in CATEGORIES}
_FEATURE_BY_KEY = {item.key: item for item in FEATURES}



# Ship notes describe *merged* public improvements, not unmerged pull requests.
# This remains metadata only: every button uses the canonical feature owner.
# Update this list whenever a visible feature changes, and keep its claim true.
@dataclass(frozen=True)
class RecentFeatureUpdate:
    key: str
    headline: str
    detail: str
    merged_date: str


RECENT_UPDATES: tuple[RecentFeatureUpdate, ...] = (
    RecentFeatureUpdate(
        "embed_studio", "Media Library, Smart Text and brand themes",
        "Reuse saved images across embeds, add safe server details, and keep your designs consistent.", "2026-10-24",
    ),
    RecentFeatureUpdate(
        "share_router", "Share videos from regular channels",
        "Keep original posts and choose videos, images, links, or everything.", "2026-10-24",
    ),
    RecentFeatureUpdate(
        "share_downloads", "More reliable social video links",
        "Better Facebook link detection and backup video formats where supported.", "2026-10-24",
    ),
    RecentFeatureUpdate(
        "action_safety_schedule", "Optional setup reminders can send automatically",
        "Staff choose when to turn on Member Setup messages; off until enabled.", "2026-10-23",
    ),
    RecentFeatureUpdate(
        "action_reminder_center", "Reminders have their own home",
        "Open your to-dos, message preferences, and reminder settings in one area.", "2026-10-23",
    ),
    RecentFeatureUpdate(
        "verification_reminders", "Reminders for unverified members",
        "Server owners can send verification reminders without member opt-in.", "2026-10-23",
    ),
    RecentFeatureUpdate(
        "somewhere_420", "See where it's 4:20",
        "Browse the world clock and jump into a sesh with the existing /toke flow.", "2026-10-22",
    ),
)
RECENT_FEATURE_KEYS: tuple[str, ...] = tuple(item.key for item in RECENT_UPDATES)


def recent_features() -> tuple[NavigationFeature, ...]:
    return tuple(_FEATURE_BY_KEY[key] for key in RECENT_FEATURE_KEYS)


# Short task-based headings, not backend module names. All feature keys retain
# their original route, access checks, and persisted configuration.
FEATURE_GROUPS: dict[str, str] = {
    "action_reminder_center": "Your reminders",
    "action_my_preferences": "Your reminders",
    "action_notification_systems": "For server moderators",
    "action_builder": "For server moderators",
    "action_safety_schedule": "For server moderators",
    "community_pings_manager": "Roles & notifications",
    "community_tools": "Server tools",
    "share_router": "Share photos & videos",
    "share_downloads": "Share photos & videos",
    "toke_settings": "Events & meetups",
    "somewhere_420": "Events & meetups",
    "community_hub": "Events & meetups",
    "movie_night": "Events & meetups",
    "embed_studio": "Create messages",
    "server_design": "Server appearance",
    "card_assets": "Server appearance",
    "status": "Check bot health",
    "diagnostics": "Check bot health",
    "logs_activity": "Server logs & counters",
    "server_stats": "Server logs & counters",
}


def grouped_features(category: str) -> tuple[tuple[str, tuple[NavigationFeature, ...]], ...]:
    groups: dict[str, list[NavigationFeature]] = {}
    for feature in features_for_category(category):
        name = FEATURE_GROUPS.get(feature.key, "Tools & settings")
        groups.setdefault(name, []).append(feature)
    return tuple((name, tuple(features)) for name, features in groups.items())


def _normalized(value: str) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def category_by_key(key: str) -> NavigationCategory | None:
    return _CATEGORY_BY_KEY.get(str(key or "").strip())


def feature_by_key(key: str) -> NavigationFeature | None:
    return _FEATURE_BY_KEY.get(str(key or "").strip())


def features_for_category(category: str) -> tuple[NavigationFeature, ...]:
    wanted = str(category or "").strip()
    return tuple(item for item in FEATURES if item.category == wanted)


def search_features(query: str, *, limit: int = 25) -> list[NavigationFeature]:
    needle = _normalized(query)
    if not needle:
        return list(FEATURES[: max(1, min(int(limit or 25), 25))])

    terms = [part for part in needle.split(" ") if part]
    ranked: list[tuple[int, int, NavigationFeature]] = []
    for index, item in enumerate(FEATURES):
        label = _normalized(item.label)
        category = _normalized(_CATEGORY_BY_KEY[item.category].label)
        aliases = tuple(_normalized(alias) for alias in item.aliases)
        haystacks = (label, category, item.key.replace("_", " "), *aliases)

        score = 0
        if needle == label:
            score = 100
        elif needle in aliases:
            score = 95
        elif any(needle == value for value in haystacks):
            score = 90
        elif any(value.startswith(needle) for value in haystacks):
            score = 80
        elif any(needle in value for value in haystacks):
            score = 70
        elif all(any(term in value for value in haystacks) for term in terms):
            score = 60

        if score > 0:
            ranked.append((-score, index, item))

    ranked.sort(key=lambda value: (value[0], value[1]))
    safe_limit = max(1, min(int(limit or 25), 25))
    return [item for _score, _index, item in ranked[:safe_limit]]


def validate_registry() -> list[str]:
    errors: list[str] = []
    category_keys = [item.key for item in CATEGORIES]
    feature_keys = [item.key for item in FEATURES]

    if len(category_keys) != len(set(category_keys)):
        errors.append("duplicate navigation category key")
    if len(feature_keys) != len(set(feature_keys)):
        errors.append("duplicate navigation feature key")

    known_categories = set(category_keys)
    if len(RECENT_FEATURE_KEYS) != len(set(RECENT_FEATURE_KEYS)):
        errors.append("duplicate recent feature destination")
    for key in RECENT_FEATURE_KEYS:
        if key not in set(feature_keys):
            errors.append(f"recent destination not registered: {key}")
    for update in RECENT_UPDATES:
        if not update.headline.strip() or not update.detail.strip() or not update.merged_date:
            errors.append(f"recent update is incomplete: {update.key}")
    for key in FEATURE_GROUPS:
        if key not in set(feature_keys):
            errors.append(f"feature group references missing destination: {key}")
    for feature in FEATURES:
        if feature.category not in known_categories:
            errors.append(f"{feature.key}: unknown category {feature.category}")
        if not feature.label.strip():
            errors.append(f"{feature.key}: empty label")
        if not feature.description.strip():
            errors.append(f"{feature.key}: empty description")

    for category in CATEGORIES:
        if not features_for_category(category.key):
            errors.append(f"{category.key}: category has no features")

    return errors


REGISTRY_ERRORS = tuple(validate_registry())
if REGISTRY_ERRORS:
    raise RuntimeError("Invalid Dank Shield navigation registry: " + "; ".join(REGISTRY_ERRORS))


__all__ = [
    "CATEGORIES",
    "FEATURES",
    "NavigationCategory",
    "NavigationFeature",
    "REGISTRY_ERRORS",
    "RECENT_FEATURE_KEYS",
    "RECENT_UPDATES",
    "FEATURE_GROUPS",
    "RecentFeatureUpdate",
    "grouped_features",
    "recent_features",
    "category_by_key",
    "feature_by_key",
    "features_for_category",
    "search_features",
    "validate_registry",
]
