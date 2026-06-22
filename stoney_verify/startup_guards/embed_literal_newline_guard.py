from __future__ import annotations

"""Prevent literal \\n, \\r\\n, and /n from appearing in Discord embeds."""

from typing import Any
import discord

_PATCHED = False

_ORIGINAL_EMBED_INIT: Any = None
_ORIGINAL_ADD_FIELD: Any = None
_ORIGINAL_INSERT_FIELD_AT: Any = None
_ORIGINAL_SET_FIELD_AT: Any = None
_ORIGINAL_SET_FOOTER: Any = None
_ORIGINAL_SET_AUTHOR: Any = None


def _clean_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\\n", "\n")
    text = text.replace("\\r", "\n")
    text = text.replace("/n", "\n")
    text = text.replace("/N", "\n")

    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    return text


def _clean_kwargs(kwargs: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in kwargs:
            kwargs[key] = _clean_text(kwargs[key])


def _embed_init(self: discord.Embed, *args: Any, **kwargs: Any) -> None:
    _clean_kwargs(kwargs, ("title", "description", "url"))
    _ORIGINAL_EMBED_INIT(self, *args, **kwargs)


def _add_field(self: discord.Embed, *args: Any, **kwargs: Any) -> discord.Embed:
    _clean_kwargs(kwargs, ("name", "value"))
    args = tuple(_clean_text(arg) if isinstance(arg, str) else arg for arg in args)
    return _ORIGINAL_ADD_FIELD(self, *args, **kwargs)


def _insert_field_at(self: discord.Embed, index: int, *args: Any, **kwargs: Any) -> discord.Embed:
    _clean_kwargs(kwargs, ("name", "value"))
    args = tuple(_clean_text(arg) if isinstance(arg, str) else arg for arg in args)
    return _ORIGINAL_INSERT_FIELD_AT(self, index, *args, **kwargs)


def _set_field_at(self: discord.Embed, index: int, *args: Any, **kwargs: Any) -> discord.Embed:
    _clean_kwargs(kwargs, ("name", "value"))
    args = tuple(_clean_text(arg) if isinstance(arg, str) else arg for arg in args)
    return _ORIGINAL_SET_FIELD_AT(self, index, *args, **kwargs)


def _set_footer(self: discord.Embed, *args: Any, **kwargs: Any) -> discord.Embed:
    _clean_kwargs(kwargs, ("text",))
    args = tuple(_clean_text(arg) if isinstance(arg, str) else arg for arg in args)
    return _ORIGINAL_SET_FOOTER(self, *args, **kwargs)


def _set_author(self: discord.Embed, *args: Any, **kwargs: Any) -> discord.Embed:
    _clean_kwargs(kwargs, ("name", "url"))
    args = tuple(_clean_text(arg) if isinstance(arg, str) else arg for arg in args)
    return _ORIGINAL_SET_AUTHOR(self, *args, **kwargs)


def apply() -> bool:
    global _PATCHED
    global _ORIGINAL_EMBED_INIT, _ORIGINAL_ADD_FIELD, _ORIGINAL_INSERT_FIELD_AT
    global _ORIGINAL_SET_FIELD_AT, _ORIGINAL_SET_FOOTER, _ORIGINAL_SET_AUTHOR

    if _PATCHED:
        return True

    try:
        _ORIGINAL_EMBED_INIT = discord.Embed.__init__
        _ORIGINAL_ADD_FIELD = discord.Embed.add_field
        _ORIGINAL_INSERT_FIELD_AT = discord.Embed.insert_field_at
        _ORIGINAL_SET_FIELD_AT = discord.Embed.set_field_at
        _ORIGINAL_SET_FOOTER = discord.Embed.set_footer
        _ORIGINAL_SET_AUTHOR = discord.Embed.set_author

        discord.Embed.__init__ = _embed_init
        discord.Embed.add_field = _add_field
        discord.Embed.insert_field_at = _insert_field_at
        discord.Embed.set_field_at = _set_field_at
        discord.Embed.set_footer = _set_footer
        discord.Embed.set_author = _set_author

        _PATCHED = True
        print("🧼 embed_literal_newline_guard active; literal newline markers are sanitized before embeds render")
        return True
    except Exception as exc:
        print(f"⚠️ embed_literal_newline_guard failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply", "_clean_text"]
