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
    home_label: str = ""


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
        "Initial setup, server mappings, permissions, and configuration.",
        home_label="Setup",
    ),
    NavigationCategory(
        "access",
        "Onboarding & Access",
        "🚪",
        "Verification, Member Setup, welcome flows, and access requirements.",
        home_label="Access",
    ),
    NavigationCategory(
        "safety",
        "Safety & Moderation",
        "🛡️",
        "Protection, moderation, cleanup, spam, invites, and member safety.",
        home_label="Safety",
    ),
    NavigationCategory(
        "people",
        "Members, Roles & Profiles",
        "👥",
        "Server roles, profile configuration, and staff member-management tools.",
        home_label="Members",
    ),
    NavigationCategory(
        "community",
        "Community & Engagement",
        "🌿",
        "Community roles, pings, sharing, Hub features, stickies, polls, and utilities.",
        home_label="Community",
    ),
    NavigationCategory(
        "tickets",
        "Tickets & Support",
        "🎫",
        "Ticket queues, panels, categories, routing, forms, and support workflows.",
        home_label="Tickets",
    ),
    NavigationCategory(
        "design",
        "Design & Branding",
        "🎨",
        "Server naming/design, card appearance, artwork, fonts, and visual settings.",
        home_label="Design",
    ),
    NavigationCategory(
        "voice",
        "Voice & Accessibility",
        "🔊",
        "Live Captions and voice accessibility controls.",
        home_label="Voice",
    ),
    NavigationCategory(
        "ops",
        "Logs, Stats & Diagnostics",
        "📊",
        "Logs, activity, live counters, health, status, and diagnostics.",
        home_label="Operations",
    ),
    NavigationCategory(
        "my",
        "My Dank Shield",
        "👤",
        "Your profile, Member Setup, optional tags, and personal community choices.",
        home_label="My Account",
    ),
    NavigationCategory(
        "utilities",
        "Utilities & Help",
        "🧰",
        "Help and cross-feature guidance.",
        home_label="Utilities",
    ),
)


