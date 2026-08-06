from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.setup_engine import verification_modes
from stoney_verify.verification_new import id_ticket_runtime


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_GUILD_ID = 1357215261001912320


def permissions(
    *,
    view: bool = True,
    send: bool = True,
    history: bool = True,
    embeds: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        view_channel=view,
        send_messages=send,
        read_message_history=history,
        embed_links=embeds,
    )


def runtime_objects(*, guild_id: int = ALLOWED_GUILD_ID):
    guild = SimpleNamespace(id=guild_id, name="Approved ID Server", me=None)
    member = SimpleNamespace(id=100)
    bot_member = SimpleNamespace(id=200)
    owner_perms = permissions()
    bot_perms = permissions()

    def permissions_for(target):
        if target is member:
            return owner_perms
        if target is bot_member:
            return bot_perms
        return permissions(view=False, send=False, history=False, embeds=False)

    channel = SimpleNamespace(
        id=300,
        guild=guild,
        permissions_for=permissions_for,
    )
    # Match the actual values saved by the ID / Web + Voice setup template.
    # That template does not need to persist verification_mode=id_verify.
    cfg = SimpleNamespace(
        setup_choice="id_voice_check",
        verification_panel_style="id_voice_check",
        verification_requires_id=True,
        verification_allows_voice=True,
    )
    return guild, channel, member, bot_member, owner_perms, bot_perms, cfg


def test_persisted_setup_values_are_recognized_as_id_verification() -> None:
    guild = SimpleNamespace(id=ALLOWED_GUILD_ID, name="Approved ID Server")

    for cfg in (
        SimpleNamespace(setup_choice="id_check"),
        SimpleNamespace(setup_choice="id_voice_check"),
        SimpleNamespace(verification_panel_style="id_check"),
        SimpleNamespace(verification_requires_id=True),
    ):
        assert verification_modes.config_requests_id_verify(cfg) is True
        assert verification_modes.effective_verification_mode(guild, cfg) == "id_verify"


def test_persisted_id_setup_still_cannot_enable_a_non_allowlisted_guild() -> None:
    guild = SimpleNamespace(id=999, name="Public Server")
    cfg = SimpleNamespace(
        setup_choice="id_voice_check",
        verification_requires_id=True,
    )

    assert verification_modes.config_requests_id_verify(cfg) is True
    assert verification_modes.effective_verification_mode(guild, cfg) == "basic_button"


def test_allowlisted_ticket_uses_valid_inherited_access_without_rewriting_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member, bot_member, _owner_perms, _bot_perms, cfg = runtime_objects()
    monkeypatch.setattr(id_ticket_runtime, "_bot_member", lambda _guild: bot_member)

    calls: list[str] = []

    async def config_loader(_guild_id: int):
        return cfg

    async def access_repair(_channel, _member):
        calls.append("repair")
        return True

    async def panel_poster(_channel, **kwargs):
        calls.append("post")
        assert kwargs["requester_id"] == member.id
        return "posted"

    posted = asyncio.run(
        id_ticket_runtime.post_allowlisted_id_ticket_panel(
            channel,
            member,
            config_loader=config_loader,
            access_repair=access_repair,
            panel_poster=panel_poster,
            site_url="https://verify.example",
            ttl_minutes=20,
            allow_regen=False,
        )
    )

    assert posted is True
    assert calls == ["post"]


def test_missing_effective_access_uses_repair_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member, bot_member, _owner_perms, bot_perms, cfg = runtime_objects()
    bot_perms.embed_links = False
    monkeypatch.setattr(id_ticket_runtime, "_bot_member", lambda _guild: bot_member)

    calls: list[str] = []

    async def config_loader(_guild_id: int):
        return cfg

    async def access_repair(_channel, _member):
        calls.append("repair")
        return True

    async def panel_poster(_channel, **_kwargs):
        calls.append("post")
        return "updated"

    posted = asyncio.run(
        id_ticket_runtime.post_allowlisted_id_ticket_panel(
            channel,
            member,
            config_loader=config_loader,
            access_repair=access_repair,
            panel_poster=panel_poster,
            site_url="https://verify.example",
            ttl_minutes=20,
            allow_regen=False,
        )
    )

    assert posted is True
    assert calls == ["repair", "post"]


def test_non_allowlisted_guild_never_receives_the_id_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member, bot_member, _owner_perms, _bot_perms, cfg = runtime_objects(guild_id=999)
    monkeypatch.setattr(id_ticket_runtime, "_bot_member", lambda _guild: bot_member)

    calls: list[str] = []

    async def config_loader(_guild_id: int):
        return cfg

    async def access_repair(_channel, _member):
        calls.append("repair")
        return True

    async def panel_poster(_channel, **_kwargs):
        calls.append("post")
        return "posted"

    posted = asyncio.run(
        id_ticket_runtime.post_allowlisted_id_ticket_panel(
            channel,
            member,
            config_loader=config_loader,
            access_repair=access_repair,
            panel_poster=panel_poster,
            site_url="https://verify.example",
            ttl_minutes=20,
            allow_regen=False,
        )
    )

    assert posted is False
    assert calls == []


def test_panel_poster_must_report_a_real_post_or_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _guild, channel, member, bot_member, _owner_perms, _bot_perms, cfg = runtime_objects()
    monkeypatch.setattr(id_ticket_runtime, "_bot_member", lambda _guild: bot_member)

    async def config_loader(_guild_id: int):
        return cfg

    async def access_repair(_channel, _member):
        return True

    async def panel_poster(_channel, **_kwargs):
        return ""

    posted = asyncio.run(
        id_ticket_runtime.post_allowlisted_id_ticket_panel(
            channel,
            member,
            config_loader=config_loader,
            access_repair=access_repair,
            panel_poster=panel_poster,
            site_url="https://verify.example",
            ttl_minutes=20,
            allow_regen=False,
        )
    )

    assert posted is False


def test_existing_allowlist_guard_owns_the_canonical_ticket_runtime_wiring() -> None:
    guard = (
        ROOT / "stoney_verify/startup_guards/id_verify_allowlist_guard.py"
    ).read_text(encoding="utf-8")
    clean_panel = (
        ROOT / "stoney_verify/commands_ext/public_ticket_panel_clean.py"
    ).read_text(encoding="utf-8")
    modes = (
        ROOT / "stoney_verify/setup_engine/verification_modes.py"
    ).read_text(encoding="utf-8")

    assert "def _patch_verification_ticket_flow" in guard
    assert "post_allowlisted_id_ticket_panel" in guard
    assert "_canonical_id_ticket_runtime" in guard
    assert "flow._post_verify_ui = post_verify_ui_canonical" in guard
    assert "_maybe_post_verification_panel" in clean_panel
    assert "verify_flow._post_verify_ui" in clean_panel
    assert '"setup_choice"' in modes
    assert '"verification_panel_style"' in modes
    assert '"verification_requires_id"' in modes
