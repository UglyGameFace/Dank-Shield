from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID
import ast

from tools import stage_dankshield_private_source_state as stage

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"tools/stage_dankshield_private_source_state.py"
WF=ROOT/".github/workflows/dankshield-private-source-copy.yml"
OLD=ROOT/"tools/aiven_guild_config_stage.py"


def test_scope_is_exact_small_private_dank_shield_tables():
    assert set(stage.TABLES)=={
      "warns","dank_profile_users","dank_profile_guild_settings"
    }
    assert stage.SOURCE_REF=="hyncpbvqyjojxhjzcdzg"
    assert stage.DESTINATION_REF=="viamriafkbqhquvrsnua"
    tree=ast.parse(OLD.read_text(encoding="utf8"))
    refs=[n.value.value for n in tree.body if isinstance(n,ast.Assign)
          and any(isinstance(t,ast.Name) and t.id=="SOURCE_REF" for t in n.targets)
          and isinstance(n.value,ast.Constant)]
    assert refs==[stage.SOURCE_REF]
    assert "dank_live_profile_cards" not in stage.TABLES
    assert "audit_logs" not in stage.TABLES
    assert "bot_operation_jobs" not in stage.TABLES
    stage._safe_shape()


def test_source_rows_hash_preserves_privacy_and_timestamps():
    row=(
        UUID("00000000-0000-0000-0000-000000000001"),
        "100", "200", None, "test privacy data",
        None, datetime(2026,10,20,tzinfo=timezone.utc),
    )
    assert stage._fingerprint([row])==stage._fingerprint([row])
    assert stage._fingerprint([row])!=stage._fingerprint([row[:-1]+(None,)])
    assert stage._serialize(({"z":3,"a":True},))==stage._serialize(({"a":True,"z":3},))


def test_budget_scope_and_fail_closed_contracts():
    assert stage.CAPS["warns"]<=1000
    assert stage.CAPS["dank_profile_guild_settings"]<=5000
    assert stage.MAX_TOTAL_BYTES<=8*1024*1024
    s=SOURCE.read_text(encoding="utf8")
    assert "set transaction isolation level repeatable read, read only" in s
    assert "set transaction read only" in s
    assert "default_transaction_read_only=on" in s
    assert "sql.Identifier(name)" in s
    assert "sql.Identifier(c)" in s
    assert "if mode==\"stage\"" in s
    assert "no destructive merge" in s
    assert "_verify_target(dst,original)" in s
    assert "except Exception:" in s
    assert "dank_live_profile_cards" not in s
    assert "upload-artifact" not in s


def test_manual_owner_only_workflow_does_not_print_private_contents():
    w=WF.read_text(encoding="utf8")
    assert "workflow_dispatch:" in w
    assert "github.actor == 'UglyGameFace'" in w
    assert "github.ref == 'refs/heads/main'" in w
    assert "secrets.DANK_SOURCE_DATABASE_URL" in w
    assert "secrets.DANK_FRESH_DATABASE_URL" in w
    assert "DANK_PRIVATE_STAGE_CONFIRMATION" in w
    assert "persist-credentials: false" in w
    assert "actions/upload-artifact" not in w
    assert "discloud/deploy-action" not in w
    assert "schedule:" not in w
