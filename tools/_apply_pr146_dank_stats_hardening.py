from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


root = Path(__file__).resolve().parents[1]
stats_path = root / "stoney_verify" / "security_stats.py"
events_path = root / "stoney_verify" / "ticket_events.py"
test_path = root / "tests" / "test_dank_stats_authority.py"

stats = stats_path.read_text(encoding="utf-8")

stats = replace_once(
    stats,
    "_LAST_REFRESH_AT: Dict[int, float] = {}\n",
    "_LAST_REFRESH_AT: Dict[int, float] = {}\n"
    "_TICKET_STATS_PAGE_SIZE = 500\n"
    "_TICKET_STATS_SELECT_COLUMNS: Optional[str] = None\n"
    "_LAST_SPAM_GUARD_ENABLED: Dict[int, bool] = {}\n"
    "_LAST_TICKET_QUERY_ERROR: Dict[int, str] = {}\n",
    "stats runtime state",
)

stats = replace_once(
    stats,
    '''def _normalize_ticket_status_counts(value: Any) -> Optional[Dict[str, int]]:\n    if value is None:\n        return None\n    raw = _mapping(value)\n    return {\n        key: max(0, _safe_int(raw.get(key), 0))\n        for key in DEFAULT_TICKET_STATUS_COUNTS\n    }\n''',
    '''def _normalize_ticket_status_counts(value: Any) -> Optional[Dict[str, int]]:\n    if value is None:\n        return None\n    raw = _mapping(value)\n    normalized = {\n        key: max(0, _safe_int(raw.get(key), 0))\n        for key in DEFAULT_TICKET_STATUS_COUNTS\n    }\n    # A claimed ticket is still active. Never publish the impossible state where\n    # the claimed subset is larger than the active/open total.\n    normalized["open_tickets"] = max(\n        normalized["open_tickets"],\n        normalized["claimed_tickets"],\n    )\n    return normalized\n''',
    "ticket count normalization",
)

stats = replace_once(
    stats,
    '''def _query_ticket_status_counts_sync(guild_id: int) -> Optional[Dict[str, int]]:\n    """Read current ticket lifecycle totals from the canonical tickets table."""\n    sb = get_supabase()\n    if sb is None:\n        return None\n\n    response = (\n        sb.table("tickets")\n        .select("status,claimed_by,assigned_to")\n        .eq("guild_id", str(int(guild_id)))\n        .execute()\n    )\n    rows = getattr(response, "data", None)\n    if rows is None:\n        return None\n\n    return _ticket_status_counts_from_rows(rows)\n\n\nasync def _ticket_status_counts(guild_id: int) -> Optional[Dict[str, int]]:\n    try:\n        counts = await asyncio.to_thread(_query_ticket_status_counts_sync, int(guild_id))\n        return _normalize_ticket_status_counts(counts)\n    except Exception:\n        return None\n''',
    '''def _ticket_stats_schema_error(exc: BaseException) -> bool:\n    text = repr(exc or "").lower()\n    return any(\n        marker in text\n        for marker in (\n            "pgrst204",\n            "schema cache",\n            "does not exist",\n            "undefined column",\n            "column",\n        )\n    ) and any(name in text for name in ("claimed_by", "assigned_to"))\n\n\ndef _ticket_stats_select_candidates() -> tuple[str, ...]:\n    preferred = (\n        "status,claimed_by,assigned_to",\n        "status,claimed_by",\n        "status,assigned_to",\n        "status",\n    )\n    cached = str(_TICKET_STATS_SELECT_COLUMNS or "").strip()\n    if not cached or cached not in preferred:\n        return preferred\n    return (cached, *tuple(item for item in preferred if item != cached))\n\n\ndef _query_ticket_status_counts_sync(guild_id: int) -> Optional[Dict[str, int]]:\n    """Read every ticket lifecycle row with schema-compatible pagination."""\n    global _TICKET_STATS_SELECT_COLUMNS\n\n    sb = get_supabase()\n    if sb is None:\n        return None\n\n    gid = str(int(guild_id))\n    page_size = max(1, int(_TICKET_STATS_PAGE_SIZE))\n    last_schema_error: Optional[BaseException] = None\n\n    for columns in _ticket_stats_select_candidates():\n        rows: list[Dict[str, Any]] = []\n        try:\n            for page_index in range(200):\n                start = page_index * page_size\n                end = start + page_size - 1\n                response = (\n                    sb.table("tickets")\n                    .select(columns)\n                    .eq("guild_id", gid)\n                    .range(start, end)\n                    .execute()\n                )\n                raw_page = getattr(response, "data", None)\n                if raw_page is None:\n                    return None\n                page = [dict(row) for row in list(raw_page or []) if isinstance(row, Mapping)]\n                rows.extend(page)\n                if len(page) < page_size:\n                    break\n            else:\n                print(\n                    f"⚠️ security_stats ticket query page cap reached guild={gid} "\n                    f"rows={len(rows)}"\n                )\n\n            previous = _TICKET_STATS_SELECT_COLUMNS\n            _TICKET_STATS_SELECT_COLUMNS = columns\n            if previous != columns and columns != "status,claimed_by,assigned_to":\n                print(\n                    f"⚠️ security_stats ticket query compatibility mode "\n                    f"columns={columns}"\n                )\n            return _ticket_status_counts_from_rows(rows)\n        except Exception as exc:\n            if _ticket_stats_schema_error(exc):\n                last_schema_error = exc\n                if _TICKET_STATS_SELECT_COLUMNS == columns:\n                    _TICKET_STATS_SELECT_COLUMNS = None\n                continue\n            raise\n\n    if last_schema_error is not None:\n        raise last_schema_error\n    return None\n\n\nasync def _ticket_status_counts(guild_id: int) -> Optional[Dict[str, int]]:\n    gid = int(guild_id)\n    try:\n        counts = await asyncio.to_thread(_query_ticket_status_counts_sync, gid)\n        _LAST_TICKET_QUERY_ERROR.pop(gid, None)\n        return _normalize_ticket_status_counts(counts)\n    except Exception as exc:\n        marker = f"{type(exc).__name__}: {str(exc)[:240]}"\n        if _LAST_TICKET_QUERY_ERROR.get(gid) != marker:\n            _LAST_TICKET_QUERY_ERROR[gid] = marker\n            print(f"⚠️ security_stats ticket query failed guild={gid} error={marker}")\n        return None\n''',
    "ticket stats query",
)

