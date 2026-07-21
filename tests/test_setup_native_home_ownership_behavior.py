from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def test_solid_home_delegates_to_canonical_product_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = object()
    expected = (object(), object())
    calls: list[Any] = []

    async def product_home(guild_arg: Any) -> tuple[Any, Any]:
        calls.append(guild_arg)
        return expected

    monkeypatch.setattr(
        recommend,
        "_product_main_setup_payload",
        product_home,
    )

    result = run(solid._build_main_setup_payload(guild))

    assert result is expected
    assert calls == [guild]


def test_recommend_registration_does_not_replace_home_builder() -> None:
    before = solid._build_main_setup_payload

    recommend.register_public_setup_recommend_commands(None, None)

    assert solid._build_main_setup_payload is before
