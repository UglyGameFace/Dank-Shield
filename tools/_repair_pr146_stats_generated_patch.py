from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


root = Path(__file__).resolve().parents[1]
stats_path = root / "stoney_verify" / "security_stats.py"
test_path = root / "tests" / "test_dank_stats_authority.py"

stats = stats_path.read_text(encoding="utf-8")
stats = replace_once(
    stats,
    '''                response = (\n                    sb.table("tickets")\n                    .select(columns)\n                    .eq("guild_id", gid)\n                    .range(start, end)\n                    .execute()\n                )\n''',
    '''                query = (\n                    sb.table("tickets")\n                    .select(columns)\n                    .eq("guild_id", gid)\n                )\n                range_method = getattr(query, "range", None)\n                if callable(range_method):\n                    response = range_method(start, end).execute()\n                else:\n                    # Compatibility with older/minimal PostgREST clients. These\n                    # clients can still provide an authoritative single response,\n                    # but cannot be paged by the caller.\n                    if page_index > 0:\n                        break\n                    response = query.execute()\n''',
    "PostgREST pagination compatibility",
)
stats_path.write_text(stats, encoding="utf-8")

test = test_path.read_text(encoding="utf-8")
test = replace_once(
    test,
    '''    security_stats._TICKET_STATS_SELECT_COLUMNS = None\n    monkeypatch.setattr(security_stats, "_TICKET_STATS_PAGE_SIZE", 2)\n''',
    '''    monkeypatch.setattr(security_stats, "_TICKET_STATS_SELECT_COLUMNS", None)\n    monkeypatch.setattr(security_stats, "_TICKET_STATS_PAGE_SIZE", 2)\n''',
    "selector cache test isolation",
)
test_path.write_text(test, encoding="utf-8")

print("Repaired generated PR #146 Dank Stats patch")
