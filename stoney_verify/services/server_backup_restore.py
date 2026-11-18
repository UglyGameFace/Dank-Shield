from __future__ import annotations

"""Plan-first restore engine for whole-server structure backups.

Phase 3 Slice 2 (spec §1.7-§1.8): given a stored structure snapshot
(Slice 1, ``server_backup_service``) and the live guild, build a dry-run
restore plan — create / update / skip / delete_candidate / blocked rows in
the design-plan idiom — preflight it against a fresh fetch, and apply it
under the spec's binding safety rules:

- R1 deletes are never implicit: they live in their own plan section and
  run only with a separate ``delete_confirmed`` flag, last, and are not
  compensable;
- R2 managed roles are never created, edited, or deleted;
- R3 role rows at or above the bot's top role are blocked, never attempted;
- R4 preflight validates the whole plan before the first mutation and any
  error aborts with zero mutations;
- R5 a pre-restore auto-backup is taken before the first mutation, and the
  restore does not start if that backup fails;
- R6 the first unexpected failure stops the run and compensates earlier
  creates/updates where an inverse exists;
- R7 every row is revalidated immediately before its mutation;
- R8 overwrite targets resolve through the plan's key map; unresolvable
  overwrites are dropped and reported, never guessed;
- R9 bot-owned resources (Share Router plumbing, config-referenced
  channels) are excluded from update/delete;
- R10 ``verification_level`` is always its own plan row.

Progress is persisted into the backup row's ``restore_state`` cursor after
every applied row (§1.8), so an interrupted restore can resume: rows whose
``after`` state already matches live replan as ``skip``.

The panel + Pro gate layer is Slice 3; nothing here renders UI.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import discord

from stoney_verify.permission_repair_core import _overwrite_from_snapshot
from stoney_verify.services import channel_mutation_throttle as throttle
from stoney_verify.services import server_backup_service as backups
from stoney_verify.services.channel_builder_runtime import (
    CATEGORY_CHILD_LIMIT,
    DISCORD_CHANNEL_LIMIT,
)
from stoney_verify.services.server_design_apply_service import fresh_channel_map
from stoney_verify.share_router_resources import (
    is_share_router_design_resource,
    is_share_router_managed_category_name,
)

logger = logging.getLogger(__name__)

ROLE_LIMIT = 250
NAME_LIMIT = 100
PRE_RESTORE_LABEL = "Pre-restore auto-backup"

ProgressCallback = Callable[[int, int], Awaitable[None]]
CancelCheck = Callable[[], Any]

_PLAN_MODES = ("all", "missing", "selected")
_ROLE_FIELDS = ("name", "color", "hoist", "mentionable", "position", "permissions")
_CATEGORY_FIELDS = ("name", "position", "nsfw")
_CHANNEL_FIELDS = (
    "type",
    "name",
    "position",
    "topic",
    "nsfw",
    "slowmode_delay",
    "bitrate",
    "user_limit",
    "rtc_region",
    "video_quality_mode",
)
_SETTING_FIELDS = (
    "name",
    "description",
    "preferred_locale",
    "verification_level",
    "explicit_content_filter",
    "default_notifications",
    "afk_timeout",
    "afk_channel_key",
    "system_channel_key",
    "rules_channel_key",
    "public_updates_channel_key",
)
_REF_SETTING_PARAMS = {
    "afk_channel_key": "afk_channel",
    "system_channel_key": "system_channel",
    "rules_channel_key": "rules_channel",
    "public_updates_channel_key": "public_updates_channel",
}
_SETTING_ENUMS = {
    "verification_level": discord.VerificationLevel,
    "explicit_content_filter": discord.ContentFilter,
    "default_notifications": discord.NotificationLevel,
}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _row(
    kind: str,
    key: str,
    action: str,
    target_id: Optional[str],
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
    status: str,
    blockers: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "key": key,
        "action": action,
        "target_id": target_id,
        "before": before,
        "after": after,
        "status": status,
        "blockers": list(blockers or []),
    }


def _is_category_obj(obj: Any) -> bool:
    if isinstance(obj, discord.CategoryChannel):
        return True
    raw = getattr(obj, "type", None)
    return _int(getattr(raw, "value", raw), -1) == 4


def _live_view(
    guild: Any,
    *,
    channels: Optional[list] = None,
    roles: Optional[list] = None,
) -> dict[str, Any]:
    """Serialize the live guild into the §1.6 snapshot shape for comparison.

    With no overrides this reads the guild cache (the planner contract).
    Preflight passes a fresh channel/role fetch instead; only the
    roles/categories/channels of that view are consumed there.
    """

    if channels is None and roles is None:
        return backups.serialize_guild_structure(guild)
    shim_channels = (
        list(channels)
        if channels is not None
        else list(getattr(guild, "channels", None) or [])
    )
    shim = SimpleNamespace(
        id=_int(getattr(guild, "id", 0)),
        name=_str(getattr(guild, "name", "")),
        roles=list(roles)
        if roles is not None
        else list(getattr(guild, "roles", None) or []),
        channels=shim_channels,
        categories=[c for c in shim_channels if _is_category_obj(c)],
    )
    return backups.serialize_guild_structure(shim)


# ---------------------------------------------------------------------------
# Field-difference helpers (shared by the planner and apply revalidation)
# ---------------------------------------------------------------------------


def _normalize_overwrites(
    rows: Any, key_map: Optional[Mapping[str, str]]
) -> list[dict[str, Any]]:
    """Overwrite rows in comparable form; ``key_map`` translates the
    snapshot-space target keys of the *after* side into live-key space
    (None = already live-space). Unresolvable targets get a sentinel that
    can never compare equal, so they always read as a difference."""

    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        target = row.get("target_key")
        if key_map is not None:
            target = key_map.get(str(target)) or f"unresolved:{target}"
        out.append(
            {
                "target_type": row.get("target_type", "role"),
                "target_key": target,
                "allow": sorted(str(v) for v in (row.get("allow") or [])),
                "deny": sorted(str(v) for v in (row.get("deny") or [])),
            }
        )
    return sorted(out, key=lambda r: str(r["target_key"]))


def _field_diffs(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    fields: Iterable[str],
) -> list[str]:
    return [name for name in fields if before.get(name) != after.get(name)]


def _role_differences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    everyone: bool = False,
    include_position: bool = True,
) -> list[str]:
    fields = _ROLE_FIELDS if include_position else tuple(
        f for f in _ROLE_FIELDS if f != "position"
    )
    if everyone:
        fields = ("permissions",)
    return _field_diffs(before, after, fields)


def _category_differences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    role_key_map: Optional[Mapping[str, str]] = None,
    include_position: bool = True,
) -> list[str]:
    fields = _CATEGORY_FIELDS if include_position else tuple(
        f for f in _CATEGORY_FIELDS if f != "position"
    )
    diffs = _field_diffs(before, after, fields)
    if _normalize_overwrites(before.get("overwrites"), None) != _normalize_overwrites(
        after.get("overwrites"), role_key_map
    ):
        diffs.append("overwrites")
    return diffs


def _channel_differences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    role_key_map: Optional[Mapping[str, str]] = None,
    cat_key_map: Optional[Mapping[str, str]] = None,
    include_position: bool = True,
) -> list[str]:
    fields = _CHANNEL_FIELDS if include_position else tuple(
        f for f in _CHANNEL_FIELDS if f != "position"
    )
    diffs = _field_diffs(before, after, fields)
    after_category = after.get("category_key")
    if cat_key_map is not None and after_category:
        after_category = cat_key_map.get(str(after_category)) or (
            f"unresolved:{after_category}"
        )
    if before.get("category_key") != after_category:
        diffs.append("category")
    if before.get("forum") != after.get("forum"):
        diffs.append("forum")
    if _normalize_overwrites(before.get("overwrites"), None) != _normalize_overwrites(
        after.get("overwrites"), role_key_map
    ):
        diffs.append("overwrites")
    return diffs


def _differences_for(
    kind: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    everyone: bool = False,
    include_position: bool = True,
    role_key_map: Optional[Mapping[str, str]] = None,
    cat_key_map: Optional[Mapping[str, str]] = None,
) -> list[str]:
    if kind == "role":
        return _role_differences(
            before, after, everyone=everyone, include_position=include_position
        )
    if kind == "category":
        return _category_differences(
            before,
            after,
            role_key_map=role_key_map,
            include_position=include_position,
        )
    return _channel_differences(
        before,
        after,
        role_key_map=role_key_map,
        cat_key_map=cat_key_map,
        include_position=include_position,
    )


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def _index_live(rows: list[dict[str, Any]]):
    by_id: dict[int, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_id[_int(row.get("source_id"))] = row
        by_name.setdefault(_str(row.get("name")), []).append(row)
    return by_id, by_name


def _match_entries(
    snapshot_rows: list[dict[str, Any]],
    live_by_id: Mapping[int, dict[str, Any]],
    live_by_name: Mapping[str, list[dict[str, Any]]],
    *,
    category_filter: Optional[Callable[[dict[str, Any], dict[str, Any]], bool]] = None,
):
    """Match snapshot entries to live rows: source id first, then a unique
    name (the channel-builder ``_find_channel`` precedent). Returns
    (matches {snap key: live row}, ambiguous {snap key})."""

    matches: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    claimed: dict[int, str] = {}
    for entry in snapshot_rows:
        key = _str(entry.get("key"))
        source_id = _int(entry.get("source_id"))
        live_row = live_by_id.get(source_id) if source_id > 0 else None
        if live_row is not None and source_id not in claimed:
            matches[key] = live_row
            claimed[source_id] = key
            continue
        candidates = [
            row
            for row in live_by_name.get(_str(entry.get("name")), [])
            if _int(row.get("source_id")) not in claimed
            and (category_filter is None or category_filter(entry, row))
        ]
        if len(candidates) == 1:
            matches[key] = candidates[0]
            claimed[_int(candidates[0].get("source_id"))] = key
        elif len(candidates) > 1:
            ambiguous.add(key)
    return matches, ambiguous, claimed


def _bot_top_position(guild: Any) -> Optional[int]:
    me = getattr(guild, "me", None)
    top = getattr(me, "top_role", None) if me is not None else None
    if top is None:
        return None
    return _int(getattr(top, "position", 0))


def _bot_role_ids(guild: Any) -> set[int]:
    me = getattr(guild, "me", None)
    roles = getattr(me, "roles", None) if me is not None else None
    return {_int(getattr(role, "id", 0)) for role in (roles or [])} - {0}


def build_restore_plan(
    guild: Any,
    snapshot: Mapping[str, Any],
    *,
    mode: str = "all",
    selected_keys: Optional[Iterable[str]] = None,
    protected_ids: Optional[Iterable[int]] = None,
) -> list[dict[str, Any]]:
    """Build the dry-run restore plan for ``snapshot`` against the live
    guild cache. Pure read — no Discord writes, no storage access.

    ``mode`` mirrors config-history selective restore: ``all`` (default),
    ``missing`` (creates only; differing objects are left untouched and no
    delete candidates are produced), ``selected`` (only ``selected_keys``
    plus the creates they depend on). ``protected_ids`` lets the caller
    (which can read Dank Shield config) additionally exclude live objects
    from delete candidacy; preflight re-checks config references itself.
    """

    if mode not in _PLAN_MODES:
        raise ValueError(f"unknown restore plan mode: {mode!r}")
    if mode == "selected" and selected_keys is None:
        raise ValueError("mode='selected' requires selected_keys")

    snapshot = _mapping(snapshot)
    guild_id = _int(getattr(guild, "id", 0))
    live = _live_view(guild)
    protected = {_int(v) for v in (protected_ids or ())} - {0}
    bot_top = _bot_top_position(guild)
    bot_roles = _bot_role_ids(guild)

    snap_roles = sorted(
        (_mapping(r) for r in (snapshot.get("roles") or [])),
        key=lambda r: (_int(r.get("position")), _str(r.get("key"))),
    )
    snap_categories = sorted(
        (_mapping(c) for c in (snapshot.get("categories") or [])),
        key=lambda c: (_int(c.get("position")), _str(c.get("key"))),
    )
    snap_channels = sorted(
        (_mapping(c) for c in (snapshot.get("channels") or [])),
        key=lambda c: (_int(c.get("position")), _str(c.get("key"))),
    )
    live_roles = [_mapping(r) for r in (live.get("roles") or [])]
    live_categories = [_mapping(c) for c in (live.get("categories") or [])]
    live_channels = [_mapping(c) for c in (live.get("channels") or [])]

    live_role_by_id, live_role_by_name = _index_live(live_roles)
    live_cat_by_id, live_cat_by_name = _index_live(live_categories)
    live_chan_by_id, live_chan_by_name = _index_live(live_channels)

    role_objs = {_int(getattr(r, "id", 0)): r for r in (getattr(guild, "roles", None) or [])}
    cat_objs = {
        _int(getattr(c, "id", 0)): c for c in (getattr(guild, "categories", None) or [])
    }
    if not cat_objs:
        cat_objs = {
            _int(getattr(c, "id", 0)): c
            for c in (getattr(guild, "channels", None) or [])
            if _is_category_obj(c)
        }
    chan_objs = {
        _int(getattr(c, "id", 0)): c
        for c in (getattr(guild, "channels", None) or [])
        if not _is_category_obj(c)
    }

    # -- matching ------------------------------------------------------------
    role_match, role_ambiguous, role_claimed = _match_entries(
        snap_roles, live_role_by_id, live_role_by_name
    )
    cat_match, cat_ambiguous, cat_claimed = _match_entries(
        snap_categories, live_cat_by_id, live_cat_by_name
    )
    snap_cat_live_id: dict[int, int] = {}
    for entry in snap_categories:
        matched = cat_match.get(_str(entry.get("key")))
        if matched is not None:
            snap_cat_live_id[_int(entry.get("source_id"))] = _int(matched.get("source_id"))

    def _same_category(snap_entry: Mapping[str, Any], live_row: Mapping[str, Any]) -> bool:
        snap_cat_key = snap_entry.get("category_key")
        if not snap_cat_key:
            return not live_row.get("category_key")
        live_cat_id = snap_cat_live_id.get(_int(str(snap_cat_key).split(":", 1)[-1]))
        if live_cat_id is None:
            return False
        return _str(live_row.get("category_key")) == f"cat:{live_cat_id}"

    chan_match, chan_ambiguous, chan_claimed = _match_entries(
        snap_channels,
        live_chan_by_id,
        live_chan_by_name,
        category_filter=_same_category,
    )

    # Snapshot-key -> live-key maps, used to translate overwrite targets and
    # channel categories into live space for comparison (R8).
    role_key_map: dict[str, str] = {}
    for entry in snap_roles:
        matched = role_match.get(_str(entry.get("key")))
        if matched is not None:
            role_key_map[_str(entry.get("key"))] = _str(matched.get("key"))
    cat_key_map: dict[str, str] = {}
    for entry in snap_categories:
        matched = cat_match.get(_str(entry.get("key")))
        if matched is not None:
            cat_key_map[_str(entry.get("key"))] = _str(matched.get("key"))

    # -- role rows (ascending snapshot position: hierarchy builds bottom-up) --
    role_rows: list[dict[str, Any]] = []
    for entry in snap_roles:
        key = _str(entry.get("key"))
        matched = role_match.get(key)
        target_id = _str(matched.get("source_id")) if matched else None
        everyone = bool(entry.get("is_default")) or key == "role:@everyone"
        if key in role_ambiguous:
            role_rows.append(
                _row(
                    "role", key, "blocked", None, None, entry, "failed",
                    [f"Several live roles are named “{_str(entry.get('name'))}”; "
                     "restore cannot pick one safely (R8)."],
                )
            )
            continue
        if bool(entry.get("managed")):
            if matched is None:
                role_rows.append(
                    _row(
                        "role", key, "skip", None, None, entry, "unchanged",
                        ["Managed role has no live counterpart; the bot cannot "
                         "create managed roles (R2)."],
                    )
                )
            else:
                role_rows.append(
                    _row("role", key, "skip", target_id, matched, entry, "unchanged")
                )
            continue
        if everyone:
            if matched is None:
                role_rows.append(
                    _row(
                        "role", key, "blocked", None, None, entry, "failed",
                        ["The @everyone role could not be found in the live guild."],
                    )
                )
                continue
            diffs = _role_differences(matched, entry, everyone=True)
            if diffs:
                role_rows.append(
                    _row("role", key, "update", target_id, matched, entry, "changed")
                )
            else:
                role_rows.append(
                    _row("role", key, "skip", target_id, matched, entry, "unchanged")
                )
            continue
        if matched is not None:
            diffs = _role_differences(matched, entry)
            if not diffs:
                role_rows.append(
                    _row("role", key, "skip", target_id, matched, entry, "unchanged")
                )
            elif bot_top is not None and _int(matched.get("position")) >= bot_top:
                role_rows.append(
                    _row(
                        "role", key, "blocked", target_id, matched, entry, "failed",
                        ["This role sits at or above Dank Shield's top role; "
                         "Discord forbids the bot from editing it (R3)."],
                    )
                )
            else:
                role_rows.append(
                    _row("role", key, "update", target_id, matched, entry, "changed")
                )
            continue
        # Unmatched -> create.
        if bot_top is not None and _int(entry.get("position")) >= bot_top:
            role_rows.append(
                _row(
                    "role", key, "blocked", None, None, entry, "failed",
                    ["The backup places this role at or above Dank Shield's top "
                     "role; Discord forbids the bot from putting it there (R3)."],
                )
            )
        else:
            role_rows.append(
                _row("role", key, "create", None, None, entry, "changed")
            )

    # -- category rows ---------------------------------------------------------
    category_rows: list[dict[str, Any]] = []
    for entry in snap_categories:
        key = _str(entry.get("key"))
        matched = cat_match.get(key)
        target_id = _str(matched.get("source_id")) if matched else None
        if key in cat_ambiguous:
            category_rows.append(
                _row(
                    "category", key, "blocked", None, None, entry, "failed",
                    [f"Several live categories are named “{_str(entry.get('name'))}”; "
                     "restore cannot pick one safely (R8)."],
                )
            )
            continue
        if matched is not None:
            live_obj = cat_objs.get(_int(matched.get("source_id")))
            if live_obj is not None and is_share_router_design_resource(live_obj):
                category_rows.append(
                    _row(
                        "category", key, "skip", target_id, matched, entry,
                        "unchanged",
                        ["Managed by Share Router; restore leaves it untouched (R9)."],
                    )
                )
                continue
            diffs = _category_differences(matched, entry, role_key_map=role_key_map)
            if diffs:
                category_rows.append(
                    _row("category", key, "update", target_id, matched, entry, "changed")
                )
            else:
                category_rows.append(
                    _row("category", key, "skip", target_id, matched, entry, "unchanged")
                )
            continue
        if is_share_router_managed_category_name(_str(entry.get("name"))):
            category_rows.append(
                _row(
                    "category", key, "blocked", None, None, entry, "failed",
                    ["This category name is reserved for Share Router plumbing; "
                     "restore will not recreate it (R9)."],
                )
            )
            continue
        category_rows.append(
            _row("category", key, "create", None, None, entry, "changed")
        )
    category_row_by_key = {row["key"]: row for row in category_rows}

    # -- channel rows (grouped by category, snapshot order preserved) ---------
    channel_rows: list[dict[str, Any]] = []
    for entry in snap_channels:
        key = _str(entry.get("key"))
        matched = chan_match.get(key)
        target_id = _str(matched.get("source_id")) if matched else None
        entry_type = _str(entry.get("type"))
        if key in chan_ambiguous:
            channel_rows.append(
                _row(
                    "channel", key, "blocked", None, None, entry, "failed",
                    [f"Several live channels are named “{_str(entry.get('name'))}” "
                     "in the same category; restore cannot pick one safely (R8)."],
                )
            )
            continue
        category_key = entry.get("category_key")
        category_row = category_row_by_key.get(_str(category_key)) if category_key else None
        if category_key and category_row is None:
            channel_rows.append(
                _row(
                    "channel", key, "blocked", target_id,
                    matched, entry, "failed",
                    ["Its category is not part of this backup."],
                )
            )
            continue
        if category_row is not None and category_row["action"] == "blocked":
            channel_rows.append(
                _row(
                    "channel", key, "blocked", target_id, matched, entry, "failed",
                    ["Its category could not be restored, so the channel is "
                     "blocked too."],
                )
            )
            continue
        if matched is not None:
            live_obj = chan_objs.get(_int(matched.get("source_id")))
            if live_obj is not None and is_share_router_design_resource(live_obj):
                channel_rows.append(
                    _row(
                        "channel", key, "skip", target_id, matched, entry,
                        "unchanged",
                        ["Managed by Share Router; restore leaves it untouched (R9)."],
                    )
                )
                continue
            diffs = _channel_differences(
                matched,
                entry,
                role_key_map=role_key_map,
                cat_key_map=cat_key_map,
            )
            if entry_type == "stage":
                if diffs:
                    channel_rows.append(
                        _row(
                            "channel", key, "blocked", target_id, matched, entry,
                            "failed",
                            ["Stage channels are captured for reference but are "
                             "not restorable in v1."],
                        )
                    )
                else:
                    channel_rows.append(
                        _row("channel", key, "skip", target_id, matched, entry, "unchanged")
                    )
                continue
            if "type" in diffs and {_str(matched.get("type")), entry_type} != {"text", "news"}:
                channel_rows.append(
                    _row(
                        "channel", key, "blocked", target_id, matched, entry,
                        "failed",
                        [f"The live channel is a {_str(matched.get('type'))} channel "
                         f"but the backup has a {entry_type} channel; restore "
                         "cannot convert channel types."],
                    )
                )
                continue
            if diffs:
                channel_rows.append(
                    _row("channel", key, "update", target_id, matched, entry, "changed")
                )
            else:
                channel_rows.append(
                    _row("channel", key, "skip", target_id, matched, entry, "unchanged")
                )
            continue
        if entry_type == "stage":
            channel_rows.append(
                _row(
                    "channel", key, "blocked", None, None, entry, "failed",
                    ["Stage channels are captured for reference but cannot be "
                     "created by restore v1."],
                )
            )
            continue
        channel_rows.append(
            _row("channel", key, "create", None, None, entry, "changed")
        )

    # -- guild setting rows ------------------------------------------------------
    setting_rows: list[dict[str, Any]] = []
    snap_guild = _mapping(snapshot.get("guild"))
    live_guild = _mapping(live.get("guild"))
    channel_row_by_key = {row["key"]: row for row in channel_rows}
    for field_name in _SETTING_FIELDS:
        if field_name not in snap_guild:
            continue
        snap_value = snap_guild.get(field_name)
        live_value = live_guild.get(field_name)
        if snap_value == live_value:
            continue
        key = f"setting:{field_name}"
        if field_name in _REF_SETTING_PARAMS:
            if snap_value is None:
                setting_rows.append(
                    _row(
                        "guild_setting", key, "update", None,
                        {field_name: live_value}, {field_name: None}, "changed",
                    )
                )
                continue
            ref_row = channel_row_by_key.get(_str(snap_value))
            if ref_row is None:
                setting_rows.append(
                    _row(
                        "guild_setting", key, "blocked", None,
                        {field_name: live_value}, {field_name: snap_value}, "failed",
                        ["The channel this setting points at is not part of "
                         "this backup."],
                    )
                )
            elif ref_row["action"] == "blocked":
                setting_rows.append(
                    _row(
                        "guild_setting", key, "blocked", None,
                        {field_name: live_value}, {field_name: snap_value}, "failed",
                        ["The channel this setting points at could not be "
                         "restored."],
                    )
                )
            else:
                setting_rows.append(
                    _row(
                        "guild_setting", key, "update", None,
                        {field_name: live_value}, {field_name: snap_value}, "changed",
                    )
                )
            continue
        setting_rows.append(
            _row(
                "guild_setting", key, "update", None,
                {field_name: live_value}, {field_name: snap_value}, "changed",
            )
        )

    rows = role_rows + category_rows + channel_rows + setting_rows

    # -- R4 limits: excess creates become blocked rows ---------------------------
    create_total = len(live_channels) + len(live_categories)
    for row in rows:
        if row["kind"] in ("category", "channel") and row["action"] == "create":
            create_total += 1
            if create_total > DISCORD_CHANNEL_LIMIT:
                row["action"] = "blocked"
                row["status"] = "failed"
                row["blockers"].append(
                    f"Discord's {DISCORD_CHANNEL_LIMIT}-channel limit would be "
                    "exceeded (R4)."
                )
    role_total = len(live_roles)
    for row in rows:
        if row["kind"] == "role" and row["action"] == "create":
            role_total += 1
            if role_total > ROLE_LIMIT:
                row["action"] = "blocked"
                row["status"] = "failed"
                row["blockers"].append(
                    f"Discord's {ROLE_LIMIT}-role limit would be exceeded (R4)."
                )
    children: dict[str, int] = {}
    for live_row in live_channels:
        parent = live_row.get("category_key")
        if parent:
            children[_str(parent)] = children.get(_str(parent), 0) + 1
    for row in channel_rows:
        if row["action"] != "create":
            continue
        after = row.get("after") or {}
        snap_cat_key = after.get("category_key")
        if not snap_cat_key:
            continue
        live_parent = cat_key_map.get(_str(snap_cat_key)) or _str(snap_cat_key)
        children[live_parent] = children.get(live_parent, 0) + 1
        if children[live_parent] > CATEGORY_CHILD_LIMIT:
            row["action"] = "blocked"
            row["status"] = "failed"
            row["blockers"].append(
                f"Its category would exceed Discord's {CATEGORY_CHILD_LIMIT}-"
                "channel limit (R4)."
            )

    # -- delete candidates (mode "all" only; never implicit, R1) ------------------
    delete_rows: list[dict[str, Any]] = []
    if mode == "all":
        channel_candidate_ids: set[int] = set()
        channel_candidates: list[dict[str, Any]] = []
        for live_row in sorted(
            live_channels,
            key=lambda r: (_int(r.get("position")), _int(r.get("source_id"))),
        ):
            live_id = _int(live_row.get("source_id"))
            if live_id in chan_claimed or live_id <= 0:
                continue
            if live_id in protected:
                continue
            live_obj = chan_objs.get(live_id)
            if live_obj is not None and is_share_router_design_resource(live_obj):
                continue
            channel_candidate_ids.add(live_id)
            channel_candidates.append(
                _row(
                    "channel", _str(live_row.get("key")), "delete_candidate",
                    _str(live_row.get("source_id")), live_row, None, "changed",
                )
            )
        category_candidates: list[dict[str, Any]] = []
        for live_row in sorted(
            live_categories,
            key=lambda r: (_int(r.get("position")), _int(r.get("source_id"))),
        ):
            live_id = _int(live_row.get("source_id"))
            if live_id in cat_claimed or live_id <= 0:
                continue
            if live_id in protected:
                continue
            live_obj = cat_objs.get(live_id)
            if live_obj is not None and is_share_router_design_resource(live_obj):
                continue
            staying = [
                row
                for row in live_channels
                if _str(row.get("category_key")) == _str(live_row.get("key"))
                and _int(row.get("source_id")) not in channel_candidate_ids
            ]
            if staying:
                category_candidates.append(
                    _row(
                        "category", _str(live_row.get("key")), "blocked",
                        _str(live_row.get("source_id")), live_row, None, "failed",
                        ["This category still holds channels that are staying, "
                         "so restore will not delete it."],
                    )
                )
            else:
                category_candidates.append(
                    _row(
                        "category", _str(live_row.get("key")), "delete_candidate",
                        _str(live_row.get("source_id")), live_row, None, "changed",
                    )
                )
        role_candidates: list[dict[str, Any]] = []
        for live_row in sorted(
            live_roles,
            key=lambda r: (_int(r.get("position")), _int(r.get("source_id"))),
        ):
            live_id = _int(live_row.get("source_id"))
            if live_id in role_claimed or live_id <= 0:
                continue
            if live_id in protected or live_id in bot_roles:
                continue
            if bool(live_row.get("is_default")) or bool(live_row.get("managed")):
                continue
            role_candidates.append(
                _row(
                    "role", _str(live_row.get("key")), "delete_candidate",
                    _str(live_row.get("source_id")), live_row, None, "changed",
                )
            )
        delete_rows = channel_candidates + category_candidates + role_candidates

    rows = rows + delete_rows

    # -- mode transforms -----------------------------------------------------------
    if mode == "missing":
        transformed: list[dict[str, Any]] = []
        for row in rows:
            if row["action"] == "delete_candidate":
                continue
            if row["action"] == "update":
                row = dict(row)
                row["action"] = "skip"
                row["status"] = "unchanged"
                row["blockers"] = list(row["blockers"]) + [
                    "Differs from the backup; left unchanged in missing-only mode."
                ]
            transformed.append(row)
        rows = transformed
    elif mode == "selected":
        rows = _apply_selection(rows, set(_str(k) for k in (selected_keys or ())))

    return rows


def _apply_selection(
    rows: list[dict[str, Any]], wanted: set[str]
) -> list[dict[str, Any]]:
    """Keep only selected rows plus the creates they depend on (a selected
    channel needs its category and its overwrite roles to exist)."""

    by_key = {row["key"]: row for row in rows}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row["key"] not in wanted or row["action"] == "delete_candidate":
                continue
            after = row.get("after") or {}
            deps: set[str] = set()
            if row["kind"] == "channel" and after.get("category_key"):
                deps.add(_str(after.get("category_key")))
            if row["kind"] == "guild_setting":
                for value in after.values():
                    if isinstance(value, str) and value.startswith("chan:"):
                        deps.add(value)
            for overwrite in after.get("overwrites") or []:
                if isinstance(overwrite, Mapping) and overwrite.get("target_key"):
                    deps.add(_str(overwrite.get("target_key")))
            for dep in deps:
                dep_row = by_key.get(dep)
                if (
                    dep_row is not None
                    and dep_row["action"] == "create"
                    and dep not in wanted
                ):
                    wanted.add(dep)
                    dep_row["blockers"] = list(dep_row["blockers"]) + [
                        "Included because a selected item depends on it."
                    ]
                    changed = True
    return [
        row
        for row in rows
        if row["key"] in wanted and row["action"] != "delete_candidate"
    ]


# ---------------------------------------------------------------------------
# Preflight (fresh fetch; any error aborts with zero mutations, R4)
# ---------------------------------------------------------------------------


async def _fresh_roles(guild: Any) -> list:
    fetch = getattr(guild, "fetch_roles", None)
    if callable(fetch):
        try:
            rows = await fetch()
            if rows:
                return list(rows)
        except Exception:
            pass
    return list(getattr(guild, "roles", None) or [])


def _serialize_one(kind: str, obj: Any, guild_id: int) -> Optional[dict[str, Any]]:
    omitted = {"member_overwrites": 0, "stage_channels": 0}
    if kind == "role":
        return backups._serialize_role(obj, guild_id)
    if kind == "category":
        return backups._serialize_category(obj, guild_id, omitted)
    return backups._serialize_channel(obj, guild_id, omitted)


async def _config_protected_ids(guild: Any) -> set[int]:
    """Every snowflake Dank Shield's saved config references (verification,
    ticket, log, stats channels — and, deliberately, referenced roles too).
    A restore delete that targets one is refused at preflight (R1/R9)."""

    guild_id = _int(getattr(guild, "id", 0))
    if guild_id <= 0:
        return set()
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild_id, refresh=False)
    except Exception:
        return set()
    found: set[int] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for item in value.values():
                _walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                _walk(item)
        elif isinstance(value, bool):
            return
        elif isinstance(value, int):
            if value > 0:
                found.add(value)
        elif isinstance(value, str) and value.isdigit():
            found.add(int(value))

    _walk(cfg if isinstance(cfg, Mapping) else {})
    return found


async def preflight_restore(
    guild: Any,
    plan: list[dict[str, Any]],
    *,
    delete_confirmed: bool = False,
) -> list[str]:
    """Validate the complete plan against a fresh fetch. Returns error
    strings; a non-empty list means the restore must not start (R4)."""

    errors: list[str] = []
    guild_id = _int(getattr(guild, "id", 0))
    fresh_channels = await fresh_channel_map(guild)
    fresh_roles = await _fresh_roles(guild)
    fresh_role_by_id = {_int(getattr(r, "id", 0)): r for r in fresh_roles}

    actionable = [
        row
        for row in plan
        if row.get("action") in ("create", "update") and row.get("status") == "changed"
    ]

    # Existence + drift for updates; continued absence for creates (R7's
    # plan-level twin).
    for row in actionable:
        kind = row["kind"]
        if kind == "guild_setting":
            continue
        if row["action"] == "update":
            target_id = _int(row.get("target_id"))
            obj = (
                fresh_role_by_id.get(target_id)
                if kind == "role"
                else fresh_channels.get(target_id)
            )
            if obj is None:
                errors.append(f"{row['key']}: the item from the preview no longer exists.")
                continue
            current = _serialize_one(kind, obj, guild_id)
            if current is None:
                errors.append(f"{row['key']}: the item changed type after the preview.")
                continue
            diffs = _differences_for(
                kind,
                _mapping(row.get("before")),
                current,
                everyone=(row["key"] == "role:@everyone"),
                include_position=False,
            )
            if diffs:
                errors.append(
                    f"{row['key']}: changed after the preview "
                    f"({', '.join(diffs)})."
                )
        else:  # create
            source_id = _int((_mapping(row.get("after"))).get("source_id"))
            if source_id > 0 and (
                source_id in fresh_role_by_id or source_id in fresh_channels
            ):
                errors.append(
                    f"{row['key']}: an item with the backup's original id "
                    "now exists."
                )

    # Limits (R4).
    channel_creates = [
        r for r in actionable if r["action"] == "create" and r["kind"] in ("channel", "category")
    ]
    if len(fresh_channels) + len(channel_creates) > DISCORD_CHANNEL_LIMIT:
        errors.append(
            f"The restore would exceed Discord's {DISCORD_CHANNEL_LIMIT}-channel limit."
        )
    role_creates = [
        r for r in actionable if r["action"] == "create" and r["kind"] == "role"
    ]
    if len(fresh_roles) + len(role_creates) > ROLE_LIMIT:
        errors.append(f"The restore would exceed Discord's {ROLE_LIMIT}-role limit.")
    cat_live_id_by_snap_key: dict[str, Optional[int]] = {}
    for row in plan:
        if row["kind"] == "category":
            cat_live_id_by_snap_key[row["key"]] = (
                _int(row.get("target_id")) if row.get("target_id") else None
            )
    children: dict[str, int] = {}
    for channel in fresh_channels.values():
        parent = getattr(channel, "category", None)
        parent_id = _int(getattr(parent, "id", 0)) if parent is not None else 0
        if parent_id > 0:
            bucket = f"cat:{parent_id}"
            children[bucket] = children.get(bucket, 0) + 1
    for row in channel_creates:
        if row["kind"] != "channel":
            continue
        snap_cat_key = (_mapping(row.get("after"))).get("category_key")
        if not snap_cat_key:
            continue
        live_id = cat_live_id_by_snap_key.get(_str(snap_cat_key))
        bucket = f"cat:{live_id}" if live_id else _str(snap_cat_key)
        children[bucket] = children.get(bucket, 0) + 1
        if children[bucket] > CATEGORY_CHILD_LIMIT:
            errors.append(
                f"{row['key']}: its category would exceed Discord's "
                f"{CATEGORY_CHILD_LIMIT}-channel limit."
            )

    # Name lengths.
    for row in actionable:
        if row["kind"] in ("role", "category", "channel"):
            name = _str((_mapping(row.get("after"))).get("name"))
            if len(name) > NAME_LIMIT:
                errors.append(f"{row['key']}: the name exceeds Discord's limit.")

    # Bot permissions + hierarchy (R3 re-checked against fresh state).
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None) if me is not None else None
    if any(r["kind"] in ("category", "channel") for r in actionable) and not (
        perms is not None and (perms.manage_channels or perms.administrator)
    ):
        errors.append("Dank Shield is missing Manage Channels for this restore.")
    if any(r["kind"] == "role" for r in actionable) and not (
        perms is not None and (perms.manage_roles or perms.administrator)
    ):
        errors.append("Dank Shield is missing Manage Roles for this restore.")
    bot_top = _bot_top_position(guild)
    if bot_top is not None:
        for row in actionable:
            if row["kind"] != "role":
                continue
            if row["action"] == "create":
                position = _int((_mapping(row.get("after"))).get("position"))
            else:
                live_role = fresh_role_by_id.get(_int(row.get("target_id")))
                position = _int(getattr(live_role, "position", 0)) if live_role else 0
            if position >= bot_top:
                errors.append(
                    f"{row['key']}: at or above Dank Shield's top role (R3)."
                )

    # Delete protection — only when deletes were actually confirmed; an
    # unconfirmed plan never deletes, so protected candidates are inert.
    if delete_confirmed:
        protected = await _config_protected_ids(guild)
        bot_role_ids = _bot_role_ids(guild)
        for row in plan:
            if row.get("action") != "delete_candidate":
                continue
            target_id = _int(row.get("target_id"))
            if target_id in protected:
                errors.append(
                    f"{row['key']}: Dank Shield configuration references this "
                    "item; a restore can never delete it (R1)."
                )
            elif row["kind"] == "role" and target_id in bot_role_ids:
                errors.append(
                    f"{row['key']}: this is one of Dank Shield's own roles; "
                    "a restore can never delete it (R1)."
                )
            elif row["kind"] == "role" and (
                _mapping(row.get("before")).get("is_default")
                or _mapping(row.get("before")).get("managed")
            ):
                errors.append(
                    f"{row['key']}: @everyone and managed roles can never be "
                    "deleted by a restore (R1)."
                )

    return errors


# ---------------------------------------------------------------------------
# Apply engine
# ---------------------------------------------------------------------------


@dataclass
class RestoreResult:
    """Design ``TransactionResult`` idiom, spec §1.7 result shape."""

    ok: bool
    applied: list[str] = field(default_factory=list)
    failure: str = ""
    compensated: list[str] = field(default_factory=list)
    residual: list[dict[str, Any]] = field(default_factory=list)
    delete_results: list[dict[str, Any]] = field(default_factory=list)
    skipped_deletes: list[str] = field(default_factory=list)
    dropped_overwrites: list[str] = field(default_factory=list)
    pre_restore_backup_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "applied": list(self.applied),
            "failure": self.failure,
            "compensated": list(self.compensated),
            "residual": [dict(row) for row in self.residual],
            "delete_results": [dict(row) for row in self.delete_results],
            "skipped_deletes": list(self.skipped_deletes),
            "dropped_overwrites": list(self.dropped_overwrites),
            "pre_restore_backup_id": self.pre_restore_backup_id,
        }


def _enum_value(enum_type: Any, value: Any) -> Any:
    try:
        return enum_type(_int(value))
    except Exception:
        return value


def _live_key_for(kind: str, obj: Any, guild_id: int) -> str:
    obj_id = _int(getattr(obj, "id", 0))
    if kind == "role":
        return "role:@everyone" if obj_id == guild_id else f"role:{obj_id}"
    if kind == "category":
        return f"cat:{obj_id}"
    return f"chan:{obj_id}"


def _role_kwargs(entry: Mapping[str, Any], *, everyone: bool = False) -> dict[str, Any]:
    if everyone:
        return {"permissions": discord.Permissions(_int(entry.get("permissions")))}
    return {
        "name": _str(entry.get("name")),
        "colour": discord.Colour(_int(entry.get("color"))),
        "hoist": bool(entry.get("hoist")),
        "mentionable": bool(entry.get("mentionable")),
        "permissions": discord.Permissions(_int(entry.get("permissions"))),
    }


class _ApplyRun:
    """Mutable state for one restore application: resolved objects, the
    snapshot-key -> live-object map overwrites resolve through (R8), and
    the compensation stack (R6)."""

    def __init__(self, guild: Any, *, user_id: int, delay_seconds: float) -> None:
        self.guild = guild
        self.guild_id = _int(getattr(guild, "id", 0))
        self.user_id = _int(user_id)
        self.delay = max(0.0, float(delay_seconds))
        self.reason = f"Dank Shield server structure restore by {self.user_id}"
        self.rollback_reason = (
            f"Dank Shield automatic rollback after failed restore by {self.user_id}"
        )
        self.resolved: dict[str, Any] = {}
        self.live_key_of: dict[str, str] = {}
        self.inverses: list[tuple[str, dict[str, Any], Callable[[], Awaitable[None]]]] = []
        self.dropped_overwrites: list[str] = []

    # -- object lookup ------------------------------------------------------
    def _find_role(self, role_id: int) -> Any:
        if role_id <= 0:
            return None
        get_role = getattr(self.guild, "get_role", None)
        if callable(get_role):
            try:
                found = get_role(role_id)
                if found is not None:
                    return found
            except Exception:
                pass
        for role in getattr(self.guild, "roles", None) or []:
            if _int(getattr(role, "id", 0)) == role_id:
                return role
        return None

    def _find_channel(self, channel_id: int) -> Any:
        if channel_id <= 0:
            return None
        get_channel = getattr(self.guild, "get_channel", None)
        if callable(get_channel):
            try:
                found = get_channel(channel_id)
                if found is not None:
                    return found
            except Exception:
                pass
        pools = list(getattr(self.guild, "channels", None) or []) + list(
            getattr(self.guild, "categories", None) or []
        )
        for channel in pools:
            if _int(getattr(channel, "id", 0)) == channel_id:
                return channel
        return None

    def _register(self, kind: str, key: str, obj: Any) -> None:
        self.resolved[key] = obj
        self.live_key_of[key] = _live_key_for(kind, obj, self.guild_id)

    def seed(self, plan: list[dict[str, Any]]) -> None:
        for row in plan:
            target_id = _int(row.get("target_id"))
            if target_id <= 0:
                continue
            obj = (
                self._find_role(target_id)
                if row["kind"] == "role"
                else self._find_channel(target_id)
            )
            if obj is not None:
                self._register(row["kind"], row["key"], obj)
        default_role = getattr(self.guild, "default_role", None) or self._find_role(
            self.guild_id
        )
        if default_role is not None:
            self.resolved.setdefault("role:@everyone", default_role)
            self.live_key_of["role:@everyone"] = "role:@everyone"

    # -- overwrites (R8) ------------------------------------------------------
    def _build_overwrites(self, rows: Any) -> dict[Any, Any]:
        built: dict[Any, Any] = {}
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            target_key = _str(row.get("target_key"))
            target = self.resolved.get(target_key)
            if target is None:
                if target_key not in self.dropped_overwrites:
                    self.dropped_overwrites.append(target_key)
                continue
            built[target] = _overwrite_from_snapshot(
                {
                    "allow": list(row.get("allow") or []),
                    "deny": list(row.get("deny") or []),
                }
            )
        return built

    # -- revalidation (R7) ------------------------------------------------------
    def revalidate(self, row: dict[str, Any]) -> str:
        kind = row["kind"]
        if row["action"] == "create":
            source_id = _int((_mapping(row.get("after"))).get("source_id"))
            if source_id > 0:
                found = (
                    self._find_role(source_id)
                    if kind == "role"
                    else self._find_channel(source_id)
                )
                if found is not None:
                    return (
                        f"{row['key']}: a {kind} with the backup's original "
                        "id appeared after the preview."
                    )
            return ""
        if kind == "guild_setting":
            field_name = row["key"].split(":", 1)[-1]
            current_block = backups._serialize_guild_block(self.guild, self.guild_id)
            if current_block.get(field_name) != (_mapping(row.get("before"))).get(field_name):
                return f"{row['key']}: the setting changed after the preview."
            return ""
        obj = self.resolved.get(row["key"])
        if obj is None:
            return f"{row['key']}: the target could not be found."
        current = _serialize_one(kind, obj, self.guild_id)
        if current is None:
            return f"{row['key']}: the target changed type after the preview."
        # Both sides are live-space here (the row's ``before`` was serialized
        # from live at plan time), so the key maps stay identity (None).
        diffs = _differences_for(
            kind,
            _mapping(row.get("before")),
            current,
            everyone=(row["key"] == "role:@everyone"),
            include_position=False,
        )
        if diffs:
            return (
                f"{row['key']}: changed after the preview "
                f"({', '.join(diffs)})."
            )
        return ""

    # -- per-kind execution ------------------------------------------------------
    async def _execute_role(self, row: dict[str, Any]) -> None:
        key = row["key"]
        after = _mapping(row.get("after"))
        everyone = key == "role:@everyone"
        if row["action"] == "create":
            created = await self.guild.create_role(
                **_role_kwargs(after), reason=self.reason
            )
            self._register("role", key, created)
            self.inverses.append(
                (key, row, lambda obj=created: obj.delete(reason=self.rollback_reason))
            )
            return
        obj = self.resolved[key]
        before = _mapping(row.get("before"))
        edited = await obj.edit(**_role_kwargs(after, everyone=everyone), reason=self.reason)
        if edited is not None:
            self._register("role", key, edited)
            obj = edited
        self.inverses.append(
            (
                key,
                row,
                lambda obj=obj, before=before, everyone=everyone: obj.edit(
                    **_role_kwargs(before, everyone=everyone),
                    reason=self.rollback_reason,
                ),
            )
        )

    async def _execute_category(self, row: dict[str, Any]) -> None:
        key = row["key"]
        after = _mapping(row.get("after"))
        if row["action"] == "create":
            created = await self.guild.create_category(
                _str(after.get("name")),
                overwrites=self._build_overwrites(after.get("overwrites")),
                position=_int(after.get("position")),
                reason=self.reason,
            )
            self._register("category", key, created)
            self.inverses.append(
                (key, row, lambda obj=created: obj.delete(reason=self.rollback_reason))
            )
            if bool(after.get("nsfw")):
                edited = await created.edit(nsfw=True, reason=self.reason)
                if edited is not None:
                    self._register("category", key, edited)
            return
        obj = self.resolved[key]
        before = _mapping(row.get("before"))
        edited = await obj.edit(
            name=_str(after.get("name")),
            position=_int(after.get("position")),
            nsfw=bool(after.get("nsfw")),
            overwrites=self._build_overwrites(after.get("overwrites")),
            reason=self.reason,
        )
        if edited is not None:
            self._register("category", key, edited)
            obj = edited
        self.inverses.append(
            (
                key,
                row,
                lambda obj=obj, before=before: obj.edit(
                    name=_str(before.get("name")),
                    position=_int(before.get("position")),
                    nsfw=bool(before.get("nsfw")),
                    overwrites=self._build_overwrites(before.get("overwrites")),
                    reason=self.rollback_reason,
                ),
            )
        )

    def _forum_tags(self, entry: Mapping[str, Any]) -> list:
        forum = _mapping(entry.get("forum"))
        tags = []
        for tag in forum.get("available_tags") or []:
            if not isinstance(tag, Mapping):
                continue
            emoji = tag.get("emoji")
            tags.append(
                discord.ForumTag(
                    name=_str(tag.get("name")),
                    moderated=bool(tag.get("moderated")),
                    emoji=emoji if emoji else None,
                )
            )
        return tags

    def _forum_kwargs(self, entry: Mapping[str, Any], *, creating: bool) -> dict[str, Any]:
        forum = _mapping(entry.get("forum"))
        kwargs: dict[str, Any] = {}
        if forum:
            kwargs["available_tags"] = self._forum_tags(entry)
            sort_order = forum.get("default_sort_order")
            if sort_order is not None:
                kwargs["default_sort_order"] = _enum_value(
                    discord.ForumOrderType, sort_order
                )
            layout = forum.get("default_forum_layout")
            if layout is not None:
                kwargs["default_layout"] = _enum_value(discord.ForumLayoutType, layout)
            kwargs["default_thread_slowmode_delay"] = _int(
                forum.get("default_thread_slowmode_delay")
            )
            reaction = forum.get("default_reaction_emoji")
            kwargs["default_reaction_emoji"] = reaction if reaction else None
            kwargs["default_auto_archive_duration"] = _int(
                forum.get("default_auto_archive_duration"), 1440
            )
        return kwargs

    async def _create_channel(self, entry: Mapping[str, Any]) -> Any:
        category_key = entry.get("category_key")
        category = self.resolved.get(_str(category_key)) if category_key else None
        common: dict[str, Any] = {
            "category": category,
            "position": _int(entry.get("position")),
            "overwrites": self._build_overwrites(entry.get("overwrites")),
            "reason": self.reason,
        }
        entry_type = _str(entry.get("type"))
        name = _str(entry.get("name"))
        if entry_type == "text":
            return await self.guild.create_text_channel(
                name,
                topic=entry.get("topic"),
                nsfw=bool(entry.get("nsfw")),
                slowmode_delay=_int(entry.get("slowmode_delay")),
                **common,
            )
        if entry_type == "news":
            return await self.guild.create_text_channel(
                name,
                news=True,
                topic=entry.get("topic"),
                nsfw=bool(entry.get("nsfw")),
                slowmode_delay=_int(entry.get("slowmode_delay")),
                **common,
            )
        if entry_type == "voice":
            voice_kwargs: dict[str, Any] = {"nsfw": bool(entry.get("nsfw"))}
            if entry.get("bitrate") is not None:
                voice_kwargs["bitrate"] = _int(entry.get("bitrate"))
            if entry.get("user_limit") is not None:
                voice_kwargs["user_limit"] = _int(entry.get("user_limit"))
            if entry.get("rtc_region"):
                voice_kwargs["rtc_region"] = _str(entry.get("rtc_region"))
            if entry.get("video_quality_mode") is not None:
                voice_kwargs["video_quality_mode"] = _enum_value(
                    discord.VideoQualityMode, entry.get("video_quality_mode")
                )
            return await self.guild.create_voice_channel(name, **voice_kwargs, **common)
        if entry_type == "forum":
            return await self.guild.create_forum(
                name,
                topic=entry.get("topic"),
                nsfw=bool(entry.get("nsfw")),
                slowmode_delay=_int(entry.get("slowmode_delay")),
                **self._forum_kwargs(entry, creating=True),
                **common,
            )
        raise RuntimeError(f"unsupported channel type for restore: {entry_type!r}")

    def _channel_edit_kwargs(
        self, entry: Mapping[str, Any], current_type: str
    ) -> dict[str, Any]:
        entry_type = _str(entry.get("type"))
        category_key = entry.get("category_key")
        kwargs: dict[str, Any] = {
            "name": _str(entry.get("name")),
            "position": _int(entry.get("position")),
            "category": self.resolved.get(_str(category_key)) if category_key else None,
            "overwrites": self._build_overwrites(entry.get("overwrites")),
            "reason": self.reason,
        }
        if entry_type in ("text", "news", "forum"):
            kwargs["topic"] = entry.get("topic")
            kwargs["slowmode_delay"] = _int(entry.get("slowmode_delay"))
        if entry_type in ("text", "news", "voice", "forum"):
            kwargs["nsfw"] = bool(entry.get("nsfw"))
        if entry_type == "voice":
            if entry.get("bitrate") is not None:
                kwargs["bitrate"] = _int(entry.get("bitrate"))
            if entry.get("user_limit") is not None:
                kwargs["user_limit"] = _int(entry.get("user_limit"))
            kwargs["rtc_region"] = entry.get("rtc_region")
            if entry.get("video_quality_mode") is not None:
                kwargs["video_quality_mode"] = _enum_value(
                    discord.VideoQualityMode, entry.get("video_quality_mode")
                )
        if entry_type == "forum":
            kwargs.update(self._forum_kwargs(entry, creating=False))
        if entry_type != current_type and {entry_type, current_type} == {"text", "news"}:
            kwargs["type"] = (
                discord.ChannelType.news
                if entry_type == "news"
                else discord.ChannelType.text
            )
        return kwargs

    async def _execute_channel(self, row: dict[str, Any]) -> None:
        key = row["key"]
        after = _mapping(row.get("after"))
        if row["action"] == "create":
            created = await self._create_channel(after)
            self._register("channel", key, created)
            self.inverses.append(
                (key, row, lambda obj=created: obj.delete(reason=self.rollback_reason))
            )
            return
        obj = self.resolved[key]
        before = _mapping(row.get("before"))
        current = _serialize_one("channel", obj, self.guild_id)
        current_type = _str((current or {}).get("type"))
        edited = await obj.edit(**self._channel_edit_kwargs(after, current_type))
        if edited is not None:
            self._register("channel", key, edited)
            obj = edited
        self.inverses.append(
            (
                key,
                row,
                lambda obj=obj, before=before, current_type=current_type: obj.edit(
                    **self._channel_edit_kwargs(before, current_type)
                ),
            )
        )

    def _setting_kwargs(
        self, entry: Mapping[str, Any], *, snapshot_space: bool
    ) -> dict[str, Any]:
        field_name, value = next(iter(entry.items()))
        if field_name in _REF_SETTING_PARAMS:
            param = _REF_SETTING_PARAMS[field_name]
            if value is None:
                return {param: None}
            if snapshot_space:
                target = self.resolved.get(_str(value))
                if target is None:
                    raise RuntimeError(
                        f"Setting {field_name} references {value}, which the "
                        "restore did not resolve."
                    )
                return {param: target}
            channel_id = _int(str(value).split(":", 1)[-1])
            return {param: self._find_channel(channel_id)}
        if field_name in _SETTING_ENUMS:
            return {field_name: _enum_value(_SETTING_ENUMS[field_name], value)}
        if field_name == "preferred_locale":
            return {field_name: _enum_value(discord.Locale, value)}
        if field_name == "afk_timeout":
            return {field_name: _int(value)}
        return {field_name: value}

    async def _execute_setting(self, row: dict[str, Any]) -> None:
        after = _mapping(row.get("after"))
        before = _mapping(row.get("before"))
        await self.guild.edit(
            **self._setting_kwargs(after, snapshot_space=True), reason=self.reason
        )
        self.inverses.append(
            (
                row["key"],
                row,
                lambda before=before: self.guild.edit(
                    **self._setting_kwargs(before, snapshot_space=False),
                    reason=self.rollback_reason,
                ),
            )
        )

    async def execute(self, row: dict[str, Any]) -> None:
        if row["kind"] == "role":
            await self._execute_role(row)
        elif row["kind"] == "category":
            await self._execute_category(row)
        elif row["kind"] == "channel":
            await self._execute_channel(row)
        else:
            await self._execute_setting(row)

    async def role_positions_pass(self, applied_role_rows: list[dict[str, Any]]) -> None:
        """One final ``edit_role_positions`` pass over successfully applied
        roles (Discord recomputes positions on every create, so per-row
        positions cannot be trusted mid-run)."""

        positions: dict[Any, int] = {}
        for row in applied_role_rows:
            if row["key"] == "role:@everyone":
                continue
            obj = self.resolved.get(row["key"])
            if obj is None:
                continue
            wanted = _int((_mapping(row.get("after"))).get("position"))
            if _int(getattr(obj, "position", 0)) != wanted:
                positions[obj] = wanted
        if positions:
            await self.guild.edit_role_positions(positions, reason=self.reason)

    async def compensate(self) -> tuple[list[str], list[dict[str, Any]]]:
        compensated: list[str] = []
        residual: list[dict[str, Any]] = []
        for key, row, undo in reversed(self.inverses):
            try:
                await undo()
                compensated.append(key)
            except Exception:
                residual.append(row)
            if self.delay > 0:
                await asyncio.sleep(self.delay)
        return compensated, residual

    async def execute_delete(self, row: dict[str, Any]) -> dict[str, Any]:
        target_id = _int(row.get("target_id"))
        obj = (
            self._find_role(target_id)
            if row["kind"] == "role"
            else self._find_channel(target_id)
        )
        if obj is None:
            return {
                "key": row["key"],
                "target_id": row.get("target_id"),
                "ok": True,
                "error": "already gone",
            }
        try:
            await obj.delete(reason=self.reason)
            return {
                "key": row["key"],
                "target_id": row.get("target_id"),
                "ok": True,
                "error": "",
            }
        except Exception as exc:
            return {
                "key": row["key"],
                "target_id": row.get("target_id"),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }


async def _cancelled(cancel_check: Optional[CancelCheck]) -> bool:
    if cancel_check is None:
        return False
    try:
        value = cancel_check()
        if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
            value = await value
        return bool(value)
    except Exception:
        return False


async def apply_restore_plan(
    guild: Any,
    plan: list[dict[str, Any]],
    *,
    user_id: int,
    backup_id: int,
    progress: Optional[ProgressCallback] = None,
    delete_confirmed: bool = False,
    plan_mode: str = "all",
    cancel_check: Optional[CancelCheck] = None,
    delay_seconds: Optional[float] = None,
) -> RestoreResult:
    """Apply a confirmed restore plan under the guild mutation lock.

    Deletes run only when ``delete_confirmed`` is true — there is no other
    path from a plan to a delete. The pre-restore auto-backup (R5) and the
    ``restore_state`` cursor (§1.8) are written through Slice 1's service.
    """

    guild_id = _int(getattr(guild, "id", 0))
    delay = throttle.DEFAULT_DELAY_SECONDS if delay_seconds is None else float(delay_seconds)
    lock = throttle.guild_lock(guild_id)
    if lock.locked():
        return RestoreResult(
            ok=False,
            failure="Another channel mutation batch is already running for this server.",
        )
    async with lock:
        return await _apply_locked(
            guild,
            plan,
            user_id=user_id,
            backup_id=backup_id,
            progress=progress,
            delete_confirmed=delete_confirmed,
            plan_mode=plan_mode,
            cancel_check=cancel_check,
            delay_seconds=delay,
        )


async def _apply_locked(
    guild: Any,
    plan: list[dict[str, Any]],
    *,
    user_id: int,
    backup_id: int,
    progress: Optional[ProgressCallback],
    delete_confirmed: bool,
    plan_mode: str,
    cancel_check: Optional[CancelCheck],
    delay_seconds: float,
) -> RestoreResult:
    guild_id = _int(getattr(guild, "id", 0))
    main_rows = [
        row
        for row in plan
        if row.get("action") in ("create", "update") and row.get("status") == "changed"
    ]
    delete_rows = [row for row in plan if row.get("action") == "delete_candidate"]
    skipped_delete_keys = [row["key"] for row in delete_rows]

    # One in-flight restore per guild (§1.8): another backup's running
    # cursor blocks a second restore; this backup's own cursor is a resume.
    try:
        existing = await asyncio.to_thread(
            backups.list_backups_sync, guild_id, limit=backups.MAX_BACKUPS_PER_GUILD
        )
        for other in existing:
            if _int(other.get("backup_id")) == _int(backup_id):
                continue
            state = other.get("restore_state")
            if isinstance(state, Mapping) and state.get("status") == "running":
                return RestoreResult(
                    ok=False,
                    failure="Another restore is already in flight for this server.",
                    skipped_deletes=skipped_delete_keys,
                )
    except Exception:
        logger.warning(
            "restore in-flight check failed for guild %s", guild_id, exc_info=True
        )

    state: dict[str, Any] = {
        "status": "running",
        "plan_mode": plan_mode,
        "started_by": str(_int(user_id)),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "applied_keys": [],
        "failed_key": None,
        "delete_confirmed": bool(delete_confirmed),
    }

    errors = await preflight_restore(guild, plan, delete_confirmed=delete_confirmed)
    if errors:
        state["status"] = "failed"
        state["failure"] = "; ".join(errors[:3])
        await backups.update_restore_state(guild_id, backup_id, state)
        return RestoreResult(
            ok=False,
            failure="; ".join(errors[:3]),
            skipped_deletes=skipped_delete_keys,
        )

    await backups.update_restore_state(guild_id, backup_id, state)

    # R5: the restore does not start unless the pre-restore backup lands.
    try:
        pre_restore = await backups.create_backup(
            guild, created_by=_int(user_id), label=PRE_RESTORE_LABEL
        )
        pre_restore_backup_id = _int(pre_restore.get("backup_id"))
    except Exception as exc:
        state["status"] = "failed"
        state["failure"] = f"Pre-restore auto-backup failed: {type(exc).__name__}"
        await backups.update_restore_state(guild_id, backup_id, state)
        return RestoreResult(
            ok=False,
            failure=(
                "The pre-restore auto-backup failed "
                f"({type(exc).__name__}), so the restore did not start."
            ),
            skipped_deletes=skipped_delete_keys,
        )

    run = _ApplyRun(guild, user_id=user_id, delay_seconds=delay_seconds)
    run.seed(plan)

    applied_keys: list[str] = []
    failure = ""
    failed_key: Optional[str] = None
    positions_done = False
    index = 0
    while index < len(main_rows):
        row = main_rows[index]
        if await _cancelled(cancel_check):
            state["status"] = "cancelled"
            state["applied_keys"] = list(applied_keys)
            await backups.update_restore_state(guild_id, backup_id, state)
            return RestoreResult(
                ok=False,
                applied=applied_keys,
                failure="Restore cancelled by the operator; applied rows stay "
                "in place and the restore can be resumed.",
                skipped_deletes=skipped_delete_keys,
                dropped_overwrites=list(run.dropped_overwrites),
                pre_restore_backup_id=pre_restore_backup_id,
            )
        drift = run.revalidate(row)
        if drift:
            failure, failed_key = drift, row["key"]
            break
        try:
            await run.execute(row)
        except Exception as exc:
            failure = f"{row['key']}: Discord rejected the change ({type(exc).__name__}: {exc})."
            failed_key = row["key"]
            break
        applied_keys.append(row["key"])
        state["applied_keys"] = list(applied_keys)
        await backups.update_restore_state(guild_id, backup_id, state)
        if progress is not None:
            try:
                await progress(len(applied_keys), len(main_rows))
            except Exception:
                logger.warning("restore progress callback failed", exc_info=True)
        if row["kind"] == "role" and not positions_done:
            following = main_rows[index + 1] if index + 1 < len(main_rows) else None
            if following is None or following["kind"] != "role":
                positions_done = True
                try:
                    await run.role_positions_pass(
                        [
                            r
                            for r in main_rows[: index + 1]
                            if r["kind"] == "role" and r["key"] in applied_keys
                        ]
                    )
                except Exception as exc:
                    failure = (
                        "Role position pass failed "
                        f"({type(exc).__name__}: {exc})."
                    )
                    failed_key = None
                    break
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        index += 1

    if failure:
        compensated, residual = await run.compensate()
        residual_keys = {row["key"] for row in residual}
        state["status"] = "failed"
        state["failed_key"] = failed_key
        state["failure"] = failure
        state["applied_keys"] = [k for k in applied_keys if k in residual_keys]
        await backups.update_restore_state(guild_id, backup_id, state)
        return RestoreResult(
            ok=False,
            applied=applied_keys,
            failure=failure,
            compensated=compensated,
            residual=residual,
            skipped_deletes=skipped_delete_keys,
            dropped_overwrites=list(run.dropped_overwrites),
            pre_restore_backup_id=pre_restore_backup_id,
        )

    delete_results: list[dict[str, Any]] = []
    if delete_rows and delete_confirmed:
        for row in delete_rows:
            delete_results.append(await run.execute_delete(row))
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        skipped_delete_keys = []

    state["status"] = "done"
    state["applied_keys"] = list(applied_keys)
    state["delete_results"] = delete_results
    await backups.update_restore_state(guild_id, backup_id, state)
    return RestoreResult(
        ok=True,
        applied=applied_keys,
        delete_results=delete_results,
        skipped_deletes=skipped_delete_keys,
        dropped_overwrites=list(run.dropped_overwrites),
        pre_restore_backup_id=pre_restore_backup_id,
    )


__all__ = [
    "PRE_RESTORE_LABEL",
    "ROLE_LIMIT",
    "RestoreResult",
    "apply_restore_plan",
    "build_restore_plan",
    "preflight_restore",
]

