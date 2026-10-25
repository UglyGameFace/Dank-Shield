"""Durable Embed Studio draft recovery without a second persistence owner."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from stoney_verify.embed_studio_core import (
    MAX_DRAFTS, StudioValidationError, draft_snapshot, library_payload,
    new_project, parse_library,
)
from stoney_verify.embed_studio_ui import (
    StudioHomeView, StudioSession, _autosave, _restore_snapshot,
)


def _library() -> dict:
    return parse_library({"embed_studio_v1": {
        "revision": 7,
        "templates": {"template": {"name": "Rules", "project": new_project()}},
        "published": {"99:100": {"channel_id": "99", "message_id": "100"}},
    }})


def _save(lib: dict, owner: int, revision: int = 0, *, now: int = 1800000000):
    return draft_snapshot(
        lib, owner_id=owner, expected_version=revision,
        project=new_project(), now=now, channel_id=99, active=0,
        post_key="99:100",
    )


def test_draft_migration_preserves_templates_and_tracked_posts():
    lib = _library()
    assert lib["drafts"] == {}
    assert _save(lib, 10) == 1
    stored = library_payload(lib)
    assert stored["templates"] == lib["templates"]
    assert stored["published"] == lib["published"]
    parsed = parse_library({"embed_studio_v1": stored})
    assert parsed["drafts"]["10"]["version"] == 1
    assert parsed["drafts"]["10"]["post_key"] == "99:100"
    assert parsed["revision"] == 8


def test_same_user_stale_editor_cannot_replace_newer_saved_work():
    lib = _library()
    _save(lib, 10)
    snapshot = deepcopy(lib)
    assert _save(lib, 10, 1) == 2
    with pytest.raises(StudioValidationError, match="another Studio session"):
        _save(lib, 10, 1)
    assert lib["drafts"]["10"]["version"] == 2
    assert snapshot["drafts"]["10"]["version"] == 1


def test_draft_limits_and_expiry_do_not_evict_live_edits():
    lib = _library()
    for owner in range(1, MAX_DRAFTS + 1):
        _save(lib, owner)
    with pytest.raises(StudioValidationError, match="Eight editor drafts"):
        _save(lib, 999)
    # Stale other staff draft can be reclaimed without touching the owner's.
    lib["drafts"]["1"]["updated_at"] = 1
    _save(lib, 999)
    assert "1" not in lib["drafts"]
    assert "8" in lib["drafts"] and "999" in lib["drafts"]


def test_draft_validation_prevents_unbounded_or_invalid_editor_data():
    lib = _library()
    with pytest.raises(StudioValidationError, match="Invalid draft page"):
        draft_snapshot(
            lib, owner_id=33, expected_version=0,
            project=new_project(), active=300, now=1800000000,
        )
    assert lib["drafts"] == {}
    with pytest.raises(StudioValidationError, match="Invalid draft owner"):
        draft_snapshot(
            lib, owner_id=0, expected_version=0,
            project=new_project(), now=1800000000,
        )
    with pytest.raises(StudioValidationError):
        draft_snapshot(
            lib, owner_id=33, expected_version=0,
            project={"content": "", "embeds": []}, now=1800000000,
        )


def test_restore_only_own_record_and_tracked_edit_target():
    lib = _library()
    _save(lib, 10)
    lib["drafts"]["10"]["project"]["embeds"][0]["title"] = "Recovered"
    session = StudioSession(guild_id=5, owner_id=10)
    assert _restore_snapshot(session, lib)
    assert session.project["embeds"][0]["title"] == "Recovered"
    assert session.channel_id == 99 and session.post_key == "99:100"
    assert session.saved_version == 1 and not session.dirty
    other = StudioSession(guild_id=5, owner_id=11)
    assert not _restore_snapshot(other, lib)
    lib["published"] = {}
    assert _restore_snapshot(session, lib)
    assert session.post_key == ""


def test_mobile_editor_exposes_explicit_safe_reload():
    view = StudioHomeView(StudioSession(guild_id=5, owner_id=10))
    labels = {getattr(item, "label", "") for item in view.children}
    assert {"Saved Designs", "Preview & Post"} <= labels
    from stoney_verify.embed_studio_ui import StudioSavedTools
    assert {"Restore My Draft", "Saved Post Layouts"} <= {
        item.label for item in StudioSavedTools(StudioSession(guild_id=5, owner_id=10)).children
    }


def test_autosave_uses_cas_and_conflicting_open_editors_fail_closed(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    state = {"value": library_payload(_library())}
    saves = []
    async def load(guild_id):
        assert guild_id == 5
        source = {"embed_studio_v1": deepcopy(state["value"])}
        return source, parse_library(source)

    async def save(guild_id, source, lib):
        assert guild_id == 5
        if source.get("embed_studio_v1") != state["value"]:
            return False
        state["value"] = library_payload(lib)
        saves.append(deepcopy(state["value"]))
        return True

    monkeypatch.setattr(ui, "_load_library", load)
    monkeypatch.setattr(ui, "_save_library", save)
    interaction = SimpleNamespace(response=SimpleNamespace(is_done=lambda: True))

    first = StudioSession(guild_id=5, owner_id=10)
    first.apply(lambda p: p["embeds"][0].update(title="Initial"))
    assert asyncio.run(_autosave(interaction, first)) == ""
    assert not first.dirty and first.saved_version == 1
    second = StudioSession(guild_id=5, owner_id=10)
    assert _restore_snapshot(second, parse_library({"embed_studio_v1": state["value"]}))
    first.apply(lambda p: p["embeds"][0].update(title="Latest"))
    second.apply(lambda p: p["embeds"][0].update(title="Stale edit"))
    assert asyncio.run(_autosave(interaction, first)) == ""
    error = asyncio.run(_autosave(interaction, second))
    assert "another Studio session" in error
    assert second.dirty
    assert state["value"]["drafts"]["10"]["project"]["embeds"][0]["title"] == "Latest"
    assert len(saves) == 2


def test_autosave_backend_outage_preserves_local_changes(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    async def offline(_guild_id):
        raise RuntimeError("do not expose a credential")
    monkeypatch.setattr(ui, "_load_library", offline)
    interaction = SimpleNamespace(response=SimpleNamespace(is_done=lambda: True))
    session = StudioSession(guild_id=5, owner_id=10)
    session.apply(lambda p: p["embeds"][0].update(title="Still editable"))
    result = asyncio.run(_autosave(interaction, session))
    assert "Autosave unavailable" in result and "credential" not in result
    assert session.dirty and session.project["embeds"][0]["title"] == "Still editable"
