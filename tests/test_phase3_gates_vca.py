from __future__ import annotations

"""Phase 3 Slices 4/5/7: Pro gates for Voice Safety, Live Captions,
AntiNuke Strict Lockdown, and External Safety Lookups.

Every gate is driven through its real call site with the membership status
stubbed (the test_membership_gate.py pattern), proving the four states:

- unconfigured -> allowed, exactly as before membership existed;
- configured + Pro -> allowed;
- configured + free + grandfathered -> allowed;
- configured + free, not grandfathered -> denied with the Pro embed and
  NO state change written.

Slice 7 (external lookups) has no grandfather by design (spec §2.5), so its
free state is always denied.
"""

import asyncio
from types import SimpleNamespace

import discord
import pytest

import stoney_verify.anti_nuke as anti_nuke
import stoney_verify.guild_context as guild_context
import stoney_verify.member_external_safety_sources as external_sources
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context
from stoney_verify.services import membership
import stoney_verify.commands_ext.member_command_center as member_center
import stoney_verify.commands_ext.public_live_captions as captions_panel
import stoney_verify.commands_ext.public_protection_center as protection_center
import stoney_verify.commands_ext.public_voice_safety as voice_safety_panel

GUILD_ID = 4242
USER_ID = 777
MONTHLY_SKU = 111222333


@pytest.fixture(autouse=True)
def _clean_membership_env(monkeypatch):
    monkeypatch.delenv(membership.PRO_MONTHLY_SKU_ENV, raising=False)
    monkeypatch.delenv(membership.PRO_YEARLY_SKU_ENV, raising=False)


def _ready_context(guild_id: int = GUILD_ID):
    config = {
        "guild_id": str(guild_id),
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
    return build_guild_context(config["guild_id"], GuildRuntimeConfig(config))


def _configure(monkeypatch, status, calls: list | None = None):
    """Arm membership with a stubbed per-guild status (no Discord/DB)."""
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))

    async def resolve(_guild_id, *, client=None):
        if calls is not None:
            calls.append((_guild_id, client))
        return status

    monkeypatch.setattr(membership, "guild_pro_status", resolve)

    async def fake_context(_guild_id, *, refresh=False):
        return _ready_context(_guild_id)

    monkeypatch.setattr(guild_context, "get_guild_context", fake_context)


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.sent: list = []

    async def defer(self, **_kwargs) -> None:
        self.deferred = True

    def is_done(self) -> bool:
        return self.deferred

    async def send_message(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))
        self.deferred = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))


def _interaction(*, guild=None, followup=None, response=None) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild if guild is not None else SimpleNamespace(id=GUILD_ID),
        user=SimpleNamespace(id=USER_ID),
        client=object(),
        response=response if response is not None else FakeResponse(),
        followup=followup if followup is not None else FakeFollowup(),
    )


def _denial_embed_from(sent: list) -> discord.Embed:
    for _content, kwargs in sent:
        embed = kwargs.get("embed")
        if isinstance(embed, discord.Embed):
            return embed
    raise AssertionError(f"no denial embed in sent messages: {sent!r}")


# ---------------------------------------------------------------------------
# Slice 5 — AntiNuke Strict Lockdown (_toggle_antinuke_strict_lockdown)
# ---------------------------------------------------------------------------

_STRICT_KEY = protection_center.STRICT_LOCKDOWN_KEY


def _patch_antinuke(monkeypatch, *, strict_on: bool):
    saves: list = []
    sent: list = []
    refreshed: list = []
    state = {
        "antinuke_enabled": True,
        "antinuke_mode": "contain",
        _STRICT_KEY: strict_on,
    }

    async def require_owner(_interaction):
        return True

    monkeypatch.setattr(protection_center, "_require_antinuke_owner", require_owner)

    async def get_settings(_guild_id, **_kwargs):
        return dict(state)

    async def save_settings(_guild_id, patch):
        saves.append(dict(patch))
        state.update(patch)
        return dict(state)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(anti_nuke, "save_antinuke_settings", save_settings)
    monkeypatch.setattr(anti_nuke, "antinuke_permission_health", lambda _g, _c: [])

    async def send_ephemeral(_interaction, content="", **kwargs):
        sent.append((content, kwargs))

    monkeypatch.setattr(protection_center, "_send_ephemeral", send_ephemeral)

    async def refresh_panel(_interaction, **kwargs):
        refreshed.append(kwargs)

    monkeypatch.setattr(protection_center, "_refresh_panel", refresh_panel)
    return saves, sent, refreshed


