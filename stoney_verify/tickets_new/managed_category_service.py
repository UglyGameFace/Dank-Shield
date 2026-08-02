from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from ..globals import get_supabase


CATEGORY_SETUP_VERSION = 2
SAFE_STARTER_KEYS: tuple[str, ...] = ("report", "appeal", "support")

# One catalog owns database reconciliation, setup selection, and every live
# ticket picker. Rows stay available globally, but only rows with is_enabled=true
# are shown to members.
CATEGORY_CATALOG: tuple[Dict[str, Any], ...] = (
    {"category_key": "verification", "slug": "verification_issue", "name": "Verification", "description": "Help with verification or approval issues.", "intake_type": "verification", "sort_order": 10, "is_default": False},
    {"category_key": "account-access", "slug": "account_access", "name": "Account / Access", "description": "Account access, login, hacked account, email, password, and 2FA issues.", "intake_type": "account", "sort_order": 20, "is_default": False},
    {"category_key": "payments-refunds", "slug": "payments_refunds", "name": "Payments / Refunds", "description": "Payments, orders, receipts, invoices, refunds, and chargebacks.", "intake_type": "purchase", "sort_order": 30, "is_default": False},
    {"category_key": "appeal", "slug": "appeal", "name": "Appeal", "description": "Appeal a moderation action or access restriction.", "intake_type": "appeal", "sort_order": 40, "is_default": False},
    {"category_key": "report", "slug": "report", "name": "Report a Member", "description": "Report a member or server issue.", "intake_type": "report", "sort_order": 50, "is_default": False},
    {"category_key": "staff-complaint", "slug": "staff_complaint", "name": "Staff Complaint", "description": "Complaints or escalation requests involving staff or moderator behavior.", "intake_type": "report", "sort_order": 60, "is_default": False},
    {"category_key": "bug", "slug": "technical_support", "name": "Bug / Technical Support", "description": "Site bugs, panel problems, bot issues, broken flows, and technical failures.", "intake_type": "bug", "sort_order": 70, "is_default": False},
    {"category_key": "cod-services", "slug": "cod_services", "name": "COD Services", "description": "Call of Duty, Warzone, Zombies, lobby, account, unlock, or service questions.", "intake_type": "cod_services", "sort_order": 80, "is_default": False},
    {"category_key": "game-services", "slug": "game_services", "name": "Game Services", "description": "Route game-related service questions to the right staff.", "intake_type": "game_services", "sort_order": 90, "is_default": False},
    {"category_key": "service-request", "slug": "service_request", "name": "Service Requests", "description": "General service requests, carries, boosts, recoveries, and fulfillment questions.", "intake_type": "custom", "sort_order": 100, "is_default": False},
    {"category_key": "vouch-referral", "slug": "vouch_referral", "name": "Vouch / Invite / Referral", "description": "Invite credit, referral rewards, vouch issues, and who-invited-who questions.", "intake_type": "custom", "sort_order": 110, "is_default": False},
    {"category_key": "giveaway-reward", "slug": "giveaway_reward", "name": "Giveaway / Reward Issues", "description": "Giveaway prizes, missing rewards, winner disputes, and reward claims.", "intake_type": "custom", "sort_order": 120, "is_default": False},
    {"category_key": "content-media", "slug": "content_media", "name": "Content / Media Requests", "description": "Graphics, thumbnails, banners, content requests, media edits, and promo assets.", "intake_type": "custom", "sort_order": 130, "is_default": False},
    {"category_key": "partnership", "slug": "partnership", "name": "Partnerships", "description": "Partnerships, sponsorships, collaborations, and promotions.", "intake_type": "partnership", "sort_order": 140, "is_default": False},
    {"category_key": "question", "slug": "question", "name": "Other Question", "description": "Ask something that does not fit the other options.", "intake_type": "question", "sort_order": 150, "is_default": False},
    {"category_key": "support", "slug": "support", "name": "Support", "description": "General help from staff.", "intake_type": "general", "sort_order": 999, "is_default": True},
)

_CATALOG_BY_KEY: Dict[str, Dict[str, Any]] = {
    str(row["category_key"]): dict(row) for row in CATEGORY_CATALOG
}

