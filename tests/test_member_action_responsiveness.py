from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify.commands_ext import member_role_browser_common as common


ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ROOT / "stoney_verify" / "commands_ext" / "member_role_browser_actions.py"


def test_member_action_audit_write_has_hard_timeout(monkeypatch) -> None:
    async def never_returns(*args, **kwargs):
        _ = (args, kwargs)
        await asyncio.Event().wait()

    monkeypatch.setattr(common, "_MEMBER_ACTION_AUDIT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(common.asyncio, "to_thread", never_returns)

    result = asyncio.run(
        common.record_member_action(
            guild_id=1,
            actor_id=2,
            target_id=3,
            action="ban",
            reason="responsiveness regression test",
            metadata={"ok": True},
        )
    )

    assert result is False


def test_destructive_member_actions_ack_before_remote_work_and_release_before_audit() -> None:
    source = ACTIONS.read_text(encoding="utf-8")
    start = source.index("class MemberDestructiveActionModal")
    block = source[start:]

    defer_at = block.index("await interaction.response.defer(ephemeral=True, thinking=True)")
    permission_at = block.index("if not await require_review(interaction):")
    target_at = block.index("self.parent._fresh_target(interaction)")
    blockers_at = block.index("action_blockers(")
    lock_at = block.index("async with action_lock(")
    reply_at = block.index("await interaction.followup.send(")
    audit_at = block.index("await record_member_action(")

    assert defer_at < permission_at < target_at < blockers_at < lock_at < reply_at < audit_at
    assert "timeout=_DESTRUCTIVE_PRECHECK_TIMEOUT_SECONDS" in block
    assert "timeout=_DESTRUCTIVE_DISCORD_TIMEOUT_SECONDS" in block

    lock_block = block[lock_at:reply_at]
    assert "record_member_action" not in lock_block
    assert "Discord did not confirm the {self.action} in time." in block