stats = replace_once(
    stats,
    '''async def _spam_guard_enabled(guild_id: int) -> bool:\n    try:\n        from .spam_guard import get_spam_settings\n\n        spam_settings = await get_spam_settings(int(guild_id))\n        return bool(spam_settings.get("enabled"))\n    except Exception:\n        return False\n''',
    '''async def _spam_guard_enabled(guild_id: int) -> Optional[bool]:\n    gid = int(guild_id)\n    try:\n        from .spam_guard import get_spam_settings\n\n        spam_settings = await get_spam_settings(gid)\n        enabled = bool(spam_settings.get("enabled"))\n        _LAST_SPAM_GUARD_ENABLED[gid] = enabled\n        return enabled\n    except Exception as exc:\n        cached = _LAST_SPAM_GUARD_ENABLED.get(gid)\n        print(\n            f"⚠️ security_stats SpamGuard state read failed guild={gid} "\n            f"using={'cached' if cached is not None else 'unknown'} "\n            f"error={type(exc).__name__}"\n        )\n        return cached\n''',
    "spam guard status",
)

stats = replace_once(
    stats,
    "    spam_guard_enabled: bool,\n",
    "    spam_guard_enabled: Optional[bool],\n",
    "display signature",
)

stats = replace_once(
    stats,
    '''    return {\n        "status": f"🛡️ SpamGuard: {'ONLINE' if spam_guard_enabled else 'OFFLINE'}",\n''',
    '''    spam_status = (\n        "ONLINE" if spam_guard_enabled is True\n        else "OFFLINE" if spam_guard_enabled is False\n        else "UNKNOWN"\n    )\n    return {\n        "status": f"🛡️ SpamGuard: {spam_status}",\n''',
    "display status label",
)

