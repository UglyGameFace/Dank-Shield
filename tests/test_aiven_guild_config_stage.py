"""No-network safety contracts for the owner-controlled Aiven config staging."""
from dataclasses import replace
import os

import pytest

from stoney_verify.core_data_stage import plan_source
from tools import aiven_guild_config_stage as stage


def plan(rows):
    def page(name, keys, offset, size):
        assert name == "guild_configs" and keys == ("id",)
        return rows[offset:offset + size]
    return plan_source(page, selected_tables=["guild_configs"])


def make(guild="123", row_id="id1"):
    return {"id": row_id, "guild_id": guild,
            "settings": {"verify_channel_id": "987", "welcome_message": "hello"},
            "config": {"verify_channel_id": "987"}}


def test_only_owner_confirmation_permits_copy():
    stage.validate_mode("preview", "")
    stage.validate_mode("stage", stage.CONFIRMATION)
    for mode, confirmation in [("stage", ""), ("stage", "YES"), ("import", stage.CONFIRMATION)]:
        with pytest.raises(ValueError):
            stage.validate_mode(mode, confirmation)


def test_exact_guild_configs_first_slice_requires_unique_valid_guilds():
    stage.validate_first_slice(plan([make(), make("456", "id2")]))
    with pytest.raises(ValueError, match="outside safe bound"):
        stage.validate_first_slice(plan([]))
    with pytest.raises(ValueError, match="duplicate guild"):
        stage.validate_first_slice(plan([make(), make("123", "id2")]))
    for bad in ["", "0", "00", "-1", "one", "1" * 21]:
        with pytest.raises(ValueError, match="invalid guild"):
            stage.validate_first_slice(plan([make(bad)]))


def test_other_tables_and_oversized_source_blocked():
    checked = plan([make()])
    with pytest.raises(ValueError, match="only guild_configs"):
        stage.validate_first_slice(replace(checked, table_counts={"tickets": 1}))
    with pytest.raises(ValueError, match="safe byte budget"):
        stage.validate_first_slice(replace(checked, accounted_bytes=stage.MAX_SOURCE_BYTES + 1))


def test_workflow_is_manual_only_no_deploy_and_no_payload_artifacts():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    file = root / ".github/workflows/aiven-guild-config-staging.yml"
    content = file.read_text("utf-8")
    assert "  workflow_dispatch:" in content
    for event in ("  push:", "  schedule:", "  pull_request:", "  workflow_run:"):
        assert event not in content
    assert "github.actor == 'UglyGameFace'" in content
    assert "github.ref == 'refs/heads/main'" in content
    assert "STAGE_AIVEN_GUILD_CONFIGS_ONLY" in content
    assert "secrets.DANK_SOURCE_DATABASE_URL" in content
    assert "secrets.AIVEN_POSTGRES_URI" in content
    assert "secrets.AIVEN_POSTGRES_CA_PEM" in content
    assert "actions/upload-artifact" not in content
    assert "discloud/deploy-action" not in content
    assert "SUPABASE_SERVICE_ROLE_KEY" not in content


def test_staging_reuses_atomic_checks_and_disables_public_routing():
    from pathlib import Path
    code = (Path(__file__).resolve().parents[1] /
            "tools/aiven_guild_config_stage.py").read_text("utf-8")
    assert "from stoney_verify.core_data_stage import apply_stage" in code
    assert "validate_source_dsn" in code
    assert "validate_target" in code
    assert 'set transaction isolation level repeatable read, read only' in code
    assert "default_transaction_read_only=on" in code
    assert '"sslmode": "verify-full"' in code
    assert "_assert_staged_matches" in code
    assert "source_reread=lambda: _source_plan(src)" in code
    assert "delete from" not in code.lower()
    assert "update dank_core.guild_config_state" not in code.lower()
    assert "print(str(exc))" not in code
