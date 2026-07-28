from __future__ import annotations

"""Keep platform username copy responses free of Markdown wrappers.

Discord bots cannot write directly to a member's device clipboard. The fastest
safe interaction is therefore a private response containing only the current
public username. Older profile code used a fenced ``text`` block, which caused
mobile clients to copy the backticks and language marker too. This guard wraps
the canonical private sender before the public profile module captures it.
"""

import re
from typing import Any

_PATCHED = False
_ORIGINAL_SEND_PRIVATE = None
_FENCED_TEXT_RE = re.compile(r"\A```text\n(?P<value>[\s\S]*?)\n```\Z")


def plain_copy_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _FENCED_TEXT_RE.fullmatch(value)
    if match is None:
        return value
    return match.group("value")


def apply() -> bool:
    global _PATCHED, _ORIGINAL_SEND_PRIVATE
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_profile_cards_core as core

        original = getattr(core, "_send_private", None)
        if not callable(original):
            return False
        _ORIGINAL_SEND_PRIVATE = original

        async def _send_private_plain_copy(interaction: Any, **kwargs: Any) -> None:
            if "content" in kwargs:
                kwargs["content"] = plain_copy_content(kwargs.get("content"))
            await original(interaction, **kwargs)

        core._send_private = _send_private_plain_copy
        _PATCHED = True
        return True
    except Exception as exc:
        print(f"⚠️ profile_username_copy_guard failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply", "plain_copy_content"]