FEATURES: tuple[NavigationFeature, ...] = (
    NavigationFeature(
        "setup_center",
        "Setup & Settings",
        "⚙️",
        "setup",
        "Configure the server and map the roles/channels Dank Shield uses.",
        ("setup", "configure server", "server settings", "fix access", "permissions", "roles channels"),
        True,
    ),
    NavigationFeature(
        "verification",
        "Verification",
        "✅",
        "access",
        "Manage verification status, panels, repair, and role mappings.",
        ("verify", "unverified", "verified", "verification setup", "role mapping", "verify panel"),
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
        "Manage Spam Guard, Invite Shield, AntiNuke, automod, filters, and protection policy.",
        ("spam guard", "spamguard", "invite shield", "antinuke", "anti nuke", "automod", "link shield", "filters"),
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
        "Open the canonical role and profile administration center.",
        ("roles", "role editor", "create role", "role health", "profiles", "server role editor"),
        True,
    ),
    NavigationFeature(
        "profile_builder",
        "Profile Builder",
        "🌿",
        "people",
        "Configure the server profile panel, tags, cosmetics, and related member options.",
        ("profile builder", "profile panel", "profile roles", "cosmetic roles", "pronouns", "interests"),
        True,
    ),
    NavigationFeature(
        "community_pings_manager",
        "Community & Pings Manager",
        "🌿",
        "community",
        "Configure the server's member-selectable community and notification roles.",
        ("community pings", "ping roles", "notification roles", "stoner", "sesh pings", "toke setup"),
        True,
    ),
    NavigationFeature(
        "community_tools",
        "Community Tools",
        "🧰",
        "community",
        "Open stickies, polls, embeds, lookups, permission checks, and community utilities.",
        (
            "sticky",
            "stickies",
            "poll",
            "embed builder",
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
        "Configure mobile share-sheet proxy channels and their real destinations.",
        ("share", "share route", "share router", "memes", "share sheet", "proxy channel"),
        True,
    ),
    NavigationFeature(
        "community_hub",
        "Community Hub",
        "🎮",
        "community",
        "Open cross-server community, LFG, party, and Hub controls.",
        ("hub", "lfg", "party", "cross server", "community hub"),
    ),
    NavigationFeature(
        "tickets",
        "Tickets",
        "🎫",
        "tickets",
        "Manage ticket queues, panels, categories, forms, routing, and support settings.",
        ("ticket", "tickets", "support", "ticket panel", "ticket category", "intake", "routing", "forms"),
        True,
    ),
    NavigationFeature(
        "server_design",
        "Server Design",
        "🎨",
        "design",
        "Manage server naming, styles, Search-Safe naming, and design rules.",
        ("design", "server design", "search safe", "search-safe", "channel fonts", "channel names", "category names"),
        True,
    ),
    NavigationFeature(
        "card_assets",
        "Card Assets",
        "📎",
        "design",
        "Manage Join/Exit artwork and uploaded card fonts.",
        ("card assets", "background", "join background", "exit background", "font", "custom font"),
        True,
    ),
    NavigationFeature(
        "live_captions",
        "Live Captions",
        "📝",
        "voice",
        "Configure ordinary-server live voice captions and accessibility output.",
        ("captions", "live captions", "voice captions", "transcription", "transcribe"),
    ),
    NavigationFeature(
        "logs_activity",
        "Logs & Activity",
        "🧾",
        "ops",
        "Configure logs, activity tracking, and related operational visibility.",
        ("logs", "modlog", "activity", "member logs", "audit log"),
        True,
    ),
    NavigationFeature(
        "server_stats",
        "Server Stats",
        "📊",
        "ops",
        "Manage live server counter channels, labels, layout, and refresh behavior.",
        ("stats", "server stats", "counters", "member count", "ticket count"),
        True,
    ),
    NavigationFeature(
        "status",
        "Status",
        "📡",
        "ops",
        "View Dank Shield runtime and service status.",
        ("status", "bot status", "online", "uptime"),
    ),
    NavigationFeature(
        "diagnostics",
        "Diagnostics",
        "🩺",
        "ops",
        "Open read-only startup, configuration, and health diagnostics.",
        ("diagnostics", "health", "errors", "troubleshoot", "startup"),
        True,
    ),
    NavigationFeature(
        "my_profile",
        "My Profile",
        "🪪",
        "my",
        "Open your Dank Shield profile, signature, privacy, platforms, and appearance.",
        ("my profile", "profile", "signature", "privacy", "platforms", "appearance"),
    ),
    NavigationFeature(
        "my_member_setup",
        "My Member Setup",
        "🧭",
        "my",
        "Review or complete the current Member Setup requirements for your account.",
        ("my setup", "member setup", "review setup", "account setup", "access setup"),
    ),
    NavigationFeature(
        "profile_tags",
        "Profile Tags & Cosmetics",
        "🎭",
        "my",
        "Choose optional profile tags and cosmetic roles configured by this server.",
        ("profile tags", "cosmetics", "pronouns", "identity", "interests"),
    ),
    NavigationFeature(
        "my_community_pings",
        "My Community & Pings",
        "🌿",
        "my",
        "Choose your optional community and notification roles.",
        ("my pings", "notifications", "community roles", "ping preferences", "sesh pings"),
    ),
    NavigationFeature(
        "help",
        "Help",
        "❓",
        "utilities",
        "See the compact command guide and where major Dank Shield features live.",
        ("help", "commands", "where is", "how do i", "find feature"),
    ),
)


_CATEGORY_BY_KEY = {item.key: item for item in CATEGORIES}
_FEATURE_BY_KEY = {item.key: item for item in FEATURES}


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
    "category_by_key",
    "feature_by_key",
    "features_for_category",
    "search_features",
    "validate_registry",
]
