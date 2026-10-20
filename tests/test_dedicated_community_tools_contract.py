from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NEW=ROOT/"supabase_fresh/migrations/202610200022_dankshield_community_tools.sql"
SMOKE=ROOT/"supabase_fresh/tests/community_tools_smoke.sql"
WF=ROOT/".github/workflows/dankshield-community-tools-sql.yml"
SOURCES=(
 "20260811122504_community_tools.sql",
 "20260812224000_smart_sticky_quiet_notices.sql",
 "20260905121500_community_tools_hardening.sql",
 "20260921042000_quiet_notice_atomic_delivery_clear.sql",
)

def test_source_scripts_in_exact_order_without_diverging_runtime():
    sql=NEW.read_text(encoding="utf8")
    position=0
    for fname in SOURCES:
        fragment=(ROOT/"supabase/migrations"/fname).read_text(encoding="utf8")
        start=sql.find(fragment,position)
        assert start>=position, f"canonical source missing/out of order: {fname}"
        position=start+len(fragment)
    for name in ("dank_stickies","dank_sticky_polls","dank_quiet_notices"):
        assert f"public.{name}" in sql
    for func in ("save_dank_sticky_bundle","record_dank_quiet_notice_activity","clear_dank_quiet_notice_delivery"):
        assert func in sql
    assert "revoke all on table public.dank_stickies" in sql
    assert "grant select,insert,update,delete" in sql.lower()

def test_smoke_and_ci_are_non_persistent():
    q=SMOKE.read_text(encoding="utf8")
    w=WF.read_text(encoding="utf8")
    assert q.startswith("-- All SQL test rows")
    assert q.endswith("rollback;\n")
    assert "save_dank_sticky_bundle" in q
    assert "clear_dank_quiet_notice_delivery" in q
    assert "Stale notice clear incorrectly succeeded" in q
    assert "postgres:16" in w
    assert "pull_request:" in w
    assert "secrets." not in w
    assert "discloud/deploy-action" not in w
