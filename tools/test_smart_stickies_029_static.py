from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/community_quiet_notice_service.py").read_text(encoding="utf-8")
QUIET_UI = (ROOT / "stoney_verify/commands_ext/public_quiet_notice.py").read_text(encoding="utf-8")
PREVIEW_UI = (ROOT / "stoney_verify/commands_ext/public_sticky_preview.py").read_text(encoding="utf-8")
MAIN_UI = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20260812224000_smart_sticky_quiet_notices.sql").read_text(encoding="utf-8")


def test_quiet_activity_extends_the_single_canonical_message_listener() -> None:
    assert RUNTIME.count('bot.add_listener(runtime.on_message, "on_message")') == 1
    assert "def _observe_quiet_activity" in RUNTIME
    assert "def _quiet_watch_loop" in RUNTIME
    assert "def _check_quiet_notices" in RUNTIME
    assert "channel.history(" not in RUNTIME
    assert "@bot.event" not in RUNTIME
    assert "bot.on_message =" not in RUNTIME


def test_quiet_activity_and_delivery_share_the_same_per_guild_lock() -> None:
    assert "self._quiet_locks: dict[int, asyncio.Lock]" in RUNTIME
    assert RUNTIME.count("self._quiet_locks.setdefault(") >= 2
    assert "latest = self._quiet_configs.get(guild_id) or config" in RUNTIME


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
    assert 'delete_after=30' in temporary_test
    assert "save_sticky(" not in temporary_test
    assert "refresh_channel(" not in temporary_test
    assert "update_sticky_delivery" not in PREVIEW_UI

    assert 'label="Post 30s Test"' in QUIET_UI
    assert "delete_after=30" in QUIET_UI
    assert "update_quiet_delivery" not in QUIET_UI


def test_create_edit_is_a_draft_until_explicit_publish() -> None:
    editor = MAIN_UI.split("class StickyEditorModal", 1)[1].split("class StickySpeedModal", 1)[0]
    assert "await show_sticky_draft_preview(interaction, config)" in editor
    assert "save_sticky(" not in editor
    assert 'label="Publish Sticky"' in PREVIEW_UI
    assert 'label="Discard Draft"' in PREVIEW_UI
    assert "publish_config = _merge_draft_with_live_state(self.config, current)" in PREVIEW_UI
    assert "saved = await save_sticky(publish_config)" in PREVIEW_UI
    assert "last_message_id=current.last_message_id" in PREVIEW_UI
    assert "enabled=current.enabled" in PREVIEW_UI
    assert "nothing has changed live yet" in PREVIEW_UI


def test_quiet_setup_edit_restarts_timer_and_clears_previous_delivery_state() -> None:
    assert "last_activity_at=now" in QUIET_UI
    assert "last_notice_message_id=None" in QUIET_UI
    assert "last_notice_sent_at=None" in QUIET_UI
    assert "Its inactivity timer starts again from now" in QUIET_UI
    assert "For auto-clear, enter `yes` or `no`" in QUIET_UI


def test_main_sticky_center_is_compact_and_routes_advanced_controls() -> None:
    assert 'label="Preview / Test"' in MAIN_UI
    assert 'label="Sticky Settings"' in MAIN_UI
    assert 'label="Quiet Server Notice"' in MAIN_UI
    assert "class StickySettingsView" in MAIN_UI
    assert "show_sticky_preview" in MAIN_UI
    assert "open_quiet_notice_center" in MAIN_UI
    assert "advanced controls are under Sticky Settings" in MAIN_UI
    assert "Removal is kept here so destructive controls stay out of the main sticky screen" in MAIN_UI


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
