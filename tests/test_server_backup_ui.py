from __future__ import annotations

"""Phase 3 slices 3a/3b: Server Structure Backups panel.

Gate tests drive the REAL ``membership_gate.premium_decision`` (env +
status stubs, the ``test_membership_gate.py`` pattern) so the four states
— unconfigured / pro / free without grandfather / free with grandfather —
are proven end to end at the panel. The backup service and the restore
engine are faked at the UI module's references.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import discord
import pytest

import stoney_verify.guild_context as guild_context
from stoney_verify import navigation_registry as registry
from stoney_verify import server_backup_ui as ui
from stoney_verify.commands_ext import public_command_surface_v2 as surface
from stoney_verify.config_history_ui import ConfigHistoryView
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context
from stoney_verify.help_guides import HELP_GUIDES
from stoney_verify.services import membership
from stoney_verify.services import server_backup_restore as restore_engine

GUILD_ID = 123
MONTHLY_SKU = 111222333


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def button_labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


def find_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "label", "") or "") == label
    ]
    assert len(matches) == 1, f"expected exactly one {label!r} button, got {len(matches)}"
    return matches[0]


def embed_text(embed: discord.Embed) -> str:
    parts = [str(embed.title or ""), str(embed.description or "")]
    for field in embed.fields:
        parts.append(f"{field.name}\n{field.value}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.modals: list[Any] = []
        self.messages: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def is_done(self) -> bool:
        return True

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append((args, kwargs))

    async def send_modal(self, modal: Any) -> None:
        self.modals.append(modal)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def send(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self, *, user_id: int = 77) -> None:
        self.guild = SimpleNamespace(id=GUILD_ID, name="Test Guild", owner_id=77)
        self.user = SimpleNamespace(id=user_id)
        self.client = object()
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.original_edits: list[dict[str, Any]] = []

    async def edit_original_response(self, **kwargs: Any) -> None:
        self.original_edits.append(dict(kwargs))


class Store:
    """Fake backup service + fake restore engine state."""

    def __init__(self) -> None:
        self.ready = True
        self.rows: list[dict[str, Any]] = []
        self.plan: list[dict[str, Any]] = []
        self.preflight_errors: list[str] = []
        self.result = restore_engine.RestoreResult(ok=True, applied=[])
        self.create_calls: list[dict[str, Any]] = []
        self.delete_calls: list[int] = []
        self.apply_calls: list[dict[str, Any]] = []
        self.plan_modes: list[str] = []
        self.emit_progress = False


def backup_row(backup_id: int = 5, **overrides: Any) -> dict[str, Any]:
    row = {
        "backup_id": backup_id,
        "guild_id": GUILD_ID,
        "created_by": 77,
        "label": "Before redesign",
        "created_at": "2026-11-18T10:00:00+00:00",
        "schema_version": 1,
        "snapshot_bytes": 2048,
        "restore_state": None,
        "snapshot": {"schema_version": 1, "roles": [], "categories": [], "channels": []},
    }
    row.update(overrides)
    return row


def plan_row(kind: str, key: str, action: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "kind": kind,
        "key": key,
        "action": action,
        "status": "changed" if action in ("create", "update") else "unchanged",
        "target_id": None,
        "before": {},
        "after": {},
        "blockers": [],
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _clean_membership_env(monkeypatch):
    monkeypatch.delenv(membership.PRO_MONTHLY_SKU_ENV, raising=False)
    monkeypatch.delenv(membership.PRO_YEARLY_SKU_ENV, raising=False)


@pytest.fixture()
def panel(monkeypatch):
    store = Store()
    edits: list[tuple[discord.Embed, Any]] = []
    privates: list[tuple[Any, Any]] = []

    async def fake_require(_interaction):
        return True

    async def fake_defer(_interaction):
        return None

    async def fake_edit(_interaction, *, embed, view):
        edits.append((embed, view))

    async def fake_private(_interaction, content=None, *, embed=None):
        privates.append((content, embed))

    monkeypatch.setattr(ui, "_require_setup_permission", fake_require)
    monkeypatch.setattr(ui, "_safe_defer_update", fake_defer)
    monkeypatch.setattr(ui, "_edit", fake_edit)
    monkeypatch.setattr(ui, "_private", fake_private)

    service = ui.backup_service
    monkeypatch.setattr(service, "backups_ready_sync", lambda: store.ready)

    async def fake_list(guild_id, *, limit=10):
        return tuple(store.rows[:limit])

    async def fake_get(guild_id, backup_id):
        for row in store.rows:
            if row["backup_id"] == backup_id:
                return dict(row)
        return None

    async def fake_create(guild, *, created_by, label):
        store.create_calls.append({"created_by": created_by, "label": label})
        row = backup_row(backup_id=99, label=label, created_by=created_by)
        store.rows.insert(0, row)
        return dict(row)

    async def fake_delete(guild_id, backup_id):
        store.delete_calls.append(backup_id)
        before = len(store.rows)
        store.rows = [r for r in store.rows if r["backup_id"] != backup_id]
        return len(store.rows) != before

    monkeypatch.setattr(service, "list_backups", fake_list)
    monkeypatch.setattr(service, "get_backup", fake_get)
    monkeypatch.setattr(service, "create_backup", fake_create)
    monkeypatch.setattr(service, "delete_backup", fake_delete)

    engine = ui.restore_engine

    def fake_plan(guild, snapshot, *, mode="all", **kwargs):
        store.plan_modes.append(mode)
        return [dict(row) for row in store.plan]

    async def fake_preflight(guild, plan, *, delete_confirmed=False):
        return list(store.preflight_errors)

    async def fake_apply(guild, plan, **kwargs):
        store.apply_calls.append(dict(kwargs))
        if store.emit_progress:
            total = sum(
                1
                for row in plan
                if row.get("action") in ("create", "update")
                and row.get("status") == "changed"
            )
            await kwargs["progress"](total, total)
        return store.result

    monkeypatch.setattr(engine, "build_restore_plan", fake_plan)
    monkeypatch.setattr(engine, "preflight_restore", fake_preflight)
    monkeypatch.setattr(engine, "apply_restore_plan", fake_apply)

    return SimpleNamespace(store=store, edits=edits, privates=privates)


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


def configure_membership(monkeypatch, status: str) -> None:
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))

    async def fake_status(_guild_id, *, client=None):
        return status

    monkeypatch.setattr(membership, "guild_pro_status", fake_status)

    async def fake_context(_guild_id, *, refresh=False):
        return _ready_context(_guild_id)

    monkeypatch.setattr(guild_context, "get_guild_context", fake_context)


def render_panel(panel, **kwargs):
    interaction = FakeInteraction()
    run(ui._render_panel(interaction, **kwargs))
    assert panel.edits, "panel did not render"
    return panel.edits[-1]


# ---------------------------------------------------------------------------
# Panel render states
# ---------------------------------------------------------------------------


def test_panel_renders_empty_state(panel):
    embed, view = render_panel(panel)
    text = embed_text(embed)
    assert "No structure backups yet" in text
    assert "Backups & History" in text  # copy distinguishes the two backup kinds
    assert "Create Backup" in button_labels(view)
    assert find_button(view, "Restore").disabled is True
    assert find_button(view, "Delete Backup").disabled is True


def test_panel_renders_backup_list_with_selection(panel):
    panel.store.rows = [
        backup_row(5),
        backup_row(6, label="", restore_state={"status": "done"}),
    ]
    embed, view = render_panel(panel, selected_id=5)
    text = embed_text(embed)
    assert "Saved backups (2/10)" in text
    assert "Before redesign" in text
    assert "Backup #6" in text
    assert "last restore finished" in text
    assert "Selected: Before redesign" in text
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1 and len(selects[0].options) == 2
    assert find_button(view, "Restore").disabled is False


def test_panel_storage_unavailable(panel):
    panel.store.ready = False
    embed, view = render_panel(panel)
    assert "aren't available yet" in embed_text(embed)
    assert button_labels(view) == {"Backups & History", "Close"}


# ---------------------------------------------------------------------------
# Gate behavior at the panel (real premium_decision)
# ---------------------------------------------------------------------------


def test_panel_locked_for_free_guild_without_backups(panel, monkeypatch):
    configure_membership(monkeypatch, membership.STATUS_FREE)
    embed, view = render_panel(panel)
    labels = button_labels(view)
    assert "Create Backup" not in labels
    assert "Restore" not in labels
    assert "Delete Backup" in labels  # viewing/deleting stays free
    assert "Dank Shield Pro" in embed_text(embed)


def test_create_denied_for_free_guild_without_backups(panel, monkeypatch):
    configure_membership(monkeypatch, membership.STATUS_FREE)
    panel.store.rows = [backup_row(5)]
    # Render locked state from a stale pre-lock view: rebuild an unlocked
    # view directly to prove the callback itself re-checks the gate.
    panel.store.rows = []
    view = ui.ServerBackupsView([], selected_id=0, locked=False)
    interaction = FakeInteraction()
    run(find_button(view, "Create Backup").callback(interaction))
    assert interaction.response.modals == []
    assert panel.store.create_calls == []
    assert panel.privates, "expected an ephemeral Pro denial"
    _content, denial = panel.privates[-1]
    assert denial is not None and denial.title == "⭐ Dank Shield Pro"


def test_create_allowed_when_unconfigured(panel):
    view = ui.ServerBackupsView([], selected_id=0, locked=False)
    interaction = FakeInteraction()
    run(find_button(view, "Create Backup").callback(interaction))
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    modal.label_input._value = "My label"
    run(modal.on_submit(FakeInteraction()))
    assert panel.store.create_calls == [{"created_by": 77, "label": "My label"}]
    embed, _view = panel.edits[-1]
    assert "Backup saved" in embed_text(embed)


def test_create_allowed_for_pro_guild(panel, monkeypatch):
    configure_membership(monkeypatch, membership.STATUS_PRO)
    view = ui.ServerBackupsView([], selected_id=0, locked=False)
    interaction = FakeInteraction()
    run(find_button(view, "Create Backup").callback(interaction))
    assert len(interaction.response.modals) == 1


def test_create_allowed_for_free_guild_with_grandfather_backup(panel, monkeypatch):
    configure_membership(monkeypatch, membership.STATUS_FREE)
    panel.store.rows = [backup_row(5)]
    view = ui.ServerBackupsView(panel.store.rows, selected_id=5, locked=False)
    interaction = FakeInteraction()
    run(find_button(view, "Create Backup").callback(interaction))
    assert len(interaction.response.modals) == 1


def test_delete_is_never_gated(panel, monkeypatch):
    # Force the gate to deny everything: the panel renders locked, yet the
    # delete flow still completes — delete never goes through the gate.
    async def always_denied(**kwargs):
        return SimpleNamespace(denied=True)

    monkeypatch.setattr(ui.membership_gate, "premium_decision", always_denied)
    panel.store.rows = [backup_row(5)]
    embed, view = render_panel(panel, selected_id=5)
    assert "Create Backup" not in button_labels(view)
    delete_button = find_button(view, "Delete Backup")
    assert delete_button.disabled is False
    interaction = FakeInteraction()
    run(delete_button.callback(interaction))
    _embed, confirm_view = panel.edits[-1]
    run(find_button(confirm_view, "Delete Backup").callback(FakeInteraction()))
    assert panel.store.delete_calls == [5]
    embed, _view = panel.edits[-1]
    assert "Backup deleted." in embed_text(embed)


# ---------------------------------------------------------------------------
# Restore flow state machine (fake engine)
# ---------------------------------------------------------------------------


def _sample_plan() -> list[dict[str, Any]]:
    return [
        plan_row("role", "role:1", "create"),
        plan_row("channel", "chan:2", "create"),
        plan_row("channel", "chan:3", "update"),
        plan_row("role", "role:4", "skip"),
        plan_row("channel", "chan:5", "blocked", status="failed",
                 blockers=["Its category would exceed Discord's 50-channel limit (R4)."]),
        plan_row("channel", "chan:6", "delete_candidate", status="unchanged"),
        plan_row("guild_setting", "guild_setting:verification_level", "update",
                 after={"verification_level": 2}, before={"verification_level": 3}),
    ]


def _open_mode_view(panel) -> ui.RestoreModeView:
    panel.store.rows = [backup_row(5)]
    interaction = FakeInteraction()
    run(ui._open_restore_flow(interaction, 5))
    _embed, view = panel.edits[-1]
    assert isinstance(view, ui.RestoreModeView)
    return view


def test_restore_plan_preview_counts(panel):
    panel.store.plan = _sample_plan()
    mode_view = _open_mode_view(panel)
    run(find_button(mode_view, "Preview Restore Plan").callback(FakeInteraction()))
    embed, view = panel.edits[-1]
    assert isinstance(view, ui.RestorePlanView)
    text = embed_text(embed)
    assert "**2** to create" in text
    assert "**2** to update" in text  # chan:3 plus the guild_setting row
    assert "**1** already match" in text
    assert "**1** blocked" in text
    assert "50-channel limit" in text
    assert "verification_level" in text
    assert "Extra items not in this backup: 1" in text
    assert panel.store.plan_modes == ["all"]


def test_restore_missing_mode_passes_through(panel):
    panel.store.plan = [plan_row("channel", "chan:2", "create")]
    mode_view = _open_mode_view(panel)
    mode_view.mode = "missing"
    run(find_button(mode_view, "Preview Restore Plan").callback(FakeInteraction()))
    assert panel.store.plan_modes == ["missing"]


def test_plan_view_hides_continue_when_nothing_actionable(panel):
    panel.store.plan = [plan_row("role", "role:4", "skip")]
    mode_view = _open_mode_view(panel)
    run(find_button(mode_view, "Preview Restore Plan").callback(FakeInteraction()))
    _embed, view = panel.edits[-1]
    assert "Run Safety Check" not in button_labels(view)


def test_preflight_failure_stops_before_confirm(panel):
    panel.store.plan = _sample_plan()
    panel.store.preflight_errors = ["chan:3: changed after the preview (name)."]
    mode_view = _open_mode_view(panel)
    run(find_button(mode_view, "Preview Restore Plan").callback(FakeInteraction()))
    _embed, plan_view = panel.edits[-1]
    run(find_button(plan_view, "Run Safety Check").callback(FakeInteraction()))
    embed, view = panel.edits[-1]
    assert isinstance(view, ui.PreflightFailedView)
    assert "changed after the preview" in embed_text(embed)
    assert panel.store.apply_calls == []


def _reach_confirm(panel) -> ui.RestoreConfirmView:
    panel.store.plan = _sample_plan()
    mode_view = _open_mode_view(panel)
    run(find_button(mode_view, "Preview Restore Plan").callback(FakeInteraction()))
    _embed, plan_view = panel.edits[-1]
    run(find_button(plan_view, "Run Safety Check").callback(FakeInteraction()))
    _embed, view = panel.edits[-1]
    assert isinstance(view, ui.RestoreConfirmView)
    return view


def test_deletes_require_separate_arm(panel):
    confirm_view = _reach_confirm(panel)
    assert confirm_view.delete_armed is False
    embed, _ = panel.edits[-1]
    assert "will be **kept**" in embed_text(embed)
    run(find_button(confirm_view, "Confirm Restore").callback(FakeInteraction()))
    assert len(panel.store.apply_calls) == 1
    assert panel.store.apply_calls[0]["delete_confirmed"] is False


def test_deletes_run_only_when_armed(panel):
    confirm_view = _reach_confirm(panel)
    toggle = next(
        c for c in confirm_view.children
        if isinstance(c, discord.ui.Button) and "Delete" in str(c.label or "")
    )
    run(toggle.callback(FakeInteraction()))
    embed, armed_view = panel.edits[-1]
    assert isinstance(armed_view, ui.RestoreConfirmView)
    assert armed_view.delete_armed is True
    assert "WILL be deleted" in embed_text(embed)
    run(find_button(armed_view, "Confirm Restore").callback(FakeInteraction()))
    assert panel.store.apply_calls[0]["delete_confirmed"] is True


def test_apply_progress_and_result_rendering(panel):
    panel.store.plan = _sample_plan()
    panel.store.emit_progress = True
    panel.store.result = restore_engine.RestoreResult(
        ok=True,
        applied=["role:1", "chan:2", "chan:3"],
        pre_restore_backup_id=9,
        skipped_deletes=["chan:6"],
    )
    confirm_view = _reach_confirm(panel)
    interaction = FakeInteraction()
    run(find_button(confirm_view, "Confirm Restore").callback(interaction))
    assert interaction.original_edits, "progress callback should edit the message"
    embed, view = panel.edits[-1]
    assert isinstance(view, ui.RestoreResultView)
    text = embed_text(embed)
    assert "Restore Complete" in text
    assert "Applied changes (3)" in text
    assert "backup #9" in text
    assert "were kept because deletions were not confirmed" in text
    call = panel.store.apply_calls[0]
    assert call["plan_mode"] == "all"
    assert call["backup_id"] == 5


def test_apply_failure_result_rendering(panel):
    panel.store.plan = _sample_plan()
    panel.store.result = restore_engine.RestoreResult(
        ok=False,
        applied=["role:1"],
        failure="chan:2: Discord rejected the change (Forbidden: no access).",
        compensated=["role:1"],
        residual=[{"key": "chan:3"}],
    )
    confirm_view = _reach_confirm(panel)
    run(find_button(confirm_view, "Confirm Restore").callback(FakeInteraction()))
    embed, _view = panel.edits[-1]
    text = embed_text(embed)
    assert "Restore Failed" in text
    assert "Discord rejected the change" in text
    assert "rolled back" in text
    assert "not applied" in text


def test_restore_confirm_gate_denied_when_grandfather_gone(panel, monkeypatch):
    configure_membership(monkeypatch, membership.STATUS_FREE)
    confirm_view = _reach_confirm(panel)
    panel.store.rows = []  # the backup vanished mid-flow; nothing grandfathers
    run(find_button(confirm_view, "Confirm Restore").callback(FakeInteraction()))
    assert panel.store.apply_calls == []
    _content, denial = panel.privates[-1]
    assert denial is not None and denial.title == "⭐ Dank Shield Pro"


# ---------------------------------------------------------------------------
# Resume offers
# ---------------------------------------------------------------------------


def test_resume_offered_for_stale_running_cursor(panel):
    stale = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    panel.store.rows = [
        backup_row(5, restore_state={"status": "running", "started_at": stale})
    ]
    _embed, view = render_panel(panel, selected_id=5)
    assert "Resume Restore" in button_labels(view)


def test_resume_not_offered_for_fresh_running_cursor(panel):
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    panel.store.rows = [
        backup_row(5, restore_state={"status": "running", "started_at": fresh})
    ]
    embed, view = render_panel(panel, selected_id=5)
    assert "Resume Restore" not in button_labels(view)
    assert "restore in progress" in embed_text(embed)


def test_resume_offered_for_cancelled_cursor(panel):
    panel.store.rows = [backup_row(5, restore_state={"status": "cancelled"})]
    embed, view = render_panel(panel, selected_id=5)
    assert "Resume Restore" in button_labels(view)
    assert "restore stopped — resumable" in embed_text(embed)


def test_resume_flow_preselects_cursor_mode(panel):
    panel.store.rows = [
        backup_row(5, restore_state={"status": "cancelled", "plan_mode": "missing"})
    ]
    interaction = FakeInteraction()
    run(ui._open_restore_flow(interaction, 5, resume=True))
    embed, view = panel.edits[-1]
    assert isinstance(view, ui.RestoreModeView)
    assert view.mode == "missing"
    assert "Resuming" in embed_text(embed)


# ---------------------------------------------------------------------------
# Slice 3b: registry destination, route, help guide, Config History button
# ---------------------------------------------------------------------------


def test_registry_destination_registered():
    feature = registry.feature_by_key("server_backups")
    assert feature is not None
    assert feature.manager_only is True
    assert feature.category == "setup"
    assert "server_backups" in surface._FEATURE_ROUTE_KEYS
    assert "server_backups" in HELP_GUIDES
    assert registry.search_features("server backup")[0].key == "server_backups"


def test_recent_update_entry_appended_after_pinned_eight():
    keys = [item.key for item in registry.RECENT_UPDATES]
    assert keys[:8] == [
        "protection", "community_hub", "verification_reminders",
        "verification", "embed_studio", "share_router", "share_downloads",
        "action_safety_schedule",
    ]
    # server_backups was appended after the pinned eight; later merged
    # entries (e.g. the 2026-11-19 picker note) may follow it.
    assert "server_backups" in keys[8:]
    assert len(keys) == len(set(keys))


def test_config_history_view_has_structure_backups_button():
    view = ConfigHistoryView([])
    labels = button_labels(view)
    assert "Server Structure Backups" in labels
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert len(buttons) <= 9
