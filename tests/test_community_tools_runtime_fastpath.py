from __future__ import annotations

import asyncio
from types import SimpleNamespace

import stoney_verify.community_tools_runtime as runtime_module
from stoney_verify.community_tools_runtime import StickyRuntime
from stoney_verify.community_tools_service import StickyConfig


def test_unknown_non_sticky_channel_is_zero_database_fast_path(monkeypatch) -> None:
    async def explode(_: int):
        raise AssertionError("non-sticky message path must not query Supabase")

    monkeypatch.setattr(runtime_module, "get_sticky", explode)
    runtime = StickyRuntime(SimpleNamespace())

    assert asyncio.run(runtime._config_for(999999)) is None


def test_in_memory_index_immediately_serves_new_sticky_without_database_read(monkeypatch) -> None:
    async def explode(_: int):
        raise AssertionError("indexed sticky lookup must not query Supabase")

    monkeypatch.setattr(runtime_module, "get_sticky", explode)
    runtime = StickyRuntime(SimpleNamespace())
    config = StickyConfig(guild_id=123, channel_id=456, content="Keep me visible")
    runtime.set_config(config)

    assert asyncio.run(runtime._config_for(456)) == config