stats = replace_once(
    stats,
    '''async def _display_names_for_guild(\n    guild: discord.Guild,\n    *,\n    counts: Mapping[str, int],\n) -> Dict[str, str]:\n    gid = int(guild.id)\n    spam_enabled, ticket_counts = await asyncio.gather(\n        _spam_guard_enabled(gid),\n        _ticket_status_counts(gid),\n    )\n    return _display_names(\n        spam_guard_enabled=bool(spam_enabled),\n        counts=counts,\n        member_count=_guild_member_count(guild),\n        ticket_counts=ticket_counts,\n    )\n''',
    '''def _live_open_ticket_count(guild: discord.Guild) -> Optional[int]:\n    """Count visible active ticket channels as a floor against false DB zeroes."""\n    try:\n        channels = list(getattr(guild, "text_channels", []) or [])\n    except Exception:\n        return None\n\n    active = 0\n    for channel in channels:\n        try:\n            name = str(getattr(channel, "name", "") or "").strip().lower()\n            topic = str(getattr(channel, "topic", "") or "").strip().lower()\n            category_name = str(\n                getattr(getattr(channel, "category", None), "name", "") or ""\n            ).strip().lower()\n\n            ticketish = (\n                name.startswith("ticket-")\n                or name.startswith("closed-")\n                or (\n                    "ticket_number=" in topic\n                    and any(owner_key in topic for owner_key in ("owner_id=", "user_id=", "requester_id="))\n                )\n            )\n            if not ticketish:\n                continue\n\n            closed = (\n                name.startswith("closed-")\n                or "archive" in category_name\n                or "archived" in category_name\n                or "closed ticket" in category_name\n            )\n            if not closed:\n                active += 1\n        except Exception:\n            continue\n    return active\n\n\nasync def _display_names_for_guild(\n    guild: discord.Guild,\n    *,\n    counts: Mapping[str, int],\n) -> Dict[str, str]:\n    gid = int(guild.id)\n    spam_enabled, ticket_counts = await asyncio.gather(\n        _spam_guard_enabled(gid),\n        _ticket_status_counts(gid),\n    )\n\n    tickets = _normalize_ticket_status_counts(ticket_counts)\n    live_open = _live_open_ticket_count(guild)\n    if tickets is not None:\n        db_open = int(tickets["open_tickets"])\n        tickets["open_tickets"] = max(\n            db_open,\n            int(tickets["claimed_tickets"]),\n            int(live_open or 0),\n        )\n        if live_open is not None and live_open > db_open:\n            print(\n                f"⚠️ security_stats active ticket mismatch guild={gid} "\n                f"db_open={db_open} live_channels={live_open}; using live floor"\n            )\n\n    return _display_names(\n        spam_guard_enabled=spam_enabled,\n        counts=counts,\n        member_count=_guild_member_count(guild),\n        ticket_counts=tickets,\n    )\n''',
    "guild display names",
)

stats = replace_once(
    stats,
    '''            except (discord.Forbidden, discord.HTTPException):\n                continue\n''',
    '''            except (discord.Forbidden, discord.HTTPException) as exc:\n                print(\n                    f"⚠️ security_stats channel refresh failed guild={gid} "\n                    f"key={key} error={type(exc).__name__}"\n                )\n                continue\n''',
    "channel refresh diagnostics",
)

stats = replace_once(
    stats,
    "    return changed\n\n\nasync def refresh_ticket_stats_for_guild_id",
    "    return True\n\n\nasync def refresh_ticket_stats_for_guild_id",
    "refresh success semantics",
)

stats_path.write_text(stats, encoding="utf-8")

events = events_path.read_text(encoding="utf-8")
events = replace_once(
    events,
    '''        deleted_ok = await repo_mark_ticket_deleted(\n            channel_id=channel.id,\n            deleted_by=None,\n            deleted_by_name="System",\n            reason="Channel deleted event",\n        )\n        return bool(deleted_ok)\n''',
    '''        deleted_ok = await repo_mark_ticket_deleted(\n            channel_id=channel.id,\n            deleted_by=None,\n            deleted_by_name="System",\n            reason="Channel deleted event",\n        )\n        if deleted_ok:\n            try:\n                from .security_stats import refresh_ticket_stats_for_guild_id\n\n                await refresh_ticket_stats_for_guild_id(int(channel.guild.id))\n            except Exception as exc:\n                _debug(\n                    f"external-delete stats refresh failed channel={channel.id} "\n                    f"error={type(exc).__name__}"\n                )\n        return bool(deleted_ok)\n''',
    "external delete refresh",
)
events_path.write_text(events, encoding="utf-8")