_ALIAS_TO_KEY: Dict[str, str] = {
    "verification": "verification",
    "verification-help": "verification",
    "verification-issue": "verification",
    "verify": "verification",
    "verify-help": "verification",
    "account-access": "account-access",
    "account-help": "account-access",
    "payments-refunds": "payments-refunds",
    "payment-refund": "payments-refunds",
    "purchase-refund": "payments-refunds",
    "appeal": "appeal",
    "appeals": "appeal",
    "report": "report",
    "reports": "report",
    "report-a-member": "report",
    "user-report": "report",
    "user-reports": "report",
    "staff-complaint": "staff-complaint",
    "staff-complaints": "staff-complaint",
    "staff-report": "staff-complaint",
    "bug": "bug",
    "bug-report": "bug",
    "technical-support": "bug",
    "bug-technical-support": "bug",
    "cod-services": "cod-services",
    "cod-service": "cod-services",
    "call-of-duty": "cod-services",
    "call-of-duty-services": "cod-services",
    "game-services": "game-services",
    "game-service": "game-services",
    "gaming-services": "game-services",
    "service-request": "service-request",
    "service-requests": "service-request",
    "vouch-referral": "vouch-referral",
    "vouch-invite-referral": "vouch-referral",
    "giveaway-reward": "giveaway-reward",
    "giveaway-rewards": "giveaway-reward",
    "giveaway-reward-issues": "giveaway-reward",
    "content-media": "content-media",
    "content-media-request": "content-media",
    "content-media-requests": "content-media",
    "partnership": "partnership",
    "partnerships": "partnership",
    "question": "question",
    "questions": "question",
    "other-question": "question",
    "support": "support",
    "general": "support",
    "general-support": "support",
    "other": "support",
    "custom": "support",
}


class ManagedCategorySyncError(RuntimeError):
    """Raised when the managed ticket-category catalog cannot be reconciled."""


class CategorySelectionError(RuntimeError):
    """Raised when a guild ticket-category selection cannot be saved safely."""


@dataclass(frozen=True)
class CategorySetupState:
    rows: List[Dict[str, Any]]
    active_rows: List[Dict[str, Any]]
    selected_keys: tuple[str, ...]
    required: bool
    reason: str
    version: int


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip() or default)
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", ""}:
        return False
    return bool(default)


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower().replace("&", " and ")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-")


