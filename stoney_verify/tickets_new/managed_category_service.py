from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from ..globals import get_supabase


class ManagedCategorySyncError(RuntimeError):
    """Raised when the managed ticket-category catalog cannot be reconciled."""


def _sync_managed_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    try:
        supabase = get_supabase()
    except Exception as exc:  # pragma: no cover - defensive runtime boundary
        raise ManagedCategorySyncError("Supabase is unavailable.") from exc

    if not supabase:
        raise ManagedCategorySyncError("Supabase is unavailable.")

    try:
        response = supabase.rpc(
            "reconcile_dank_ticket_categories",
            {"p_guild_id": str(int(guild_id))},
        ).execute()
    except Exception as exc:
        raise ManagedCategorySyncError(
            f"Managed category reconciliation failed for guild {int(guild_id)}."
        ) from exc

    rows = getattr(response, "data", None) or []
    return [dict(row) for row in rows if isinstance(row, dict)]


async def sync_managed_categories(guild_id: int) -> List[Dict[str, Any]]:
    """Reconcile one guild against the current global Dank Shield catalog."""

    return await asyncio.to_thread(_sync_managed_categories_sync, int(guild_id))


def summarize_sync(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    inserted = 0
    updated = 0
    deleted_duplicates = 0

    for row in rows:
        try:
            inserted += int(row.get("inserted_count") or 0)
        except Exception:
            pass
        try:
            updated += int(row.get("updated_count") or 0)
        except Exception:
            pass
        try:
            deleted_duplicates += int(row.get("deleted_duplicate_count") or 0)
        except Exception:
            pass

    return {
        "inserted": inserted,
        "updated": updated,
        "deleted_duplicates": deleted_duplicates,
    }


__all__ = [
    "ManagedCategorySyncError",
    "summarize_sync",
    "sync_managed_categories",
]
