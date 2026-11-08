"""Regression for ticket creation under Basic Verify (issue #701)."""
from types import SimpleNamespace
import sys
import pytest


@pytest.mark.asyncio
async def test_basic_verify_ticket_skips_panel_without_warning(monkeypatch):
    from stoney_verify.commands_ext import public_ticket_panel_clean as panel

    guild = SimpleNamespace(id=123)
    channel = SimpleNamespace(id=456, guild=guild)
    owner = SimpleNamespace(id=789)
    calls = []

    async def is_unverified(member):
        return True

    async def get_config(guild_id):
        return {"verification_mode": "basic_button"}

    def should_route(guild_arg, config):
        calls.append("resolve")
        return False

    async def ensure_vc(*args):
        calls.append("vc")
        return True, ""

    async def post(*args):
        calls.append("post")
        return False

    flow = SimpleNamespace(
        _is_unverified_only_member=is_unverified,
        _get_guild_config_safe=get_config,
        _should_auto_route_unverified_ticket=should_route,
        _ensure_configured_vc_verify_locked=ensure_vc,
        _post_verify_ui=post,
    )
    monkeypatch.setitem(sys.modules, "stoney_verify.startup_guards.unverified_ticket_panel_flow", flow)
    from stoney_verify import startup_guards
    monkeypatch.setattr(startup_guards, "unverified_ticket_panel_flow", flow, raising=False)
    monkeypatch.setattr(panel, "_canon", lambda row: "verification")

    assert await panel._maybe_post_verification_panel(channel, owner, {"slug": "verification"}) == ""
    assert calls == ["resolve"]


@pytest.mark.asyncio
async def test_advanced_verify_post_failure_still_warns(monkeypatch):
    from stoney_verify.commands_ext import public_ticket_panel_clean as panel
    from stoney_verify import startup_guards

    async def yes(*args):
        return True

    async def config(*args):
        return {}

    async def vc(*args):
        return True, ""

    async def failed_post(*args):
        return False

    flow = SimpleNamespace(
        _is_unverified_only_member=yes,
        _get_guild_config_safe=config,
        _should_auto_route_unverified_ticket=lambda *args: True,
        _ensure_configured_vc_verify_locked=vc,
        _post_verify_ui=failed_post,
    )
    monkeypatch.setattr(startup_guards, "unverified_ticket_panel_flow", flow, raising=False)
    monkeypatch.setattr(panel, "_canon", lambda row: "verification")
    channel = SimpleNamespace(id=456, guild=SimpleNamespace(id=123))
    assert await panel._maybe_post_verification_panel(channel, SimpleNamespace(id=789), {"slug": "verification"}) == "Verification panel failed to post."
