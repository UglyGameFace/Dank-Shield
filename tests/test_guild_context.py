from __future__ import annotations

from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context


def test_guild_context_reports_ready_ticket_verify_and_logging_config():
    cfg = GuildRuntimeConfig(
        {
            "guild_id": "12345",
            "source": "supabase:guild_configs",
            "ticket_category_id": "111",
            "staff_role_id": "222",
            "verify_channel_id": "333",
            "unverified_role_id": "444",
            "verified_role_id": "555",
            "modlog_channel_id": "666",
            "allow_runtime_discovery": True,
            "use_env_fallbacks": False,
        }
    )

    context = build_guild_context(12345, cfg)

    assert context.guild_id == 12345
    assert context.source == "supabase:guild_configs"
    assert context.unsafe_to_act is False
    assert context.ticket_ready is True
    assert context.verify_ready is True
    assert context.logging_ready is True
    assert context.channel_id("ticket_category_id") == 111
    assert context.role_id("staff_role_id") == 222
    assert context.channel_id("unknown_channel_key", default=999) == 999
    assert context.role_id("unknown_role_key", default=888) == 888


def test_guild_context_refuses_unconfigured_public_fallback_actions():
    cfg = GuildRuntimeConfig(
        {
            "guild_id": "67890",
            "source": "unconfigured:isolated_public_fallback",
            "ticket_category_id": None,
            "staff_role_id": None,
            "verify_channel_id": None,
            "unverified_role_id": None,
            "verified_role_id": None,
            "modlog_channel_id": None,
            "allow_runtime_discovery": True,
            "use_env_fallbacks": False,
        }
    )

    context = build_guild_context(67890, cfg)

    assert context.guild_id == 67890
    assert context.is_unconfigured is True
    assert context.unsafe_to_act is True
    assert context.ticket_ready is False
    assert context.verify_ready is False
    assert context.logging_ready is False
    assert context.missing_ticket_keys == ("ticket_category_id", "staff_role_id")
    assert context.missing_verify_keys == ("verify_channel_id", "unverified_role_id", "verified_role_id")
    assert context.missing_log_keys == ("modlog_channel_id",)


def test_guild_context_summary_is_plain_data_for_diagnostics_and_future_premium_gates():
    cfg = GuildRuntimeConfig(
        {
            "guild_id": "24680",
            "source": "supabase:guild_configs",
            "ticket_category_id": "101",
            "staff_role_id": None,
            "verify_channel_id": "303",
            "unverified_role_id": "404",
            "verified_role_id": "505",
            "modlog_channel_id": None,
            "allow_runtime_discovery": False,
            "use_env_fallbacks": False,
        }
    )

    context = build_guild_context(24680, cfg)
    summary = context.summary()

    assert summary["guild_id"] == 24680
    assert summary["source"] == "supabase:guild_configs"
    assert summary["unsafe_to_act"] is False
    assert summary["ticket_ready"] is False
    assert summary["verify_ready"] is True
    assert summary["logging_ready"] is False
    assert summary["missing_ticket_keys"] == ["staff_role_id"]
    assert summary["missing_log_keys"] == ["modlog_channel_id"]
    assert summary["allow_runtime_discovery"] is False
