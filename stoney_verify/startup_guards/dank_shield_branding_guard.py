from __future__ import annotations

"""Normalize remaining public-facing Stoney Verify branding to Dank Shield.

This guard is intentionally text-only. It does not rename the Python package,
Supabase tables, legacy env vars, or internal module names because that would be
a risky product rename. It only cleans content that users/staff can see.

Important: the bot name shown beside Discord messages is controlled by the
Discord application/bot profile, not by these embed/text patches.
"""

from typing import Any, Iterable

_PATCHED = False

_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("/stoney setup", "/dank setup"),
    ("/stoney", "/dank"),
    ("Stoney Verify", "Dank Shield"),
    ("StoneyVerify", "DankShield"),
    ("Stoney setup", "Dank Shield setup"),
    ("Stoney is", "Dank Shield is"),
    ("Stoney lacks", "Dank Shield lacks"),
    ("Stoney ticket", "Dank Shield ticket"),
    ("Stoney panel", "Dank Shield panel"),
    ("Stoney", "Dank Shield"),
)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for old, new in _REPLACEMENTS:
            out = out.replace(old, new)
        return out
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_clean(v) for v in value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def _clean_embed(embed: Any) -> Any:
    try:
        if getattr(embed, "title", None):
            embed.title = _clean(embed.title)
        if getattr(embed, "description", None):
            embed.description = _clean(embed.description)
    except Exception:
        pass

    try:
        footer = getattr(getattr(embed, "footer", None), "text", "") or ""
        if footer:
            embed.set_footer(text=_clean(footer))
    except Exception:
        pass

    try:
        author = getattr(getattr(embed, "author", None), "name", "") or ""
        if author:
            embed.set_author(name=_clean(author))
    except Exception:
        pass

    try:
        for field in list(getattr(embed, "fields", []) or []):
            name = _clean(getattr(field, "name", "") or "")
            value = _clean(getattr(field, "value", "") or "")
            inline = bool(getattr(field, "inline", False))
            idx = list(getattr(embed, "fields", []) or []).index(field)
            embed.set_field_at(idx, name=name, value=value, inline=inline)
    except Exception:
        pass

    return embed


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    for key in ("content",):
        if key in out:
            out[key] = _clean(out[key])
    if "embed" in out and out["embed"] is not None:
        out["embed"] = _clean_embed(out["embed"])
    if "embeds" in out and isinstance(out["embeds"], list):
        out["embeds"] = [_clean_embed(e) for e in out["embeds"]]
    return out


def _wrap_async_reply_function(obj: Any, attr_name: str, *, content_arg_indexes: Iterable[int] = (1,)) -> bool:
    original = getattr(obj, attr_name, None)
    if not callable(original) or getattr(original, "_dank_branding_wrapped", False):
        return False

    async def wrapper(*args: Any, **kwargs: Any):
        args_list = list(args)
        for idx in content_arg_indexes:
            try:
                if len(args_list) > idx:
                    args_list[idx] = _clean(args_list[idx])
            except Exception:
                pass
        kwargs = _clean_payload(kwargs)
        return await original(*args_list, **kwargs)

    setattr(wrapper, "_dank_branding_wrapped", True)
    setattr(obj, attr_name, wrapper)
    return True


def install_dank_shield_branding_guard() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import public_ticket_panel_clean as panel

        original_health = getattr(panel, "_health_lines", None)
        if callable(original_health) and not getattr(original_health, "_dank_branding_wrapped", False):
            async def health_wrapper(*args: Any, **kwargs: Any):
                return _clean(await original_health(*args, **kwargs))
            setattr(health_wrapper, "_dank_branding_wrapped", True)
            setattr(panel, "_health_lines", health_wrapper)

        original_embed = getattr(panel, "_panel_embed", None)
        if callable(original_embed) and not getattr(original_embed, "_dank_branding_wrapped", False):
            def embed_wrapper(*args: Any, **kwargs: Any):
                return _clean_embed(original_embed(*args, **kwargs))
            setattr(embed_wrapper, "_dank_branding_wrapped", True)
            setattr(panel, "_panel_embed", embed_wrapper)

        _wrap_async_reply_function(panel, "_ephemeral", content_arg_indexes=(1,))
        _wrap_async_reply_function(panel, "_edit_or_reply", content_arg_indexes=())
    except Exception:
        pass

    try:
        from stoney_verify.tickets_new import category_resolver
        original_hint = getattr(category_resolver, "_setup_hint", None)
        if callable(original_hint) and not getattr(original_hint, "_dank_branding_wrapped", False):
            def hint_wrapper(*args: Any, **kwargs: Any) -> str:
                return str(_clean(original_hint(*args, **kwargs)))
            setattr(hint_wrapper, "_dank_branding_wrapped", True)
            setattr(category_resolver, "_setup_hint", hint_wrapper)
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards import setup_category_modal_compat as compat
        _wrap_async_reply_function(compat, "_reply", content_arg_indexes=(1,))
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards import unverified_ticket_panel_flow as verify_flow
        _wrap_async_reply_function(verify_flow, "_reply", content_arg_indexes=(1,))

        original_intro = getattr(verify_flow, "_send_direct_ticket_intro", None)
        if callable(original_intro) and not getattr(original_intro, "_dank_branding_wrapped", False):
            async def intro_wrapper(*args: Any, **kwargs: Any):
                return await original_intro(*args, **kwargs)
            setattr(intro_wrapper, "_dank_branding_wrapped", True)
            setattr(verify_flow, "_send_direct_ticket_intro", intro_wrapper)
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards import legacy_public_ticket_panel_disable as legacy_panel
        _wrap_async_reply_function(legacy_panel, "_reply_legacy_disabled", content_arg_indexes=())
    except Exception:
        pass

    _PATCHED = True
    try:
        print("🛡️ dank_shield_branding_guard active")
        print("ℹ️ Discord message author/app name is controlled by the Discord bot profile, not code embeds.")
    except Exception:
        pass
    return True


install_dank_shield_branding_guard()

__all__ = ["install_dank_shield_branding_guard"]
