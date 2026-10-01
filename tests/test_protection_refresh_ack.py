from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.commands_ext import public_protection_center as center


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, events: list[str], *, done: bool = False) -> None:
        self.events = events
        self.done = done
        self.defer_calls = 0

    def is_done(self) -> bool:
        return self.done

    async def defer(self, *, ephemeral: bool = False) -> None:
        assert ephemeral is True
        self.defer_calls += 1
        self.events.append("defer")
        self.done = True


class _Interaction:
    def __init__(self, events: list[str], *, response_done: bool = False) -> None:
        self.guild = SimpleNamespace(id=123)
        self.user = SimpleNamespace(id=456)
        self.channel = None
        self.response = _Response(events, done=response_done)
        self.events = events
        self.edit_payloads: list[dict] = []
        self.last_payload = None

    async def edit_original_response(self, **payload):
        self.events.append("edit")
        self.edit_payloads.append(dict(payload))
        self.last_payload = dict(payload)


def _install_refresh_fakes(monkeypatch, events: list[str]) -> None:
    async def get_config(_guild_id: int, *, refresh: bool = False):
        assert refresh is True
        events.append("config")
        await asyncio.sleep(0)
        return {"automod_enabled": True}

    async def get_spam(_guild_id: int):
        events.append("spam")
        await asyncio.sleep(0)
        return ({"enabled": True, "mode": "timeout"}, "loaded")

    async def stats(_interaction, _guild):
        events.append("stats")

    async def unexpected_followup(*args, **kwargs):
        raise AssertionError(f"unexpected fallback followup: {args!r} {kwargs!r}")

    monkeypatch.setattr(center, "get_guild_config", get_config)
    monkeypatch.setattr(center, "_load_spam_settings", get_spam)
    monkeypatch.setattr(center, "_refresh_security_stats_after_panel", stats)
    monkeypatch.setattr(center, "_protection_embed", lambda *args, **kwargs: "embed")
    monkeypatch.setattr(center, "ProtectionCenterView", lambda **kwargs: "view")
    monkeypatch.setattr(center, "safe_send_interaction", unexpected_followup)


def test_refresh_panel_acknowledges_before_slow_loads_and_edits_original(
    monkeypatch,
) -> None:
    events: list[str] = []
    _install_refresh_fakes(monkeypatch, events)
    interaction = _Interaction(events)

    asyncio.run(center._refresh_panel(interaction, content="open"))

    assert events[0] == "defer"
    assert events[1] == "edit"
    assert events.index("config") > 1
    assert events.index("spam") > 1
    assert events.count("edit") == 2
    final_edit_index = len(events) - 2
    assert events[final_edit_index] == "edit"
    assert events[-1] == "stats"
    assert interaction.response.defer_calls == 1
    assert interaction.edit_payloads[0]["content"] == "⏳ Loading Protection Center…"
    assert interaction.edit_payloads[0]["view"] is None
    assert interaction.last_payload == {
        "content": "open",
        "embed": "embed",
        "view": "view",
    }


def test_refresh_panel_reuses_existing_ack_without_duplicate_defer(
    monkeypatch,
) -> None:
    events: list[str] = []
    _install_refresh_fakes(monkeypatch, events)
    interaction = _Interaction(events, response_done=True)

    asyncio.run(center._refresh_panel(interaction))

    assert "defer" not in events
    assert interaction.response.defer_calls == 0
    assert events[-2:] == ["edit", "stats"]


def test_protection_entry_ack_is_idempotent() -> None:
    events: list[str] = []
    interaction = _Interaction(events)

    asyncio.run(center._ack_protection_entry(interaction))
    asyncio.run(center._ack_protection_entry(interaction))

    assert events == ["defer"]
    assert interaction.response.defer_calls == 1


def test_public_protection_entries_ack_before_permission_checks() -> None:
    checks = (
        ("stoney_verify/commands_ext/public_protection_center.py", "async def protection_center"),
        ("stoney_verify/commands_ext/public_command_surface_v2.py", 'if key == "protection":'),
        ("stoney_verify/commands_ext/public_command_hub.py", 'label="Protection"'),
        ("stoney_verify/commands_ext/public_setup_solid.py", 'custom_id="stoney_solid:features_protection"'),
        ("stoney_verify/commands_ext/public_setup_recommend.py", "async def _open_protection_options"),
        ("stoney_verify/protection_center_services.py", "async def open_protection_center"),
        ("stoney_verify/commands_ext/public_protection_invite_ui.py", "async def back_to_protection"),
    )

    for relative, anchor in checks:
        source = (ROOT / relative).read_text(encoding="utf-8")
        start = source.index(anchor)
        block = source[start : start + 2600]
        ack = block.index("_ack_protection_entry")
        permission = block.index("_require_setup_permission")
        assert ack < permission, relative


def test_refresh_panel_replaces_loading_when_render_fails(monkeypatch) -> None:
    events: list[str] = []
    _install_refresh_fakes(monkeypatch, events)
    interaction = _Interaction(events)

    def boom(*_args, **_kwargs):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(center, "_protection_embed", boom)
    monkeypatch.setattr(
        center,
        "log_interaction_failure",
        lambda *_args, **_kwargs: SimpleNamespace(error_id="render-test"),
    )

    asyncio.run(center._refresh_panel(interaction, content="open"))

    assert interaction.edit_payloads[0]["content"] == "⏳ Loading Protection Center…"
    terminal = interaction.edit_payloads[-1]
    assert terminal["embed"] is None
    assert terminal["view"] is None
    assert "could not finish opening" in terminal["content"]
    assert "render-test" in terminal["content"]
    assert "stats" not in events


def test_refresh_panel_replaces_loading_when_final_edit_fails(monkeypatch) -> None:
    events: list[str] = []
    _install_refresh_fakes(monkeypatch, events)

    class FinalEditFailsInteraction(_Interaction):
        async def edit_original_response(self, **payload):
            self.events.append("edit")
            self.edit_payloads.append(dict(payload))
            self.last_payload = dict(payload)
            if len(self.edit_payloads) == 2:
                raise RuntimeError("discord rejected final panel")

    interaction = FinalEditFailsInteraction(events)
    monkeypatch.setattr(
        center,
        "log_interaction_failure",
        lambda *_args, **_kwargs: SimpleNamespace(error_id="edit-test"),
    )

    asyncio.run(center._refresh_panel(interaction, content="open"))

    assert interaction.edit_payloads[0]["content"] == "⏳ Loading Protection Center…"
    assert interaction.edit_payloads[1]["content"] == "open"
    terminal = interaction.edit_payloads[-1]
    assert terminal["embed"] is None
    assert terminal["view"] is None
    assert "could not finish opening" in terminal["content"]
    assert "edit-test" in terminal["content"]
    assert "stats" not in events
