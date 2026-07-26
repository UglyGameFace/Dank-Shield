from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path


REPLACEMENTS = {
    Path("stoney_verify/profile_signature_studio.py"): (
        '''    payload: dict[str, Any] = {
        "content": content,
        "embed": embed,
        "view": view,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if file is not None:
        payload["file"] = file
''',
        '''    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if file is not None:
        payload["file"] = file
''',
    ),
    Path("stoney_verify/commands_ext/public_command_hub.py"): (
        '''    payload = {
        "content": content,
        "embed": embed,
        "view": view,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
''',
        '''    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
''',
    ),
    Path("stoney_verify/welcome_setup_ui.py"): (
        '''    payload = {
        "content": content,
        "embed": embed,
        "view": view,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
''',
        '''    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
''',
    ),
}

files: dict[str, str] = {}
for path, (old, new) in REPLACEMENTS.items():
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise SystemExit(f"expected one helper payload block in {path}, found {source.count(old)}")
    files[str(path)] = source.replace(old, new, 1)

files["tests/test_ui_private_followup_payloads.py"] = '''from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from stoney_verify import profile_signature_studio
from stoney_verify.commands_ext import public_command_hub
from stoney_verify import welcome_setup_ui


class _Response:
    def __init__(self, *, done: bool) -> None:
        self._done = done
        self.sent: dict[str, Any] | None = None

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, **payload: Any) -> None:
        self.sent = payload


class _Followup:
    def __init__(self) -> None:
        self.sent: dict[str, Any] | None = None

    async def send(self, **payload: Any) -> None:
        self.sent = payload


class _Interaction:
    def __init__(self, *, done: bool) -> None:
        self.response = _Response(done=done)
        self.followup = _Followup()


PrivateSender = Callable[..., Awaitable[None]]


def _run(sender: PrivateSender, **kwargs: Any) -> dict[str, Any]:
    interaction = _Interaction(done=True)
    asyncio.run(sender(interaction, **kwargs))
    assert interaction.followup.sent is not None
    return interaction.followup.sent


def test_profile_preview_followup_omits_none_view() -> None:
    embed = object()
    file = object()
    payload = _run(profile_signature_studio._private, embed=embed, file=file)

    assert payload["embed"] is embed
    assert payload["file"] is file
    assert "view" not in payload
    assert "content" not in payload


def test_content_only_followups_omit_none_optional_fields() -> None:
    for sender in (public_command_hub._private, welcome_setup_ui._send_private):
        payload = _run(sender, content="ok")
        assert payload["content"] == "ok"
        assert "embed" not in payload
        assert "view" not in payload
'''

files["ACTIVE_TASK.md"] = '''# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-005 — Complete live Profile Signature studio smoke correction

**Status:** IMPLEMENTED / EXACT-HEAD VALIDATION REQUIRED
**Branch:** `fix/profile-followup-payload`
**PR:** `#134`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until the Profile Signature studio opens, previews, saves, and navigates without Discord component or follow-up payload errors.

## Scope

- Correct the deployed `/dank profile` Preview failure.
- Inspect the shared response path used by appearance, privacy, platform, reset, and server-default actions.
- Remove the same unsafe optional payload behavior from the sibling Welcome & Join and `/dank` hub helpers introduced in the same UI overhaul.
- Add focused regression coverage and run the complete repository validation gate.

## Root cause

`_preview()` defers the interaction before rendering. The shared private-send helper then called `interaction.followup.send()` with `view=None` and `embed=None` keys still present. Discord.py rejects `None` for follow-up `view`, even though the initial interaction response path tolerated it.

## Changes

- Optional `content`, `embed`, `view`, and `file` fields are included only when present.
- The same safe payload construction is applied to Profile Signatures, Welcome & Join, and the compact `/dank` hub.
- Regression tests exercise the actual deferred/follow-up helper path and verify absent optional keys.

## Validation

- [ ] Focused regression tests pass.
- [ ] Changed Python modules compile.
- [ ] Full unit suite passes.
- [ ] Standalone checks and every repository audit pass.
- [ ] Branch is conflict-free with current `main`.
- [ ] Live Discord smoke confirms profile Preview and at least one appearance save.

## Cleanup

- Temporary patch transport files are removed before final validation.
- No compatibility shim, monkey patch, duplicate helper, or temporary runtime path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording after the active profile task reaches Definition of Done.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
'''

bundle = base64.b64encode(gzip.compress(json.dumps(files).encode("utf-8"), compresslevel=9)).decode("ascii")
print(f"PROFILE_PATCH_BUNDLE={bundle}")
raise SystemExit("intentional transport stop after emitting exact patch bundle")
