from __future__ import annotations

"""Compatibility facade for owner-facing public setup config writes.

Guild configuration persistence is owned by :mod:`stoney_verify.guild_config`.
Public setup keeps this stable import surface because many setup modules already
use it, but it no longer implements a second Supabase writer or its own safety
policy.
"""

from typing import Any, Iterable, Mapping

from .. import guild_config as _guild_config


def _setup_patch(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the setup write contract without owning persistence."""
    patch = dict(updates)
    patch.setdefault("__config_write_mode", "setup_builder")
    patch.setdefault("__config_write_source", "public_setup_config_writer")
    patch.setdefault("__config_write_invalidate_completion", True)
    return patch


def upsert_guild_config_sync(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    saved = _guild_config.upsert_guild_config_sync(int(guild_id), _setup_patch(updates))
    return dict(saved)


async def upsert_guild_config(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    saved = await _guild_config.upsert_guild_config(int(guild_id), _setup_patch(updates))
    return dict(saved)


def clear_guild_config_keys_sync(
    guild_id: int,
    keys: Iterable[str],
    *,
    source: str = "/dank setup resource reconciliation",
    actor: Any = None,
) -> dict[str, Any]:
    saved = _guild_config.clear_guild_config_keys_sync(
        int(guild_id),
        tuple(keys),
        source=source,
        actor=actor,
    )
    return dict(saved)


async def clear_guild_config_keys(
    guild_id: int,
    keys: Iterable[str],
    *,
    source: str = "/dank setup resource reconciliation",
    actor: Any = None,
) -> dict[str, Any]:
    saved = await _guild_config.clear_guild_config_keys(
        int(guild_id),
        tuple(keys),
        source=source,
        actor=actor,
    )
    return dict(saved)


def apply_public_setup_writer_patch() -> bool:
    """Keep legacy setup callbacks pointed at the canonical writer facade.

    ``public_setup_group`` still carries its historical local writer as a
    compatibility fallback. The public command profile imports this facade early
    through onboarding, so binding the aliases here makes the canonical writer
    authoritative before setup commands can be used. Repeated calls are safe.
    """
    try:
        from . import public_setup_group as group

        group._upsert_config_sync = upsert_guild_config_sync  # type: ignore[attr-defined]
        group._upsert_config = upsert_guild_config  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


# Install the compatibility aliases as soon as any public setup/config consumer
# imports this facade. This removes registration-order dependence while the large
# setup group retains its dormant fallback implementation for compatibility.
apply_public_setup_writer_patch()


__all__ = [
    "upsert_guild_config_sync",
    "upsert_guild_config",
    "clear_guild_config_keys_sync",
    "clear_guild_config_keys",
    "apply_public_setup_writer_patch",
]
