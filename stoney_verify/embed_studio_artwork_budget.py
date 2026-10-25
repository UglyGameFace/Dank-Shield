"""Explicit artwork budgets and non-destructive storage usage accounting.

Physical Supabase Storage bytes, not the 50-link UI cap, determine usage.
No silent asset deletion. Missing budget configuration stops managed uploads.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any, Mapping

from .embed_studio_core import StudioValidationError
from .embed_studio_storage_v2 import verify_project

MIB = 1024 * 1024
GUILD_BUDGET_ENV = "DANK_STUDIO_ARTWORK_GUILD_MIB"
GLOBAL_BUDGET_ENV = "DANK_STUDIO_ARTWORK_GLOBAL_MIB"
RESERVE_RPC = "dank_embed_studio_asset_reserve"
FINALIZE_RPC = "dank_embed_studio_asset_finalize"
RELEASE_RPC = "dank_embed_studio_asset_release"
ASSET_TABLE = "dank_embed_studio_assets"
HOLD_TABLE = "dank_embed_studio_asset_holds"


class ArtworkBudgetError(StudioValidationError):
    pass


@dataclass(frozen=True)
class ArtworkBudget:
    guild_bytes: int
    global_bytes: int


def get_budget(environ: Mapping[str, str] | None = None) -> ArtworkBudget:
    env = os.environ if environ is None else environ
    try:
        guild = int(str(env.get(GUILD_BUDGET_ENV) or "0").strip())
        global_ = int(str(env.get(GLOBAL_BUDGET_ENV) or "0").strip())
    except ValueError as exc:
        raise ArtworkBudgetError("Artwork capacity is not configured correctly.") from exc
    if (guild < 6 or global_ < guild or global_ > 1048576):
        raise ArtworkBudgetError(
            "Managed artwork capacity is not configured. Set valid per-server "
            "and total MiB budgets before enabling uploads."
        )
    return ArtworkBudget(guild * MIB, global_ * MIB)


def _client() -> Any:
    verify_project()
    from .globals import get_supabase
    client = get_supabase()
    if client is None:
        raise ArtworkBudgetError("Artwork capacity database is unavailable.")
    return client


def _rpc_result(db: Any, name: str, params: dict[str, Any], key: str) -> dict[str, Any]:
    result = db.rpc(name, params).execute()
    data = getattr(result, "data", None)
    if not isinstance(data, Mapping) or type(data.get(key)) is not bool:
        raise ArtworkBudgetError("Artwork quota transaction was not confirmed.")
    return dict(data)


def reserve_artwork(
    db: Any, *, guild_id: int, object_path: str, size_bytes: int, budget: ArtworkBudget,
) -> None:
    result = _rpc_result(db, RESERVE_RPC, {
        "p_guild_id": str(guild_id), "p_path": object_path,
        "p_bytes": size_bytes, "p_guild_budget": budget.guild_bytes,
        "p_global_budget": budget.global_bytes,
    }, "reserved")
    if not result["reserved"]:
        raise ArtworkBudgetError(
            "Artwork upload limit reached or reservation already exists. "
            "Review stored artwork usage or increase the verified storage allocation."
        )


def finalize_artwork(
    db: Any, *, guild_id: int, object_path: str, size_bytes: int,
    name: str, content_type: str, digest: str,
) -> None:
    result = _rpc_result(db, FINALIZE_RPC, {
        "p_guild_id": str(guild_id), "p_path": object_path,
        "p_bytes": size_bytes, "p_name": name,
        "p_content_type": content_type, "p_sha256": digest,
    }, "finalized")
    if not result["finalized"]:
        raise ArtworkBudgetError("Artwork metadata could not be finalized. No saved link was created.")


def release_artwork(db: Any, guild_id: int, object_path: str) -> None:
    result = db.rpc(RELEASE_RPC, {
        "p_guild_id": str(guild_id), "p_path": object_path,
    }).execute()
    if type(getattr(result, "data", None)) is not bool:
        raise ArtworkBudgetError("Artwork reservation release was not confirmed.")


USAGE_RPC = "dank_embed_studio_asset_usage"


def _read_usage_sync(guild_id: int) -> dict[str, int]:
    if int(guild_id) <= 0:
        raise ArtworkBudgetError("Artwork usage requires a real Discord server.")
    result = _client().rpc(USAGE_RPC, {"p_guild_id": str(guild_id)}).execute()
    data = getattr(result, "data", None)
    expected = ("guild_used", "global_used", "asset_count",
                "reservation_count", "stale_reservations")
    if not isinstance(data, Mapping):
        raise ArtworkBudgetError("Artwork usage was not confirmed by the database.")
    if any(type(data.get(k)) is not int or data[k] < 0 for k in expected):
        raise ArtworkBudgetError("Artwork usage returned malformed values.")
    return {key: data[key] for key in expected}


async def read_artwork_usage(guild_id: int) -> dict[str, int]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_read_usage_sync, guild_id), timeout=12)
    except (ArtworkBudgetError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise ArtworkBudgetError("Artwork inventory is temporarily unavailable.") from exc
