from __future__ import annotations

from typing import Any

_PATCHED = False


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace('/stoney setup', '/dank setup').replace('/stoney', '/dank').replace('Stoney Verify', 'Dank Shield').replace('StoneyVerify', 'DankShield')
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_clean(v) for v in value)
    return value


def install_dank_shield_branding_guard() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import public_ticket_panel_clean as panel
        original_health = getattr(panel, '_health_lines', None)
        if callable(original_health) and not getattr(original_health, '_dank_branding_wrapped', False):
            async def health_wrapper(*args: Any, **kwargs: Any):
                return _clean(await original_health(*args, **kwargs))
            setattr(health_wrapper, '_dank_branding_wrapped', True)
            setattr(panel, '_health_lines', health_wrapper)

        original_embed = getattr(panel, '_panel_embed', None)
        if callable(original_embed) and not getattr(original_embed, '_dank_branding_wrapped', False):
            def embed_wrapper(*args: Any, **kwargs: Any):
                embed = original_embed(*args, **kwargs)
                try:
                    footer = getattr(getattr(embed, 'footer', None), 'text', '') or ''
                    if footer:
                        embed.set_footer(text=_clean(footer))
                except Exception:
                    pass
                try:
                    if getattr(embed, 'title', None):
                        embed.title = _clean(embed.title)
                    if getattr(embed, 'description', None):
                        embed.description = _clean(embed.description)
                except Exception:
                    pass
                return embed
            setattr(embed_wrapper, '_dank_branding_wrapped', True)
            setattr(panel, '_panel_embed', embed_wrapper)
    except Exception:
        pass

    try:
        from stoney_verify.tickets_new import category_resolver
        original_hint = getattr(category_resolver, '_setup_hint', None)
        if callable(original_hint) and not getattr(original_hint, '_dank_branding_wrapped', False):
            def hint_wrapper(*args: Any, **kwargs: Any) -> str:
                return str(_clean(original_hint(*args, **kwargs)))
            setattr(hint_wrapper, '_dank_branding_wrapped', True)
            setattr(category_resolver, '_setup_hint', hint_wrapper)
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards import setup_category_modal_compat as compat
        original_reply = getattr(compat, '_reply', None)
        if callable(original_reply) and not getattr(original_reply, '_dank_branding_wrapped', False):
            async def reply_wrapper(interaction: Any, content: str) -> None:
                return await original_reply(interaction, _clean(content))
            setattr(reply_wrapper, '_dank_branding_wrapped', True)
            setattr(compat, '_reply', reply_wrapper)
    except Exception:
        pass

    _PATCHED = True
    try:
        print('🛡️ dank_shield_branding_guard active')
    except Exception:
        pass
    return True


install_dank_shield_branding_guard()

__all__ = ['install_dank_shield_branding_guard']
