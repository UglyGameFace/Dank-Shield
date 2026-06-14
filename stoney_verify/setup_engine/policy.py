from __future__ import annotations

from typing import Any
import re
import unicodedata

import discord

from .models import SetupConfigSnapshot
from .scanner import all_channel_targets, get_channel, parent_id, target_id, target_name

_SEPARATOR_RE = re.compile(r"[\s_\-–—|｜┃:：/\\]+")


def norm_name(value: Any) -> str:
    try:
        # Discord servers often use fancy Unicode channel names. NFKC folds names
        # like 𝔯𝔲𝔩𝔢𝔰 back to rules so the safety engine does not mistake a
        # deliberate public rules channel for an Unverified leak.
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        text = _SEPARATOR_RE.sub("-", text)
        return text.strip("-")
    except Exception:
        return ""


def target_norm_name(target: Any) -> str:
    return norm_name(target_name(target))


def looks_onboarding_public(target: Any) -> bool:
    name = target_norm_name(target)
    if not name:
        return False
    public_tokens = (
        "welcome",
        "rule",
        "rules",
        "verify",
        "verification",
        "support",
        "ticket-panel",
        "start",
        "onboard",
        "onboarding",
        "central-command",
        "command",
    )
    private_tokens = (
        "staff",
        "mod",
        "admin",
        "transcript",
        "log",
        "archive",
        "ticket-0",
        "active-ticket",
    )
    return any(token in name for token in public_tokens) and not any(token in name for token in private_tokens)


def looks_member_space(target: Any) -> bool:
    name = target_norm_name(target)
    member_tokens = ("general", "lounge", "chat", "main-lobby", "memes", "munchies", "gaming", "lfg", "clips")
    return any(token in name for token in member_tokens)


def saved_public_ids(config: SetupConfigSnapshot) -> set[int]:
    ids = set(config.saved_onboarding_channel_ids)
    if config.onboarding_category_id > 0:
        ids.add(config.onboarding_category_id)
    return {x for x in ids if x > 0}


def saved_private_ids(config: SetupConfigSnapshot) -> set[int]:
    return set(config.saved_private_channel_ids)


def allowed_public_ids(config: SetupConfigSnapshot, guild: discord.Guild) -> set[int]:
    allowed = saved_public_ids(config)
    private = saved_private_ids(config)

    for cid in list(allowed):
        channel = get_channel(guild, cid)
        pid = parent_id(channel)
        if pid > 0 and pid not in private:
            allowed.add(pid)

    for target in all_channel_targets(guild):
        tid = target_id(target)
        if tid <= 0 or tid in private:
            continue
        pid = parent_id(target)
        if pid in private:
            continue
        if looks_onboarding_public(target):
            allowed.add(tid)
            if pid > 0:
                allowed.add(pid)
    return allowed


def private_or_staff_ids(config: SetupConfigSnapshot, guild: discord.Guild) -> set[int]:
    private = saved_private_ids(config)
    for target in all_channel_targets(guild):
        name = target_norm_name(target)
        if any(token in name for token in ("staff", "mod", "admin", "transcript", "mod-log", "join-leave-log", "bot-status", "archive")):
            private.add(target_id(target))
    return {x for x in private if x > 0}
