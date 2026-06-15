from __future__ import annotations

from stoney_verify.guild_action_guard import decide_guild_action
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context


def _context(config: dict) -> object:
    return build_guild_context(config.get("guild_id", "123"), GuildRuntimeConfig(config))


def test_guild_action_guard_allows_ready_ticket_action():
    context = _context(
        {
            "guild_id": "123",
            "source": "supabase:guild_configs",
            "ticket_category_id": "111",
            "staff_role_id": "222",
            "verify_channel_id": "333",
            "unverified_role_id": "444",
            "verified_role_id": "555",
            "modlog_channel_id": "666",
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
        }
    )

    decision = decide_guild_action(context, action="post ticket panel", feature="tickets")

    assert decision.allowed is True
    assert decision.denied is False
    assert decision.blockers == ()
    assert "allowed" in decision.user_message().lower()


def test_guild_action_guard_blocks_unsafe_unconfigured_guild():
    context = _context(
        {
            "guild_id": "456",
            "source": "unconfigured:isolated_public_fallback",
            "ticket_category_id": None,
            "staff_role_id": None,
            "verify_channel_id": None,
            "unverified_role_id": None,
            "verified_role_id": None,
            "modlog_channel_id": None,
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
        }
    )

    decision = decide_guild_action(context, action="post ticket panel", feature="tickets")

    assert decision.allowed is False
    assert decision.denied is True
    assert decision.reason == "Cannot run post ticket panel safely."
    assert any("not safely configured" in blocker for blocker in decision.blockers)
    assert any("ticket_category_id" in blocker and "staff_role_id" in blocker for blocker in decision.blockers)


def test_guild_action_guard_blocks_required_config_keys():
    context = _context(
        {
            "guild_id": "789",
            "source": "supabase:guild_configs",
            "ticket_category_id": "111",
            "staff_role_id": "222",
            "verify_channel_id": "333",
            "unverified_role_id": "444",
            "verified_role_id": "555",
            "modlog_channel_id": None,
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
        }
    )

    decision = decide_guild_action(
        context,
        action="write audit log",
        feature="logging",
        required_keys=("modlog_channel_id",),
    )

    assert decision.allowed is False
    assert decision.denied is True
    assert any("modlog_channel_id" in blocker for blocker in decision.blockers)


def test_guild_action_guard_keeps_premium_gate_as_audit_warning_for_now():
    context = _context(
        {
            "guild_id": "321",
            "source": "supabase:guild_configs",
            "ticket_category_id": "111",
            "staff_role_id": "222",
            "verify_channel_id": "333",
            "unverified_role_id": "444",
            "verified_role_id": "555",
            "modlog_channel_id": "666",
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
        }
    )

    decision = decide_guild_action(
        context,
        action="advanced ticket analytics",
        feature="tickets",
        premium_feature="ticket_analytics",
    )

    assert decision.allowed is True
    assert decision.premium_feature == "ticket_analytics"
    assert decision.warnings == ("Premium gate pending for `ticket_analytics`. Current build treats this as an audit-only warning.",)
