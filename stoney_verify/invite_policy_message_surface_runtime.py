from __future__ import annotations

"""Prevent generated/app-response rich surfaces from becoming Invite Shield evidence.

Invite Shield must judge links the sender actually authored as ordinary message
content unless the rich surface itself is the moderation target.

Two Discord behaviors matter here:
- normal URLs can gain Discord-generated preview embeds whose remote metadata may
  advertise a Discord invite;
- slash/application commands can return utility embeds and buttons that include
  a support-server link even though the user invoked the app for a non-invite
  action such as downloading a video.

Human messages use message content only. Application interaction responses use
message content plus directly visible Components V2 text, while still ignoring
support-button destinations and rich embed metadata. Ordinary bot/webhook
messages may additionally use custom rich embeds/components because those
automated senders can author those surfaces directly. Generated link/article/
video previews remain ignored for every sender.
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


def _interaction_response(message: Any) -> bool:
    """Return True when Discord marks this message as an interaction response."""

    try:
        return getattr(message, "interaction_metadata", None) is not None
    except Exception:
        return False


def _content_text(message: Any) -> str:
    return policy.clean_invite_text(str(getattr(message, "content", "") or ""))


def _visible_component_text(component: Any) -> list[str]:
    """Collect only text Discord renders directly in Components V2."""

    parts: list[str] = []
    try:
        value = getattr(component, "content", None)
        if value:
            parts.append(str(value))
    except Exception:
        pass
    try:
        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            parts.extend(_visible_component_text(accessory))
    except Exception:
        pass
    try:
        for child in list(getattr(component, "children", []) or []):
            parts.extend(_visible_component_text(child))
    except Exception:
        pass
    return parts


def _interaction_authored_text(message: Any) -> str:
    """Read direct app-response text without treating support links as posts."""

    parts: list[str] = [str(getattr(message, "content", "") or "")]
    try:
        for row in list(getattr(message, "components", []) or []):
            parts.extend(_visible_component_text(row))
    except Exception:
        pass
    return "\n".join(policy.clean_invite_text(part) for part in parts if part)


def _rich_bot_text(message: Any) -> str:
    parts: list[str] = [str(getattr(message, "content", "") or "")]

    try:
        for embed in list(getattr(message, "embeds", []) or []):
            embed_type = str(getattr(embed, "type", "rich") or "rich").strip().lower()
            if embed_type != "rich":
                continue
            for attr in ("title", "description", "url"):
                value = getattr(embed, attr, None)
                if value:
                    parts.append(str(value))
            for field_obj in list(getattr(embed, "fields", []) or []):
                parts.append(str(getattr(field_obj, "name", "") or ""))
                parts.append(str(getattr(field_obj, "value", "") or ""))
            footer = getattr(embed, "footer", None)
            if getattr(footer, "text", None):
                parts.append(str(footer.text))
            author = getattr(embed, "author", None)
            if getattr(author, "name", None):
                parts.append(str(author.name))
            if getattr(author, "url", None):
                parts.append(str(author.url))
    except Exception:
        pass

    try:
        component_text = getattr(policy, "_component_text", None)
        if callable(component_text):
            for row in list(getattr(message, "components", []) or []):
                parts.extend(component_text(row))
    except Exception:
        pass

    return "\n".join(policy.clean_invite_text(part) for part in parts if part)


def install_invite_policy_message_surface_runtime() -> bool:
    """Install sender-authored invite extraction boundaries once."""

    if bool(getattr(policy, _INSTALL_FLAG, False)):
        return False

    original = getattr(policy, "message_text", None)
    if not callable(original):
        raise RuntimeError("invite policy message_text helper is unavailable")

    if not callable(getattr(policy, _ORIGINAL_ATTR, None)):
        setattr(policy, _ORIGINAL_ATTR, original)

    def message_text(message: Any) -> str:
        if _human_authored(message):
            return _content_text(message)
        if _interaction_response(message):
            return _interaction_authored_text(message)
        return _rich_bot_text(message)

    policy.message_text = message_text
    setattr(policy, _INSTALL_FLAG, True)
    print(
        "🛡️ Invite Shield message-surface guard active: "
        "human=content-only app-response=content+visible-v2 bot-webhook=content+custom-rich-v2"
    )
    return True


__all__ = ["install_invite_policy_message_surface_runtime"]
