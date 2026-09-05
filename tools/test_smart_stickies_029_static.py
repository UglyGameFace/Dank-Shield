from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/community_quiet_notice_service.py").read_text(encoding="utf-8")
QUIET_UI = (ROOT / "stoney_verify/commands_ext/public_quiet_notice.py").read_text(encoding="utf-8")
PREVIEW_UI = (ROOT / "stoney_verify/commands_ext/public_sticky_preview.py").read_text(encoding="utf-8")
MAIN_UI = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20260812224000_smart_sticky_quiet_notices.sql").read_text(encoding="utf-8")
HARDENING = (ROOT / "supabase/migrations/20260905121500_community_tools_hardening.sql").read_text(encoding="utf-8")


def test_quiet_activity_extends_the_single_canonical_message_listener() -> None:
    assert RUNTIME.count('bot.add_listener(runtime.on_message, "on_message")') == 1
    assert "def _observe_quiet_activity" in RUNTIME
    assert "def _quiet_watch_loop" in RUNTIME
    assert "def _check_quiet_notices" in RUNTIME
    assert "channel.history(" not in RUNTIME
    assert "@bot.event" not in RUNTIME
    assert "bot.on_message =" not in RUNTIME


def test_quiet_activity_and_delivery_share_same_per_guild_lock_and_keep_latest_activity() -> None:
    assert "self._quiet_locks: dict[int, asyncio.Lock]" in RUNTIME
    assert RUNTIME.count("self._quiet_locks.setdefault(") >= 2
    assert "latest = self._quiet_configs.get(guild_id) or config" in RUNTIME
    assert "newest_observed = self._guild_last_activity.get(guild_id) or observed" in RUNTIME
    assert "activity_at=newest_observed" in RUNTIME


def test_quiet_notice_is_separate_from_channel_sticky_storage() -> None:
    assert 'QUIET_NOTICE_TABLE = "dank_quiet_notices"' in SERVICE
    assert "guild_id bigint primary key" in MIGRATION
    assert "channel_id bigint not null" in MIGRATION
    assert "dank_stickies" not in MIGRATION


def test_quiet_notice_schema_is_service_role_only_and_bounded() -> None:
    assert "inactivity_seconds between 300 and 604800" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "revoke all on table public.dank_quiet_notices from anon, authenticated" in MIGRATION
    assert "grant all on table public.dank_quiet_notices to service_role" in MIGRATION


def test_temporary_preview_test_paths_do_not_mutate_live_delivery_state() -> None:
    temporary_test = PREVIEW_UI.split("async def _post_temporary_test", 1)[1].split("def _merge_draft_with_live_state", 1)[0]
    assert "delete_after=30" in temporary_test
    assert "save_sticky(" not in temporary_test
    assert "refresh_channel(" not in temporary_test
    assert "update_sticky_delivery" not in PREVIEW_UI

    assert 'label="Post 30s Test"' in QUIET_UI
    assert "delete_after=30" in QUIET_UI
    assert "update_quiet_delivery" not in QUIET_UI


def test_create_edit_is_draft_until_explicit_atomic_publish() -> None:
    assert "class StickyMessageModal" in MAIN_UI
    assert "class StickyEmbedModal" in MAIN_UI
    assert "await show_sticky_draft_preview(interaction, config)" in MAIN_UI
    assert 'label="Publish Sticky"' in PREVIEW_UI
    assert 'label="Discard Draft"' in PREVIEW_UI
    assert "publish_config = _merge_draft_with_live_state(self.config, current)" in PREVIEW_UI
    assert "saved, saved_poll = await save_sticky_bundle(publish_config, None)" in PREVIEW_UI
    assert "last_message_id=current.last_message_id" in PREVIEW_UI
    assert "enabled=current.enabled" in PREVIEW_UI
    assert "nothing has changed live yet" in PREVIEW_UI
    assert "create or replace function public.save_dank_sticky_bundle" in HARDENING


def test_quiet_setup_edit_preserves_authoritative_destination_and_delivery_state() -> None:
    assert "channel_id=int(base.channel_id)" in QUIET_UI
    assert "last_activity_at=now if is_new else base.last_activity_at" in QUIET_UI
    assert "last_notice_message_id=base.last_notice_message_id" in QUIET_UI
    assert "last_notice_sent_at=base.last_notice_sent_at" in QUIET_UI
    assert 'label="Use This Channel"' in QUIET_UI
    assert "Existing destination and quiet-cycle state were preserved" in QUIET_UI
    assert "For auto-clear, enter `yes` or `no`" in QUIET_UI


def test_quiet_destructive_actions_persist_before_live_message_deletion() -> None:
    toggle = QUIET_UI.split("async def toggle", 1)[1].split("async def use_here", 1)[0]
    assert toggle.index("await set_quiet_notice_enabled") < toggle.index("await runtime.delete_quiet_live_message")
    remove = QUIET_UI.split("class QuietNoticeRemoveView", 1)[1].split("async def _refresh_live_notice", 1)[0]
    assert remove.index("await delete_quiet_notice") < remove.index("await runtime.delete_quiet_live_message")


def test_main_sticky_center_is_compact_and_routes_advanced_controls() -> None:
    assert 'label="Preview / Test"' in MAIN_UI
    assert 'label="Sticky Settings"' in MAIN_UI
    assert 'label="Quiet Server Notice"' in MAIN_UI
    assert "class StickySettingsView" in MAIN_UI
    assert "show_sticky_preview" in MAIN_UI
    assert "open_quiet_notice_center" in MAIN_UI


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
