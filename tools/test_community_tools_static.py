from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACE = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
UI = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/community_tools_service.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20260811122504_community_tools.sql").read_text(encoding="utf-8")


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
    assert 'MANAGED_WEBHOOK_NAME = "Dank Shield Sticky"' in RUNTIME
    assert ".create_webhook(" in RUNTIME


def test_migration_persists_stickies_and_polls_service_role_only() -> None:
    assert "create table if not exists public.dank_stickies" in MIGRATION
    assert "create table if not exists public.dank_sticky_polls" in MIGRATION
    assert "enable row level security" in MIGRATION
    assert "revoke all on table public.dank_stickies from anon, authenticated" in MIGRATION
    assert "grant all on table public.dank_stickies to service_role" in MIGRATION


def test_poll_posting_respects_member_channel_permissions_and_validates_before_sticky_write() -> None:
    assert UI.count("_can_send_messages(interaction)") >= 2
    assert "You need **Send Messages** in this channel to create a poll." in UI

    sticky_poll = UI.split("class StickyPollModal", 1)[1].split("class StickyPollControlView", 1)[0]
    assert "validated_poll = normalize_poll(poll)" in sticky_poll
    assert sticky_poll.index("validated_poll = normalize_poll(poll)") < sticky_poll.index("saved_sticky = await save_sticky(sticky)")
    assert "save_sticky_poll(validated_poll)" in sticky_poll



def test_image_ai_is_explicitly_provider_gated() -> None:
    assert 'label="Image AI Status"' in UI
    assert "vision provider" in UI.lower()
    assert "OPENAI_API_KEY" not in UI
    assert "GEMINI_API_KEY" not in UI


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
