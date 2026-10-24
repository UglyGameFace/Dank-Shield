"""Portable Embed Studio JSON, permission boundaries and Android modal UI."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path

import discord
import pytest

from stoney_verify.embed_studio_core import (
    MAX_PORTABLE_BYTES,
    StudioValidationError,
    export_portable_project,
    import_portable_project,
    new_project,
    validate_project,
)
from stoney_verify.embed_studio_ui import (
    StudioHomeView,
    StudioJsonMenu,
    StudioJsonUploadModal,
    StudioSession,
)


def _studio_project() -> dict:
    project = new_project()
    project["content"] = "Before the announcement"
    project["embeds"][0].update(
        title="Event", image="https://example.org/event.png",
        fields=[{"name": "Time", "value": "4:20", "inline": True}],
    )
    project["links"] = [{"label": "Details", "url": "https://example.org/event"}]
    return project


def test_studio_json_round_trip_has_exact_editor_state_without_message_targets():
    project = _studio_project()
    content = export_portable_project(project, guild_id=111)
    data = json.loads(content)
    assert data["format"] == "dank-shield-embed-studio"
    assert data["format_version"] == 1
    assert data["origin_guild_id"] == "111"
    assert "post_key" not in data and "channel_id" not in data
    assert import_portable_project(content, guild_id=111) == validate_project(project)
    assert import_portable_project(content, guild_id=222) == validate_project(project)


def test_roles_allowed_same_guild_but_rejected_from_other_guild():
    project = _studio_project()
    project["role_actions"] = [{"label": "Gaming", "role_id": "123456789012345678"}]
    assert import_portable_project(export_portable_project(project, guild_id=111), guild_id=111)["role_actions"]
    with pytest.raises(StudioValidationError, match="different server"):
        import_portable_project(export_portable_project(project, guild_id=111), guild_id=222)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(format="some-unrelated-export"),
    lambda d: d.update(format_version=2),
    lambda d: d.update(format_version=True),
    lambda d: d.update(origin_guild_id="invalid"),
    lambda d: d.update(extra_field="unexpected"),
    lambda d: d.update(project={"embeds": []}),
])
def test_bad_json_schema_is_rejected_before_draft_mutation(mutate):
    payload = json.loads(export_portable_project(_studio_project(), guild_id=42))
    mutate(payload)
    with pytest.raises(StudioValidationError):
        import_portable_project(json.dumps(payload).encode(), guild_id=42)


@pytest.mark.parametrize("data", [
    b"", b"{}", b"not json", b"\xff", b'{"n":NaN}',
    b'{"format":"x","format":"y"}',
    b" " * (MAX_PORTABLE_BYTES + 1),
])
def test_malformed_duplicate_or_oversized_json_rejected(data):
    with pytest.raises(StudioValidationError):
        import_portable_project(data, guild_id=1)


def test_never_includes_secrets_staff_ids_or_saved_template_library():
    project = _studio_project()
    value = export_portable_project(project, guild_id=123)
    assert b"drafts" not in value
    assert b"templates" not in value
    assert b"published" not in value
    assert b"message_id" not in value
    assert b"role_token" not in value
    assert b"owner_id" not in value


def test_mobile_home_json_menu_and_native_file_upload_component():
    session = StudioSession(guild_id=42, owner_id=55)
    home = StudioHomeView(session)
    menu = StudioJsonMenu(session)
    modal = StudioJsonUploadModal(session)
    assert "JSON Files" in {getattr(item, "label", "") for item in home.children}
    assert {"Import JSON File", "Export JSON File", "Back to Studio"} <= {
        getattr(item, "label", "") for item in menu.children
    }
    assert isinstance(modal.upload, discord.ui.FileUpload)
    assert len(modal.children) == 1
    assert isinstance(modal.children[0], discord.ui.Label)
    assert modal.upload.max_values == 1
    assert modal.upload.min_values == 1


def test_json_modal_stale_snapshot_and_no_publish_without_staff_confirmation():
    session = StudioSession(guild_id=42, owner_id=55)
    modal = StudioJsonUploadModal(session)
    session.apply(lambda project: project["embeds"][0].update(title="Newer"))
    assert session.project != modal.opened_project
    text = Path("stoney_verify/embed_studio_ui.py").read_text()
    block = text[text.index("class StudioJsonUploadModal("):text.index("class StarterSelect(")]
    assert "self.session.project != self.opened_project" in block
    assert "self.session.post_key = \"\"" in block
    assert "self.session.apply(replace_draft)" in block
    assert "message.send(" not in block and "channel.send(" not in block
    assert "interaction.response.defer(ephemeral=True, thinking=True)" in block


def test_json_export_preserves_valid_role_dropdown_and_multiple_embeds():
    project = _studio_project()
    project["role_mode"] = "dropdown"
    project["role_actions"] = [{"label": "Role", "role_id": "123456789012345678"}]
    project["embeds"].append(deepcopy(project["embeds"][0]))
    output = import_portable_project(export_portable_project(project, guild_id=42), guild_id=42)
    assert len(output["embeds"]) == 2
    assert output["role_mode"] == "dropdown"
    assert len(output["role_actions"]) == 1


def test_valid_modal_attachment_import_replaces_draft_but_never_published_post(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    class Attachment:
        filename = "designer.JSON"
        def __init__(self, content):
            self.content = content
            self.size = len(content)
        async def read(self, *, use_cached=False):
            assert use_cached is True
            return self.content

    class Response:
        deferred = False
        async def defer(self, **kwargs):
            assert kwargs == {"ephemeral": True, "thinking": True}
            self.deferred = True
        def is_done(self):
            return self.deferred

    imported = _studio_project()
    imported["embeds"][0]["title"] = "Imported correctly"
    session = StudioSession(guild_id=42, owner_id=55, channel_id=123, post_key="123:456")
    modal = StudioJsonUploadModal(session)
    attachment = Attachment(export_portable_project(imported, guild_id=42))
    monkeypatch.setattr(discord.ui.FileUpload, "values", property(lambda _self: [attachment]))
    monkeypatch.setattr(ui, "_staff", lambda _interaction: True)
    observed = []
    async def home(_interaction, state, *, notice=""):
        observed.append((deepcopy(state.project), state.channel_id, state.post_key, notice))
    monkeypatch.setattr(ui, "_home", home)
    response = Response()
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=42), user=SimpleNamespace(id=55),
        response=response,
    )
    asyncio.run(modal.on_submit(interaction))
    assert response.deferred
    assert session.project["embeds"][0]["title"] == "Imported correctly"
    assert session.post_key == "" and session.channel_id == 123
    assert session.active == 0 and session.dirty
    assert observed and "private draft" in observed[0][3]


def test_invalid_uploaded_file_leaves_editor_untouched(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    class Attachment:
        filename = "invalid.json"
        size = 9
        async def read(self, **kwargs):
            return b"not json!"

    class Response:
        deferred = False
        async def defer(self, **kwargs):
            self.deferred = True
        def is_done(self):
            return self.deferred

    session = StudioSession(guild_id=42, owner_id=55, channel_id=123, post_key="123:456")
    snapshot = deepcopy(session.project)
    modal = StudioJsonUploadModal(session)
    monkeypatch.setattr(discord.ui.FileUpload, "values", property(lambda _self: [Attachment()]))
    monkeypatch.setattr(ui, "_staff", lambda _interaction: True)
    results = []
    async def say(_interaction, content, **kwargs):
        results.append(content)
    monkeypatch.setattr(ui, "_say", say)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=42), user=SimpleNamespace(id=55),
        response=Response(),
    )
    asyncio.run(modal.on_submit(interaction))
    assert results and "Import rejected" in results[0]
    assert session.project == snapshot
    assert session.channel_id == 123 and session.post_key == "123:456"
    assert not session.dirty


def test_stale_file_upload_never_overwrites_newer_changes(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    class Attachment:
        filename = "draft.json"
        def __init__(self, data):
            self.data = data
            self.size = len(data)
        async def read(self, **kwargs):
            return self.data

    class Response:
        async def defer(self, **kwargs):
            return None
        def is_done(self):
            return True

    session = StudioSession(guild_id=42, owner_id=55)
    modal = StudioJsonUploadModal(session)
    session.apply(lambda project: project["embeds"][0].update(title="Newer edit"))
    attachment = Attachment(export_portable_project(_studio_project(), guild_id=42))
    monkeypatch.setattr(discord.ui.FileUpload, "values", property(lambda _self: [attachment]))
    monkeypatch.setattr(ui, "_staff", lambda _interaction: True)
    results = []
    async def say(_interaction, content, **kwargs):
        results.append(content)
    monkeypatch.setattr(ui, "_say", say)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=42), user=SimpleNamespace(id=55),
        response=Response(),
    )
    asyncio.run(modal.on_submit(interaction))
    assert results and "draft changed" in results[0].lower()
    assert session.project["embeds"][0]["title"] == "Newer edit"
