"""Focused regressions for Member Intelligence Discord status and join-source display.

Extract the small resolver from source so tests do not initialize the bot, Discord
gateway, or production database.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


SRC = (Path(__file__).resolve().parents[1] / "stoney_verify" / "commands_ext"
       / "member_command_center.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id


class FakeNotFound(Exception):
    pass


class FakeForbidden(Exception):
    pass


class FakeHTTPException(Exception):
    pass


class FakeGuild:
    def __init__(self, member=None, error=None):
        self.member = member
        self.error = error
        self.fetch_calls = []

    async def fetch_member(self, member_id):
        self.fetch_calls.append(member_id)
        if self.error:
            raise self.error
        return self.member


def _resolver():
    node = next(
        item for item in TREE.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == "_resolve_intelligence_member"
    )
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, node], type_ignores=[]))
    env = {
        "Optional": Optional,
        "discord": SimpleNamespace(
            Guild=FakeGuild, Member=FakeMember,
            NotFound=FakeNotFound, Forbidden=FakeForbidden,
            HTTPException=FakeHTTPException,
        ),
    }
    exec(compile(module, "<isolated-intelligence-resolver>", "exec"), env)
    return env["_resolve_intelligence_member"]


def test_cache_miss_does_not_claim_member_left():
    guild = FakeGuild(FakeMember(42))
    member, status = asyncio.run(_resolver()(guild, 42))
    assert member.id == 42
    assert status == "verified"
    assert guild.fetch_calls == [42]


def test_only_discord_not_found_means_absent():
    member, status = asyncio.run(_resolver()(FakeGuild(error=FakeNotFound()), 42))
    assert member is None and status == "absent"


def test_api_failure_is_not_false_departure():
    for error in (FakeForbidden(), FakeHTTPException(), TimeoutError("network")):
        member, status = asyncio.run(_resolver()(FakeGuild(error=error), 42))
        assert member is None and status == "unavailable"


def test_invalid_and_unexpected_results_are_not_departures():
    assert asyncio.run(_resolver()(None, 42)) == (None, "unavailable")
    assert asyncio.run(_resolver()(FakeGuild(None), 42)) == (None, "unavailable")


def _class_method(class_name: str, method_name: str) -> ast.AsyncFunctionDef:
    cls = next(x for x in TREE.body if isinstance(x, ast.ClassDef) and x.name == class_name)
    return next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == method_name)


def test_selector_and_buttons_share_authoritative_resolver():
    selector = ast.unparse(_class_method("IntelligenceMemberSelect", "callback"))
    assert "_resolve_intelligence_member(" in selector
    assert "_show_stored_member_history(" in selector
    assert "get_member(" not in selector
    target = next(x for x in TREE.body if isinstance(x, ast.ClassDef) and x.name == "IntelligenceTargetView")
    body = ast.unparse(target)
    assert "_resolve_intelligence_member(" in body
    assert "That member left the server" not in body
    assert "await _show_stored_member_history(" in body


def test_historical_embed_shows_source_and_confidence_not_guesses():
    function = next(x for x in TREE.body if isinstance(x, ast.FunctionDef)
                    and x.name == "_historical_member_embed")
    body = ast.unparse(function)
    assert 'snapshot.get("recent_joins")' in body
    assert 'snapshot.get("relationships")' in body
    assert "entry_truth_quality" in body
    assert "entry_conflict" in body
    assert "No reliable invite or inviter attribution" in body
    assert "_intelligence_presence_text(presence)" in body
