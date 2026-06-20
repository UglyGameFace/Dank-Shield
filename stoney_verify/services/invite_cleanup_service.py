"""
Clean Invite Cleanup Service

This module contains reusable, clean logic for scanning and handling
Discord invites. The goal is to eventually centralize invite-related
cleanup behavior here instead of scattering it across startup guards.
"""

from __future__ import annotations

import re
from typing import Any

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def normalize_invite_code(code: str) -> str:
    """Normalize an invite code."""
    if not code:
        return ""
    code = code.strip().lower()
    code = code.replace("https://discord.gg/", "")
    code = code.replace("http://discord.gg/", "")
    code = code.replace("https://discord.com/invite/", "")
    code = code.replace("http://discord.com/invite/", "")
    return code.strip("/")


def extract_invite_codes(content: str) -> list[str]:
    """Extract invite codes from message content."""
    if not content:
        return []
    return list(dict.fromkeys([
        code.strip().lower()
        for code in INVITE_RE.findall(content)
        if code.strip()
    ]))


def has_invite(content: str) -> bool:
    """Check if content contains any Discord invite."""
    return bool(INVITE_RE.search(content or ""))


async def get_blocked_invite_codes(
    guild: Any,
    codes: list[str],
    *,
    allow_own: bool = True,
    override_own: bool = False,
) -> tuple[list[str], int]:
    """
    Determine which invite codes should be blocked.

    Returns:
        (blocked_codes, allowed_count)
    """
    from stoney_verify.startup_guards.invite_shield_sanitize_shared import (
        this_guild_invite_codes,
    )

    blocked: list[str] = []
    allowed_count = 0

    allowed_codes: set[str] = set()  # TODO: load from guild config later
    own_codes: set[str] = set()

    if allow_own and not override_own:
        try:
            own_codes = await this_guild_invite_codes(guild, codes)
        except Exception:
            own_codes = set()

    for code in sorted(codes):
        clean = normalize_invite_code(code)
        if not clean:
            continue
        if clean in allowed_codes or clean in own_codes:
            allowed_count += 1
            continue
        blocked.append(clean)

    return blocked, allowed_count
