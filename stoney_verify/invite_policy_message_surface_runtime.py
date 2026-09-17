from __future__ import annotations

"""Prevent Discord-generated link previews from becoming Invite Shield evidence.

Human users cannot author Discord rich embeds/components directly. When they post a
normal URL, Discord may add an unfurled preview whose remote metadata contains a
Discord invite. Invite Shield must judge what the human actually sent, not what a
remote page happened to advertise inside the generated preview.

Bot/webhook-authored messages keep the existing rich-message scan because those
senders can genuinely author embeds and components containing invite links.
"""

from typing import Any

from . import invite_policy_engine as policy

_INSTALL_FLAG = "_dank_invite_policy_message_surface_runtime_installed"
_ORIGINAL_ATTR = "_dank_original_invite_policy_message_text"


def _human_authored(message: Any) -> bool:
    author = getattr(message, "author", None)
    if author is None:
        return True
    return not bool(getattr(author, "bot", False))


def install_invite_policy_message_surface_runtime() -> bool:
    """Install the human-content-only invite extraction boundary once."""

    if bool(getattr(policy, _INSTALL_FLAG, False)):
        return False

    original = getattr(policy, "message_text", None)
    if not callable(original):
        raise RuntimeError("invite policy message_text helper is unavailable")

    if not callable(getattr(policy, _ORIGINAL_ATTR, None)):
        setattr(policy, _ORIGINAL_ATTR, original)

    def message_text(message: Any) -> str:
        if _human_authored(message):
            return policy.clean_invite_text(
                str(getattr(message, "content", "") or "")
            )
        return original(message)

    policy.message_text = message_text
    setattr(policy, _INSTALL_FLAG, True)
    print(
        "🛡️ Invite Shield message-surface guard active: "
        "human=content-only bot/webhook=rich-message"
    )
    return True


__all__ = ["install_invite_policy_message_surface_runtime"]