def _row_value(row: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if not isinstance(row, Mapping):
        return default
    if key in row and row.get(key) is not None:
        return row.get(key)
    for bucket in ("settings", "config", "metadata", "meta"):
        nested = row.get(bucket)
        if isinstance(nested, Mapping) and nested.get(key) is not None:
            return nested.get(key)
    return default


def canonical_category_key(row: Mapping[str, Any]) -> str:
    managed = _slug(row.get("managed_category_key"))
    if managed in _CATALOG_BY_KEY:
        return managed

    slug = _slug(row.get("slug") or row.get("category_slug"))
    name = _slug(
        row.get("button_label")
        or row.get("name")
        or row.get("display_name")
        or row.get("title")
    )
    for candidate in (slug, name):
        key = _ALIAS_TO_KEY.get(candidate)
        if key:
            return key

    # Narrow compatibility rules only. Broad substring tests caused Support,
    # Reports, and service rows to collapse into the wrong option.
    combined = f"{slug} {name}".strip()
    if combined.startswith("verification-") or combined.startswith("verify-"):
        return "verification"
    if combined.startswith("staff-complaint") or combined.startswith("staff-report"):
        return "staff-complaint"
    if combined.startswith("report-") or combined.endswith("-report-a-member"):
        return "report"
    if combined.startswith("cod-") or combined.startswith("call-of-duty-"):
        return "cod-services"
    if combined.startswith("game-service") or combined.startswith("gaming-service"):
        return "game-services"
    if combined.startswith("service-request"):
        return "service-request"
    if combined.startswith("vouch-") or combined.startswith("referral-"):
        return "vouch-referral"
    if combined.startswith("content-media"):
        return "content-media"
    if combined.startswith("giveaway-reward"):
        return "giveaway-reward"

    return f"custom:{slug or name or 'ticket-choice'}"


def _row_enabled(row: Mapping[str, Any]) -> bool:
    if row.get("is_enabled") is not None:
        return _safe_bool(row.get("is_enabled"), True)
    if row.get("enabled") is not None:
        return _safe_bool(row.get("enabled"), True)
    return True


def _row_sort(row: Mapping[str, Any]) -> int:
    return _safe_int(row.get("sort_order", row.get("position", 999)), 999)


def _preferred_row(left: Dict[str, Any], right: Dict[str, Any], key: str) -> Dict[str, Any]:
    catalog = _CATALOG_BY_KEY.get(key)
    if catalog:
        canonical_slug = _slug(catalog.get("slug"))
        left_exact = _slug(left.get("slug")) == canonical_slug
        right_exact = _slug(right.get("slug")) == canonical_slug
        if left_exact != right_exact:
            return left if left_exact else right

    left_managed = _safe_bool(left.get("managed_by_dank"), False)
    right_managed = _safe_bool(right.get("managed_by_dank"), False)
    if left_managed != right_managed:
        return left if left_managed else right

    if _row_sort(right) < _row_sort(left):
        return right
    return left


def dedupe_category_rows(
    raw_rows: Iterable[Mapping[str, Any]] | None,
    *,
    enabled_only: bool = True,
    fallback: bool = False,
) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for raw in raw_rows or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        if enabled_only and not _row_enabled(row):
            continue
        key = canonical_category_key(row)
        if key in by_key:
            by_key[key] = _preferred_row(by_key[key], row, key)
        else:
            by_key[key] = row

    rows = list(by_key.values())
    rows.sort(
        key=lambda row: (
            _row_sort(row),
            str(row.get("name") or row.get("button_label") or row.get("slug") or "").lower(),
            canonical_category_key(row),
        )
    )
    if rows or not fallback:
        return rows[:25]
    return starter_category_rows()


def catalog_category_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in CATEGORY_CATALOG:
        row = dict(item)
        key = str(item["category_key"])
        row.update(
            {
                "managed_by_dank": True,
                "managed_category_key": key,
                "managed_catalog_version": CATEGORY_SETUP_VERSION,
                "is_enabled": key in SAFE_STARTER_KEYS,
            }
        )
        rows.append(row)
    return rows


def starter_category_rows() -> List[Dict[str, Any]]:
    rows = [
        row for row in catalog_category_rows()
        if str(row.get("category_key")) in SAFE_STARTER_KEYS
    ]
    for row in rows:
        row["is_enabled"] = True
        row["is_default"] = str(row.get("category_key")) == "support"
    return rows


def _supabase() -> Any:
    try:
        client = get_supabase()
    except Exception as exc:
        raise ManagedCategorySyncError("Supabase is unavailable.") from exc
    if not client:
        raise ManagedCategorySyncError("Supabase is unavailable.")
    return client


def _fetch_config_sync(guild_id: int) -> Dict[str, Any]:
    sb = _supabase()
    response = (
        sb.table("guild_configs")
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return dict(rows[0]) if rows and isinstance(rows[0], Mapping) else {}


def _fetch_rows_sync(guild_id: int) -> List[Dict[str, Any]]:
    sb = _supabase()
    response = (
        sb.table("ticket_categories")
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _sync_managed_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    sb = _supabase()
    try:
        response = sb.rpc(
            "reconcile_dank_ticket_categories",
            {"p_guild_id": str(int(guild_id))},
        ).execute()
    except Exception as exc:
        raise ManagedCategorySyncError(
            f"Managed category reconciliation failed for guild {int(guild_id)}."
        ) from exc

    rows = getattr(response, "data", None) or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _shape_problem(rows: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any]) -> tuple[str, bool]:
    version = _safe_int(_row_value(cfg, "ticket_category_setup_version", 0), 0)
    selected_raw = _row_value(cfg, "ticket_category_setup_selected_keys", [])
    selected_exists = bool(selected_raw)

    canonical_counts: Dict[str, int] = {}
    managed_enabled = 0
    managed_total = 0
    custom_total = 0
    for row in rows:
        key = canonical_category_key(row)
        canonical_counts[key] = canonical_counts.get(key, 0) + 1
        if _safe_bool(row.get("managed_by_dank"), False) or key in _CATALOG_BY_KEY:
            managed_total += 1
            if _row_enabled(row):
                managed_enabled += 1
        else:
            custom_total += 1

    duplicates = sorted(key for key, count in canonical_counts.items() if count > 1)
    if duplicates:
        return "Duplicate ticket choices were detected and must be reviewed.", True

    if version < CATEGORY_SETUP_VERSION and managed_enabled >= 10:
        return "The old setup enabled nearly every built-in ticket choice.", True

    if version < CATEGORY_SETUP_VERSION and managed_total and not custom_total and not selected_exists:
        return "Built-in ticket choices need owner confirmation in the new setup.", False

    return "", False


def _config_required(cfg: Mapping[str, Any]) -> bool:
    return _safe_bool(_row_value(cfg, "ticket_category_setup_required", False), False)


def _config_reason(cfg: Mapping[str, Any]) -> str:
    return str(_row_value(cfg, "ticket_category_setup_required_reason", "") or "").strip()


def _mark_required_fallback_sync(guild_id: int, reason: str, *, reset_to_starter: bool) -> None:
    sb = _supabase()
    gid = str(int(guild_id))
    rows = _fetch_rows_sync(guild_id)

    if reset_to_starter:
        for row in rows:
            key = canonical_category_key(row)
            if key not in _CATALOG_BY_KEY:
                continue
            row_id = row.get("id")
            if not row_id:
                continue
            patch = {
                "is_enabled": key in SAFE_STARTER_KEYS,
                "is_default": key == "support",
            }
            sb.table("ticket_categories").update(patch).eq("id", str(row_id)).execute()

    from ..commands_ext.public_setup_config_writer import upsert_guild_config_sync

    upsert_guild_config_sync(
        int(guild_id),
        {
            "ticket_category_setup_required": True,
            "ticket_category_setup_required_reason": str(reason)[:500],
            "ticket_category_setup_version": 0,
            "__config_write_mode": "force",
            "__config_write_source": "ticket category setup v2 runtime migration",
        },
    )


def require_category_setup_sync(
    guild_id: int,
    reason: str,
    *,
    reset_to_starter: bool,
) -> None:
    sb = _supabase()
    try:
        sb.rpc(
            "require_dank_ticket_category_setup",
            {
                "p_guild_id": str(int(guild_id)),
                "p_reason": str(reason)[:500],
                "p_reset_to_starter": bool(reset_to_starter),
            },
        ).execute()
        return
    except Exception:
        _mark_required_fallback_sync(
            guild_id,
            reason,
            reset_to_starter=reset_to_starter,
        )


def ensure_category_setup_state_sync(guild_id: int) -> CategorySetupState:
    try:
        _sync_managed_categories_sync(guild_id)
    except ManagedCategorySyncError:
        # Older deployments may not have the RPC yet. Existing rows are still
        # usable, and the safe in-memory fallback remains available.
        pass

    cfg = _fetch_config_sync(guild_id)
    rows = _fetch_rows_sync(guild_id)
    required = _config_required(cfg)
    reason = _config_reason(cfg)

    if not required:
        detected_reason, reset = _shape_problem(rows, cfg)
        if detected_reason:
            require_category_setup_sync(
                guild_id,
                detected_reason,
                reset_to_starter=reset,
            )
            cfg = _fetch_config_sync(guild_id)
            rows = _fetch_rows_sync(guild_id)
            required = True
            reason = detected_reason

    active = dedupe_category_rows(rows, enabled_only=True, fallback=True)
    selected = tuple(
        canonical_category_key(row)
        for row in active
        if canonical_category_key(row) in _CATALOG_BY_KEY
    )
    return CategorySetupState(
        rows=dedupe_category_rows(rows, enabled_only=False, fallback=False),
        active_rows=active,
        selected_keys=tuple(dict.fromkeys(selected)),
        required=required,
        reason=reason,
        version=_safe_int(_row_value(cfg, "ticket_category_setup_version", 0), 0),
    )


def load_visible_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    try:
        return ensure_category_setup_state_sync(guild_id).active_rows
    except Exception:
        return starter_category_rows()


def _normalize_selected_keys(selected_keys: Iterable[Any]) -> tuple[str, ...]:
    selected: List[str] = []
    for raw in selected_keys:
        key = _slug(raw)
        key = _ALIAS_TO_KEY.get(key, key)
        if key in _CATALOG_BY_KEY and key not in selected:
            selected.append(key)
    if not selected:
        raise CategorySelectionError("Choose at least one ticket option.")
    return tuple(selected[:25])


def _save_selection_fallback_sync(
    guild_id: int,
    selected_keys: tuple[str, ...],
    *,
    actor_id: str,
    actor_name: str,
) -> None:
    sb = _supabase()
    rows = _fetch_rows_sync(guild_id)
    default_key = "support" if "support" in selected_keys else selected_keys[0]

    for row in rows:
        key = canonical_category_key(row)
        if key not in _CATALOG_BY_KEY:
            continue
        row_id = row.get("id")
        if not row_id:
            continue
        sb.table("ticket_categories").update(
            {
                "is_enabled": key in selected_keys,
                "is_default": key == default_key,
            }
        ).eq("id", str(row_id)).execute()

    from ..commands_ext.public_setup_config_writer import upsert_guild_config_sync

    upsert_guild_config_sync(
        int(guild_id),
        {
            "ticket_category_setup_required": False,
            "ticket_category_setup_required_reason": "",
            "ticket_category_setup_version": CATEGORY_SETUP_VERSION,
            "ticket_category_setup_selected_keys": list(selected_keys),
            "ticket_category_setup_completed_by_id": actor_id,
            "ticket_category_setup_completed_by_name": actor_name,
            "__config_write_mode": "force",
            "__config_write_source": "ticket category setup v2 selection",
        },
    )


def save_category_selection_sync(
    guild_id: int,
    selected_keys: Iterable[Any],
    *,
    actor_id: Any = "",
    actor_name: Any = "",
) -> CategorySetupState:
    selected = _normalize_selected_keys(selected_keys)
    sb = _supabase()
    try:
        sb.rpc(
            "save_dank_ticket_category_selection",
            {
                "p_guild_id": str(int(guild_id)),
                "p_selected_keys": list(selected),
                "p_actor_id": str(actor_id or ""),
                "p_actor_name": str(actor_name or ""),
            },
        ).execute()
    except Exception:
        _save_selection_fallback_sync(
            guild_id,
            selected,
            actor_id=str(actor_id or ""),
            actor_name=str(actor_name or ""),
        )
    return ensure_category_setup_state_sync(guild_id)


async def sync_managed_categories(guild_id: int) -> List[Dict[str, Any]]:
    """Reconcile one guild against the current global Dank Shield catalog."""
    return await asyncio.to_thread(_sync_managed_categories_sync, int(guild_id))


async def ensure_category_setup_state(guild_id: int) -> CategorySetupState:
    return await asyncio.to_thread(ensure_category_setup_state_sync, int(guild_id))


async def load_visible_categories(guild_id: int) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(load_visible_categories_sync, int(guild_id))


async def save_category_selection(
    guild_id: int,
    selected_keys: Iterable[Any],
    *,
    actor_id: Any = "",
    actor_name: Any = "",
) -> CategorySetupState:
    return await asyncio.to_thread(
        save_category_selection_sync,
        int(guild_id),
        tuple(selected_keys),
        actor_id=actor_id,
        actor_name=actor_name,
    )


def summarize_sync(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    inserted = 0
    updated = 0
    deleted_duplicates = 0
    for row in rows:
        inserted += _safe_int(row.get("inserted_count"), 0)
        updated += _safe_int(row.get("updated_count"), 0)
        deleted_duplicates += _safe_int(row.get("deleted_duplicate_count"), 0)
    return {
        "inserted": inserted,
        "updated": updated,
        "deleted_duplicates": deleted_duplicates,
    }


__all__ = [
    "CATEGORY_CATALOG",
    "CATEGORY_SETUP_VERSION",
    "SAFE_STARTER_KEYS",
    "CategorySelectionError",
    "CategorySetupState",
    "ManagedCategorySyncError",
    "canonical_category_key",
    "catalog_category_rows",
    "dedupe_category_rows",
    "ensure_category_setup_state",
    "ensure_category_setup_state_sync",
    "load_visible_categories",
    "load_visible_categories_sync",
    "require_category_setup_sync",
    "save_category_selection",
    "save_category_selection_sync",
    "starter_category_rows",
    "summarize_sync",
    "sync_managed_categories",
]