test_path.write_text(
    '''from __future__ import annotations\n\nimport asyncio\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom stoney_verify import security_stats, ticket_events\nfrom stoney_verify import spam_guard\n\n\nclass FakeTicketChannel:\n    def __init__(self, name: str, *, topic: str = "", category_name: str = "ACTIVE TICKETS") -> None:\n        self.name = name\n        self.topic = topic\n        self.category = SimpleNamespace(name=category_name)\n\n\ndef test_claimed_subset_can_never_exceed_open_total() -> None:\n    assert security_stats._normalize_ticket_status_counts(\n        {\n            "open_tickets": 0,\n            "claimed_tickets": 1,\n            "closed_tickets": 4,\n        }\n    ) == {\n        "open_tickets": 1,\n        "claimed_tickets": 1,\n        "closed_tickets": 4,\n    }\n\n\ndef test_live_ticket_channel_floor_prevents_false_open_zero(monkeypatch: pytest.MonkeyPatch) -> None:\n    guild = SimpleNamespace(\n        id=777,\n        member_count=25,\n        text_channels=[\n            FakeTicketChannel(\n                "custom-ticket-name",\n                topic="owner_id=55;category=support;ticket_number=42",\n            )\n        ],\n    )\n\n    async def fake_spam(_guild_id: int):\n        return True\n\n    async def fake_tickets(_guild_id: int):\n        return {\n            "open_tickets": 0,\n            "claimed_tickets": 0,\n            "closed_tickets": 12,\n        }\n\n    monkeypatch.setattr(security_stats, "_spam_guard_enabled", fake_spam)\n    monkeypatch.setattr(security_stats, "_ticket_status_counts", fake_tickets)\n\n    names = asyncio.run(security_stats._display_names_for_guild(guild, counts={}))\n\n    assert names["open_tickets"] == "🎫 Open Tickets: 1"\n    assert names["closed_tickets"] == "✅ Closed Tickets: 12"\n\n\ndef test_ticket_query_paginates_and_falls_back_for_schema_drift(monkeypatch: pytest.MonkeyPatch) -> None:\n    security_stats._TICKET_STATS_SELECT_COLUMNS = None\n    monkeypatch.setattr(security_stats, "_TICKET_STATS_PAGE_SIZE", 2)\n\n    rows = [\n        {"status": "claimed", "claimed_by": "55"},\n        {"status": "open", "claimed_by": None},\n        {"status": "closed", "claimed_by": None},\n    ]\n    selected: list[str] = []\n    ranges: list[tuple[int, int]] = []\n\n    class FakeQuery:\n        def __init__(self) -> None:\n            self.columns = ""\n            self.start = 0\n            self.end = 0\n\n        def select(self, columns: str):\n            self.columns = columns\n            selected.append(columns)\n            return self\n\n        def eq(self, key: str, value: str):\n            assert key == "guild_id"\n            assert value == "777"\n            return self\n\n        def range(self, start: int, end: int):\n            self.start = start\n            self.end = end\n            ranges.append((start, end))\n            return self\n\n        def execute(self):\n            if self.columns == "status,claimed_by,assigned_to":\n                raise RuntimeError("PGRST204: assigned_to column does not exist")\n            return SimpleNamespace(data=rows[self.start : self.end + 1])\n\n    class FakeSupabase:\n        def table(self, name: str):\n            assert name == "tickets"\n            return FakeQuery()\n\n    monkeypatch.setattr(security_stats, "get_supabase", lambda: FakeSupabase())\n\n    assert security_stats._query_ticket_status_counts_sync(777) == {\n        "open_tickets": 2,\n        "claimed_tickets": 1,\n        "closed_tickets": 1,\n    }\n    assert selected[0] == "status,claimed_by,assigned_to"\n    assert "status,claimed_by" in selected\n    assert (0, 1) in ranges\n    assert (2, 3) in ranges\n\n\ndef test_spam_guard_read_failure_reuses_last_known_truth(monkeypatch: pytest.MonkeyPatch) -> None:\n    security_stats._LAST_SPAM_GUARD_ENABLED.clear()\n    calls = 0\n\n    async def fake_get_spam_settings(_guild_id: int):\n        nonlocal calls\n        calls += 1\n        if calls == 1:\n            return {"enabled": True}\n        raise RuntimeError("temporary database outage")\n\n    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)\n\n    assert asyncio.run(security_stats._spam_guard_enabled(123)) is True\n    assert asyncio.run(security_stats._spam_guard_enabled(123)) is True\n\n\ndef test_external_ticket_channel_delete_forces_stats_refresh(monkeypatch: pytest.MonkeyPatch) -> None:\n    refreshed: list[int] = []\n\n    async def fake_find(_channel_id: int):\n        return {"status": "open"}\n\n    async def fake_close(**_kwargs):\n        return True\n\n    async def fake_delete(**_kwargs):\n        return True\n\n    async def fake_refresh(guild_id: int):\n        refreshed.append(guild_id)\n        return True\n\n    monkeypatch.setattr(ticket_events, "_find_ticket_row_by_channel_id", fake_find)\n    monkeypatch.setattr(ticket_events, "repo_mark_ticket_closed", fake_close)\n    monkeypatch.setattr(ticket_events, "repo_mark_ticket_deleted", fake_delete)\n    monkeypatch.setattr(security_stats, "refresh_ticket_stats_for_guild_id", fake_refresh)\n\n    channel = SimpleNamespace(id=999, guild=SimpleNamespace(id=777))\n    assert asyncio.run(ticket_events._mark_deleted_after_external_channel_delete(channel)) is True\n    assert refreshed == [777]\n\n\ndef test_stats_source_has_no_single_page_or_silent_false_offline_regression() -> None:\n    source = (\n        __import__("pathlib").Path("stoney_verify/security_stats.py")\n        .read_text(encoding="utf-8")\n    )\n    assert ".range(start, end)" in source\n    assert "using={'cached' if cached is not None else 'unknown'}" in source\n    assert "spam_guard_enabled=bool(spam_enabled)" not in source\n''',
    encoding="utf-8",
)

print("Applied PR #146 Dank Stats hardening")
