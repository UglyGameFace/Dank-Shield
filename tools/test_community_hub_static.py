from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "stoney_verify" / "commands_ext" / "public_community_hub.py"
RUNTIME = ROOT / "stoney_verify" / "community_hub_runtime.py"
SERVICE = ROOT / "stoney_verify" / "community_hub_service.py"
MIGRATION = ROOT / "supabase" / "migrations" / "20260925193000_community_hub.sql"
MATCHMAKING_MIGRATION = ROOT / "supabase" / "migrations" / "20260926044000_community_hub_matchmaking_loop.sql"


def main() -> int:
    for path in (UI, RUNTIME, SERVICE):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    ui = UI.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    matchmaking = MATCHMAKING_MIGRATION.read_text(encoding="utf-8")

    custom_ids = re.findall(r'custom_id\s*=\s*["\']([^"\']+)["\']', ui)
    assert custom_ids, "Community Hub exposes no component custom IDs"
    assert max(map(len, custom_ids)) <= 100, "Discord component custom_id exceeds 100 chars"
    assert len(custom_ids) == len(set(custom_ids)), "duplicate Community Hub custom_id"

    required = (
        "Find Players",
        "Quick Match",
        "Start Gaming Session",
        "Community Pulse",
        "Game Notifications",
        "Open to Play",
        "Match Safety",
        "Report Problem",
        "Safety Reports",
        "Staff Dashboard",
        "CommunitySessionPublicView",
    )
    for needle in required:
        assert needle in ui, f"missing Community Hub surface: {needle}"

    assert re.search(r"\bLFG\b", ui, re.IGNORECASE) is None
    assert "ownership_token text not null unique" in migration
    assert "create table if not exists public.dank_community_availability" in migration
    assert "create table if not exists public.dank_community_user_blocks" in migration
    assert "create table if not exists public.dank_community_reports" in migration
    assert "match safety exclusion prevents this join" in migration
    assert "add column if not exists auto_match boolean not null default false" in matchmaking
    assert "community_hub_quick_match(" in matchmaking
    assert "p_idempotency_key text" in matchmaking
    assert "community_hub_normalize_formation" in matchmaking
    assert "Enable Quick Match" in ui
    assert "AvailableGameSelect" in ui
    assert "dank:hub:find:availablegame:v1" in ui
    assert "created_match" in ui
    assert "if discord_id <= 0:" in runtime
    assert "DANK_ENABLE_PRESENCE_INTENT" in (ROOT / "stoney_verify" / "globals.py").read_text(encoding="utf-8")
    assert "lambda: channel.send(" not in runtime
    assert "lambda: guild.create_voice_channel(" not in runtime

    print("PASS Community Hub static contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
