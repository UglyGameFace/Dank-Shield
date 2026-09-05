from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACE = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
UI = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/community_tools_service.py").read_text(encoding="utf-8")
PREVIEW = (ROOT / "stoney_verify/commands_ext/public_sticky_preview.py").read_text(encoding="utf-8")
QUIET_UI = (ROOT / "stoney_verify/commands_ext/public_quiet_notice.py").read_text(encoding="utf-8")
LOOKUPS = (ROOT / "stoney_verify/community_lookup_service.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20260811122504_community_tools.sql").read_text(encoding="utf-8")
HARDENING = (ROOT / "supabase/migrations/20260905121500_community_tools_hardening.sql").read_text(encoding="utf-8")


def test_home_routes_to_community_tools_without_expanding_dank_children() -> None:
    assert 'label="Community Tools"' in SURFACE
    assert "open_community_tools(interaction, replace_message=True)" in SURFACE
    assert 'dank_children != ["home", "upload"]' in SURFACE


def test_runtime_registration_is_idempotent_and_single_owner() -> None:
    assert "ensure_community_tools_runtime(bot)" in SURFACE
    assert 'bot.add_listener(runtime.on_message, "on_message")' in RUNTIME
    assert RUNTIME.count('bot.add_listener(runtime.on_message, "on_message")') == 1
    assert "_dank_community_tools_runtime" in RUNTIME
    assert "_pending_refreshes" in RUNTIME
    assert "self._refresh_from_activity(message.channel, config)" in RUNTIME
    assert "expected_config=config, force=True" in RUNTIME


def test_sticky_runtime_avoids_monkey_patches_history_scans_and_repeat_mentions() -> None:
    assert "@bot.event" not in RUNTIME
    assert "bot.on_message =" not in RUNTIME
    assert "channel.history(" not in RUNTIME
    assert "AllowedMentions.none()" in RUNTIME


def test_raw_webhook_secrets_are_never_stored() -> None:
    forbidden = "webhook_" + "url"
    assert forbidden not in SERVICE
    assert forbidden not in UI
    assert forbidden not in RUNTIME
    assert forbidden not in MIGRATION
    assert forbidden not in HARDENING
    assert 'MANAGED_WEBHOOK_NAME = "Dank Shield Sticky"' in RUNTIME
    assert ".create_webhook(" in RUNTIME


def test_migration_persists_stickies_and_polls_service_role_only() -> None:
    assert "create table if not exists public.dank_stickies" in MIGRATION
    assert "create table if not exists public.dank_sticky_polls" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "revoke all on table public.dank_stickies from anon, authenticated" in MIGRATION
    assert "grant all on table public.dank_stickies to service_role" in MIGRATION
    assert "create or replace function public.save_dank_sticky_bundle" in HARDENING
    assert "delete from public.dank_sticky_polls" in HARDENING
    assert "grant execute on function public.save_dank_sticky_bundle(jsonb, jsonb) to service_role" in HARDENING


def test_poll_and_embed_posting_use_preview_publish_and_real_permissions() -> None:
    assert "NativePollPreviewView" in UI
    assert 'label="Publish Poll"' in UI
    assert "EmbedDraftView" in UI
    assert 'label="Publish Embed"' in UI
    assert "_poll_permission" in UI
    assert "channel.permissions_for(member)" in UI
    assert "the value was not silently changed" in UI


def test_sticky_state_transitions_use_atomic_bundle() -> None:
    assert "save_sticky_bundle" in SERVICE
    assert "STICKY_BUNDLE_RPC" in SERVICE
    assert "save_sticky_bundle(publish_config, None)" in PREVIEW
    assert "save_sticky_bundle(sticky, self.poll)" in UI
    assert "save_sticky(sticky)\n            saved_poll" not in UI


def test_runtime_replacement_is_non_destructive_and_poll_render_is_serialized() -> None:
    send_index = RUNTIME.index("message = await self._send(channel, current)")
    persist_index = RUNTIME.index("saved = await update_sticky_delivery(", send_index)
    delete_index = RUNTIME.index("await self._delete_previous(channel, previous_message_id)", persist_index)
    assert send_index < persist_index < delete_index
    assert "await self._delete_message_object(message)" in RUNTIME
    assert "_poll_render_locks" in RUNTIME
    assert "cast_and_render_poll_vote" in RUNTIME


def test_quiet_notice_destination_changes_are_explicit_and_destructive_order_is_safe() -> None:
    assert 'label="Use This Channel"' in QUIET_UI
    assert "channel_id=int(base.channel_id)" in QUIET_UI
    remove_block = QUIET_UI.split("class QuietNoticeRemoveView", 1)[1].split("class QuietNoticeModal", 1)[0]
    assert remove_block.index("await delete_quiet_notice") < remove_block.index("await runtime.delete_quiet_live_message")
    toggle_block = QUIET_UI.split("async def toggle", 1)[1].split("async def use_here", 1)[0]
    assert toggle_block.index("await set_quiet_notice_enabled") < toggle_block.index("await runtime.delete_quiet_live_message")


def test_lookups_are_bounded_validate_redirects_and_do_not_advertise_fake_ai() -> None:
    assert "MAX_CONCURRENT_LOOKUPS" in LOOKUPS
    assert "_LOOKUP_SEMAPHORE" in LOOKUPS
    assert "precipitation_probability_max" in LOOKUPS
    assert 'host == "wikihow.com" or host.endswith(".wikihow.com")' in LOOKUPS
    assert "Image AI Status" not in UI
    assert "vision provider" not in UI.lower()


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