def _toggle_strict(interaction) -> None:
    asyncio.run(protection_center._toggle_antinuke_strict_lockdown(interaction))


def test_antinuke_strict_enable_allowed_when_unconfigured(monkeypatch):
    saves, sent, _refreshed = _patch_antinuke(monkeypatch, strict_on=False)
    _toggle_strict(_interaction())
    assert saves == [{_STRICT_KEY: True}]
    assert sent == []


def test_antinuke_strict_enable_allowed_when_pro(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    saves, sent, _refreshed = _patch_antinuke(monkeypatch, strict_on=False)
    _toggle_strict(_interaction())
    assert saves == [{_STRICT_KEY: True}]
    assert sent == []


def test_antinuke_strict_enable_denied_when_free(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    saves, sent, _refreshed = _patch_antinuke(monkeypatch, strict_on=False)
    _toggle_strict(_interaction())
    # Denied: no settings write, one ephemeral carrying the Pro embed.
    assert saves == []
    embed = _denial_embed_from(sent)
    description = str(embed.description or "")
    assert "`antinuke_strict` is a **Dank Shield Pro** feature." in description
    assert "`/dank home`" in description
    assert "Setup & Server Settings" in description


def test_antinuke_strict_disable_is_never_gated(monkeypatch):
    calls: list = []
    _configure(monkeypatch, membership.STATUS_FREE, calls)
    saves, sent, _refreshed = _patch_antinuke(monkeypatch, strict_on=True)
    _toggle_strict(_interaction())
    # Turning Strict OFF always works — and the gate is never even consulted.
    assert saves == [{_STRICT_KEY: False}]
    assert sent == []
    assert calls == []


# ---------------------------------------------------------------------------
# Slice 7 — External safety lookups (_show_external_sources)
# ---------------------------------------------------------------------------


def _patch_external(monkeypatch):
    inspect_calls: list = []
    replies: list = []
    panels: list = []
    monkeypatch.setattr(
        member_center, "_is_guild_owner_for_affiliations", lambda _i: True
    )

    async def reply_ephemeral(_interaction, content="", **kwargs):
        replies.append((content, kwargs))

    monkeypatch.setattr(member_center, "reply_ephemeral", reply_ephemeral)

    async def resolve(_guild, _target_id):
        return None, "unverified"

    monkeypatch.setattr(member_center, "_resolve_intelligence_member", resolve)

    async def inspect(_target_id, username=None):
        inspect_calls.append((_target_id, username))
        return []

    monkeypatch.setattr(external_sources, "inspect_external_sources", inspect)

    async def replace_panel(_interaction, *, embed=None, view=None):
        panels.append((embed, view))

    monkeypatch.setattr(member_center, "_replace_panel", replace_panel)
    return inspect_calls, replies, panels


def _show_external(interaction) -> None:
    asyncio.run(member_center._show_external_sources(interaction, 999888))


def test_external_lookups_allowed_when_unconfigured(monkeypatch):
    inspect_calls, replies, panels = _patch_external(monkeypatch)
    _show_external(_interaction())
    assert inspect_calls == [(999888, None)]
    assert len(panels) == 1
    assert replies == []


def test_external_lookups_allowed_when_pro(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    inspect_calls, replies, panels = _patch_external(monkeypatch)
    _show_external(_interaction())
    assert inspect_calls == [(999888, None)]
    assert len(panels) == 1
    assert replies == []


def test_external_lookups_denied_when_free_without_any_grandfather(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    inspect_calls, replies, panels = _patch_external(monkeypatch)
    _show_external(_interaction())
    # No grandfather exists for this feature: the provider fan-out never
    # runs and the only reply is the Pro denial embed.
    assert inspect_calls == []
    assert panels == []
    embed = _denial_embed_from(replies)
    description = str(embed.description or "")
    assert "`external_safety_lookups` is a **Dank Shield Pro** feature." in description
    assert "never enforcement evidence" in description


# ---------------------------------------------------------------------------
# Slice 4b — Live Captions (ServerLiveCaptionsView.control)
# ---------------------------------------------------------------------------


class _FakeCaptionManager:
    def __init__(self, *, active: bool = False) -> None:
        self._active = active
        self.started: list = []
        self.stopped: list = []

    def status(self, _scope_id):
        return {"active": self._active}

    def status_for_guild(self, _guild_id):
        return {"active": False}

    async def stop(self, scope_id, announce=True):
        self.stopped.append((scope_id, announce))

    async def start_server(self, **kwargs):
        self.started.append(kwargs)


def _patch_captions(monkeypatch, *, manager, output_channel):
    followups: list = []

    async def defer_update(_interaction):
        return None

    async def get_config(_guild_id, **_kwargs):
        return {}

    async def build_embed(_interaction):
        return discord.Embed(title="Live Captions")

    async def edit_original(_interaction, **_kwargs):
        return None

    async def followup(_interaction, content):
        followups.append(content)

    monkeypatch.setattr(captions_panel, "_defer_update", defer_update)
    monkeypatch.setattr(captions_panel, "_staff_authorized", lambda _i: True)
    monkeypatch.setattr(
        captions_panel,
        "ensure_community_voice_caption_manager",
        lambda _client: manager,
    )
    monkeypatch.setattr(captions_panel, "live_captions_enabled", lambda: True)
    monkeypatch.setattr(
        captions_panel, "_current_voice_channel", lambda _i: SimpleNamespace(id=555)
    )
    monkeypatch.setattr(captions_panel, "get_guild_config", get_config)
    monkeypatch.setattr(
        captions_panel, "_configured_output_channel", lambda _g, _c: output_channel
    )
    monkeypatch.setattr(
        captions_panel, "_voice_allowed_by_config", lambda _v, _c: (True, "")
    )
    monkeypatch.setattr(
        captions_panel, "_caption_output_missing_perms", lambda _d, _m: []
    )
    monkeypatch.setattr(
        captions_panel, "build_server_live_captions_embed", build_embed
    )
    monkeypatch.setattr(captions_panel, "_edit_original", edit_original)
    monkeypatch.setattr(captions_panel, "_followup", followup)
    return followups


def _run_captions_control(interaction) -> None:
    view = captions_panel.ServerLiveCaptionsView(USER_ID)
    button = next(
        item
        for item in view.children
        if getattr(item, "custom_id", None) == "dank:captions:server:control:v1"
    )
    asyncio.run(button.callback(interaction))


def _captions_interaction() -> SimpleNamespace:
    return _interaction(
        guild=SimpleNamespace(id=GUILD_ID, me=SimpleNamespace()),
    )


def test_captions_start_denied_when_free_and_not_grandfathered(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    manager = _FakeCaptionManager()
    followups = _patch_captions(monkeypatch, manager=manager, output_channel=None)
    interaction = _captions_interaction()
    _run_captions_control(interaction)
    # Denied before any session starts; the setup-guidance message is not
    # sent either — the Pro embed is the reply.
    assert manager.started == []
    assert followups == []
    embed = _denial_embed_from(interaction.followup.sent)
    description = str(embed.description or "")
    assert "`live_captions` is a **Dank Shield Pro** feature." in description
    assert "`/dank home`" in description


def test_captions_start_allowed_when_free_but_grandfathered(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    manager = _FakeCaptionManager()
    _patch_captions(
        monkeypatch, manager=manager, output_channel=SimpleNamespace(id=888)
    )
    _run_captions_control(_captions_interaction())
    # A configured output channel is the grandfather evidence: start runs.
    assert len(manager.started) == 1
    assert manager.started[0]["destination_channel_id"] == 888


def test_captions_start_allowed_when_pro(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    manager = _FakeCaptionManager()
    _patch_captions(
        monkeypatch, manager=manager, output_channel=SimpleNamespace(id=888)
    )
    _run_captions_control(_captions_interaction())
    assert len(manager.started) == 1


def test_captions_start_unconfigured_behaves_exactly_as_before(monkeypatch):
    manager = _FakeCaptionManager()
    followups = _patch_captions(monkeypatch, manager=manager, output_channel=None)
    interaction = _captions_interaction()
    _run_captions_control(interaction)
    # No gate at all while unconfigured: the historical setup guidance is
    # the reply and nothing starts.
    assert manager.started == []
    assert interaction.followup.sent == []
    assert any("output channel first" in message for message in followups)


def test_captions_stop_is_never_gated(monkeypatch):
    calls: list = []
    _configure(monkeypatch, membership.STATUS_FREE, calls)
    manager = _FakeCaptionManager(active=True)
    _patch_captions(monkeypatch, manager=manager, output_channel=None)
    _run_captions_control(_captions_interaction())
    assert len(manager.stopped) == 1
    assert manager.started == []
    assert calls == []


# ---------------------------------------------------------------------------
# Slice 4a — Voice Safety gate helper (_voice_safety_enable_gate)
# ---------------------------------------------------------------------------


def _run_vs_gate(cfg: dict):
    interaction = SimpleNamespace(client=object())
    guild = SimpleNamespace(id=GUILD_ID)
    return asyncio.run(
        voice_safety_panel._voice_safety_enable_gate(interaction, guild, cfg)
    )


def test_voice_safety_gate_unconfigured_proceeds_without_ack(monkeypatch):
    result, embed = _run_vs_gate({})
    assert result == "proceed"
    assert embed is None


def test_voice_safety_gate_pro_without_acknowledgment_asks(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    result, embed = _run_vs_gate({})
    assert result == "ack"
    assert embed is None


def test_voice_safety_gate_pro_with_acknowledgment_proceeds(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    cfg = {
        voice_safety_panel.VOICE_SAFETY_PAID_ACK: {
            "by": USER_ID,
            "at": "2026-11-18T00:00:00+00:00",
        }
    }
    result, embed = _run_vs_gate(cfg)
    assert result == "proceed"
    assert embed is None


def test_voice_safety_gate_free_is_denied(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    result, embed = _run_vs_gate({})
    assert result == "denied"
    assert embed is not None
    description = str(embed.description or "")
    assert "`voice_safety_ai` is a **Dank Shield Pro** feature." in description
    assert "`/dank home`" in description


def test_voice_safety_gate_free_but_grandfathered_proceeds(monkeypatch):
    calls: list = []
    _configure(monkeypatch, membership.STATUS_FREE, calls)
    # The spec's exact grandfather evidence: the saved flag is already on.
    result, embed = _run_vs_gate({voice_safety_panel.VOICE_SAFETY_ENABLED: True})
    assert result == "proceed"
    assert embed is None
    assert calls == []


# ---------------------------------------------------------------------------
# Slice 4a — Voice Safety staff toggle + acknowledgment view
# ---------------------------------------------------------------------------


class _FakeVoiceManager:
    def __init__(self) -> None:
        self.auto_started: list = []

    def status_for_guild(self, _guild_id):
        return {"active": False}

    async def maybe_auto_start_voice_safety(self, guild):
        self.auto_started.append(int(guild.id))

    async def stop(self, _scope_id):
        return None


def _patch_voice_safety(monkeypatch, *, cfg: dict):
    saves: list = []
    manager = _FakeVoiceManager()
    state = dict(cfg)

    async def get_config(_guild_id, **_kwargs):
        return dict(state)

    async def save(_interaction, patch):
        saves.append(dict(patch))
        state.update(patch)
        return dict(state)

    async def refresh_disclosure(_guild, _cfg, _enabled):
        return True

    async def embed(_interaction):
        return discord.Embed(title="Voice Safety")

    monkeypatch.setattr(voice_safety_panel, "get_guild_config", get_config)
    monkeypatch.setattr(voice_safety_panel, "_save", save)
    monkeypatch.setattr(
        voice_safety_panel, "_refresh_verify_disclosure", refresh_disclosure
    )
    monkeypatch.setattr(voice_safety_panel, "_embed", embed)
    monkeypatch.setattr(
        voice_safety_panel,
        "ensure_community_voice_caption_manager",
        lambda _client: manager,
    )
    import stoney_verify.setup_engine.verification_modes as verification_modes

    monkeypatch.setattr(
        verification_modes, "effective_verification_mode", lambda _g, _c: "disabled"
    )
    return saves, manager, state


def _vs_interaction() -> SimpleNamespace:
    interaction = _interaction()
    edits: list = []

    async def edit_original_response(**kwargs):
        edits.append(kwargs)

    interaction.edit_original_response = edit_original_response
    interaction.edits = edits
    return interaction


def _run_vs_toggle(interaction) -> None:
    view = voice_safety_panel.VoiceSafetyStaffView(USER_ID)
    button = next(
        item for item in view.children if getattr(item, "label", None) == "Enable / Disable Setup"
    )
    asyncio.run(button.callback(interaction))


def test_voice_safety_toggle_enable_unconfigured_saves_without_ack(monkeypatch):
    saves, manager, _state = _patch_voice_safety(monkeypatch, cfg={})
    _run_vs_toggle(_vs_interaction())
    # Exactly the historical behavior: the flag flips, no acknowledgment
    # key is written while membership is unconfigured.
    assert saves == [{voice_safety_panel.VOICE_SAFETY_ENABLED: True}]
    assert manager.auto_started == [GUILD_ID]


def test_voice_safety_toggle_enable_denied_when_free(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    saves, manager, _state = _patch_voice_safety(monkeypatch, cfg={})
    interaction = _vs_interaction()
    _run_vs_toggle(interaction)
    assert saves == []
    assert manager.auto_started == []
    embed = _denial_embed_from(interaction.followup.sent)
    assert "`voice_safety_ai` is a **Dank Shield Pro** feature." in str(
        embed.description or ""
    )


def test_voice_safety_toggle_enable_pro_without_ack_swaps_in_ack_view(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    saves, manager, _state = _patch_voice_safety(monkeypatch, cfg={})
    interaction = _vs_interaction()
    _run_vs_toggle(interaction)
    # Nothing is saved yet; the panel message becomes the acknowledgment
    # prompt instead.
    assert saves == []
    assert manager.auto_started == []
    assert len(interaction.edits) == 1
    assert isinstance(
        interaction.edits[0].get("view"), voice_safety_panel.VoiceSafetyPaidAckView
    )


def test_voice_safety_toggle_disable_is_never_gated(monkeypatch):
    calls: list = []
    _configure(monkeypatch, membership.STATUS_FREE, calls)
    saves, _manager, _state = _patch_voice_safety(
        monkeypatch, cfg={voice_safety_panel.VOICE_SAFETY_ENABLED: True}
    )
    _run_vs_toggle(_vs_interaction())
    assert saves == [{voice_safety_panel.VOICE_SAFETY_ENABLED: False}]
    assert calls == []


def test_voice_safety_ack_confirm_writes_acknowledgment_and_enables(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    saves, manager, state = _patch_voice_safety(monkeypatch, cfg={})
    interaction = _vs_interaction()
    view = voice_safety_panel.VoiceSafetyPaidAckView(USER_ID)
    button = next(
        item for item in view.children if "Enable Voice Safety" in str(getattr(item, "label", ""))
    )
    asyncio.run(button.callback(interaction))
    assert len(saves) == 1
    patch = saves[0]
    assert patch[voice_safety_panel.VOICE_SAFETY_ENABLED] is True
    ack = patch[voice_safety_panel.VOICE_SAFETY_PAID_ACK]
    assert ack["by"] == USER_ID
    assert isinstance(ack["at"], str) and ack["at"]
    assert state[voice_safety_panel.VOICE_SAFETY_ENABLED] is True
    assert manager.auto_started == [GUILD_ID]


def test_voice_safety_ack_written_only_on_the_configured_pro_path(monkeypatch):
    # Unconfigured enable (above) writes no ack; a Pro guild that already
    # acknowledged is not asked again and no second ack is written.
    _configure(monkeypatch, membership.STATUS_PRO)
    existing_ack = {"by": USER_ID, "at": "2026-11-18T00:00:00+00:00"}
    saves, manager, _state = _patch_voice_safety(
        monkeypatch, cfg={voice_safety_panel.VOICE_SAFETY_PAID_ACK: existing_ack}
    )
    _run_vs_toggle(_vs_interaction())
    assert saves == [{voice_safety_panel.VOICE_SAFETY_ENABLED: True}]
    assert manager.auto_started == [GUILD_ID]


def test_voice_safety_ack_cancel_changes_nothing(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    saves, manager, _state = _patch_voice_safety(monkeypatch, cfg={})
    interaction = _vs_interaction()
    view = voice_safety_panel.VoiceSafetyPaidAckView(USER_ID)
    button = next(
        item for item in view.children if getattr(item, "label", None) == "Cancel"
    )
    asyncio.run(button.callback(interaction))
    assert saves == []
    assert manager.auto_started == []
    # The panel is restored to the staff view.
    assert len(interaction.edits) == 1
    assert isinstance(
        interaction.edits[0].get("view"), voice_safety_panel.VoiceSafetyStaffView
    )
