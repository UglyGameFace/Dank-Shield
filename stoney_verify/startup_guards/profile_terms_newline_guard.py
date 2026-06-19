from __future__ import annotations

"""Remove literal slash-n markers from profile help embeds."""

from typing import Any, Callable

_PATCHED = False


def _clean_text(value: Any) -> str:
    try:
        text = str(value or "")
    except Exception:
        return ""
    for bad in ("\\\\n", "\\\\N", "\\n", "\\N", "/n", "/N"):
        text = text.replace(bad, "\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _clean_embed(embed: Any) -> Any:
    try:
        if getattr(embed, "title", None):
            embed.title = _clean_text(embed.title)
        if getattr(embed, "description", None):
            embed.description = _clean_text(embed.description)
        for index, field in enumerate(list(getattr(embed, "fields", []) or [])):
            embed.set_field_at(
                index,
                name=_clean_text(getattr(field, "name", "")),
                value=_clean_text(getattr(field, "value", "")),
                inline=bool(getattr(field, "inline", False)),
            )
        footer_text = _clean_text(getattr(getattr(embed, "footer", None), "text", ""))
        if footer_text:
            embed.set_footer(text=footer_text)
    except Exception:
        pass
    return embed


def _wrap_embed_factory(module: Any, name: str) -> None:
    original = getattr(module, name, None)
    if not callable(original) or getattr(original, "_DANK_NEWLINE_CLEANED", False):
        return

    def _cleaned(*args: Any, **kwargs: Any) -> Any:
        return _clean_embed(original(*args, **kwargs))

    _cleaned._DANK_NEWLINE_CLEANED = True  # type: ignore[attr-defined]
    setattr(module, name, _cleaned)


def apply() -> bool:
    global _PATCHED
    try:
        from stoney_verify.commands_ext import public_self_roles_group as profile

        _wrap_embed_factory(profile, "_profile_terms_embed")
        _wrap_embed_factory(profile, "_profile_panel_embed")
        _PATCHED = True
        print("✅ profile_terms_newline_guard active; profile help embeds render real new lines")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ profile_terms_newline_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
