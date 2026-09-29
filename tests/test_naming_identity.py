from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import guild_config
from stoney_verify.services import naming_identity
from stoney_verify.services import server_design_studio as design


class _FakeResource:
    def __init__(self, resource_id: int, name: str, *, position: int = 1) -> None:
        self.id = int(resource_id)
        self.name = str(name)
        self.position = int(position)

    def is_default(self) -> bool:
        return False


class _FakeGuild:
    def __init__(
        self,
        *,
        roles: list[_FakeResource] | None = None,
        channels: list[_FakeResource] | None = None,
        categories: list[_FakeResource] | None = None,
    ) -> None:
        self.id = 999
        self.roles = list(roles or [])
        self.channels = list(channels or [])
        self.text_channels = list(channels or [])
        self.categories = list(categories or [])


class _FakeBot:
    def __init__(self) -> None:
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, callback: object, event_name: str) -> None:
        self.listeners.append((callback, event_name))


def _styled(value: str, style: str = "double_struck") -> str:
    rendered, _substitutions = design.transform_text_safe(value, style)
    assert rendered != value
    return rendered


def test_semantic_key_decodes_styled_role_letters_and_keeps_meaning() -> None:
    styled = f"🌿・{_styled('Stoner')}"
    assert naming_identity.semantic_key(styled) == "stoner"


def test_search_safe_display_name_preserves_decoration_but_normalizes_letters() -> None:
    styled = f"🌿・{_styled('Stoner')}"
    assert naming_identity.search_safe_display_name(styled) == "🌿・Stoner"
    assert naming_identity.has_stylized_search_text(styled) is True


def test_search_safe_display_name_preserves_unrelated_compatibility_symbols() -> None:
    raw = "™ ℠ ℃ ① ﬁ"
    assert naming_identity.search_safe_display_name(raw) == raw


def test_search_safe_display_name_decodes_fullwidth_letters_but_not_decorative_digits() -> None:
    fullwidth = _styled("Verified", style="fullwidth")
    circled_digit = "①"

    assert naming_identity.search_safe_display_name(fullwidth) == "Verified"
    assert naming_identity.search_safe_display_name(circled_digit) == circled_digit


def test_exact_font_glyph_decoder_does_not_apply_broad_nfkc() -> None:
    assert design.decode_known_unicode_font_glyph("™") == "™"
    assert design.decode_known_unicode_font_glyph("℃") == "℃"
    assert design.decode_known_unicode_font_glyph("ﬁ") == "ﬁ"


def test_style_only_rename_needs_no_persisted_alias() -> None:
    styled = f"✅・{_styled('Verified')}"
    assert naming_identity.previous_alias_for_rename("✅・Verified", styled) == ""


def test_semantic_rename_keeps_previous_search_name() -> None:
    assert naming_identity.previous_alias_for_rename("✅・Verified", "✅・Members") == "verified"


def test_search_safe_policy_survives_bounded_alias_updates() -> None:
    state = {
        "version": naming_identity.NAMING_IDENTITY_VERSION,
        "policy": {"mode": naming_identity.NAMING_MODE_SEARCH_SAFE},
        "records": {},
    }
    state = naming_identity.remember_alias(
        state,
        kind="role",
        resource_id=1,
        alias="verified",
        updated_at=1.0,
    )
    assert naming_identity.naming_policy(state)["mode"] == naming_identity.NAMING_MODE_SEARCH_SAFE

    state = naming_identity.forget_resource(state, kind="role", resource_id=1)
    assert naming_identity.naming_policy(state)["mode"] == naming_identity.NAMING_MODE_SEARCH_SAFE


def test_policy_boolean_strings_are_parsed_instead_of_python_truthiness() -> None:
    policy = naming_identity.naming_policy(
        {
            "policy": {
                "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
                "roles": "false",
                "channels": "0",
                "categories": "true",
            }
        }
    )

    assert policy["mode"] == naming_identity.NAMING_MODE_SEARCH_SAFE
    assert policy["roles"] is False
    assert policy["channels"] is False
    assert policy["categories"] is True


def test_unknown_policy_boolean_values_fall_back_to_safe_defaults() -> None:
    policy = naming_identity.naming_policy(
        {
            "policy": {
                "roles": "definitely",
                "channels": 9,
                "categories": "maybe",
            }
        }
    )

    assert policy["roles"] is True
    assert policy["channels"] is True
    assert policy["categories"] is False


def test_unknown_policy_mode_fails_closed_to_preserve() -> None:
    policy = naming_identity.naming_policy({"policy": {"mode": "surprise-mode"}})
    assert policy["mode"] == naming_identity.NAMING_MODE_PRESERVE
    assert policy["roles"] is True
    assert policy["channels"] is True
    assert policy["categories"] is False


def test_alias_history_and_resource_records_are_strictly_bounded() -> None:
    state: dict[str, object] = {}
    for index, alias in enumerate(("one", "two", "three", "four"), start=1):
        state = naming_identity.remember_alias(
            state,
            kind="role",
            resource_id=1,
            alias=alias,
            updated_at=float(index),
        )

    aliases = naming_identity.aliases_for(state, kind="role", resource_id=1)
    assert aliases == ("four", "three", "two")
    assert len(aliases) == naming_identity.MAX_ALIASES_PER_RESOURCE

    for resource_id in range(2, naming_identity.MAX_TRACKED_RESOURCES + 4):
        state = naming_identity.remember_alias(
            state,
            kind="channel",
            resource_id=resource_id,
            alias=f"channel-{resource_id}",
            updated_at=float(resource_id + 10),
        )

    records = dict(state["records"])  # type: ignore[index]
    assert len(records) == naming_identity.MAX_TRACKED_RESOURCES
    assert "role:1" not in records


def test_process_state_cache_is_bounded_across_many_guilds(monkeypatch) -> None:
    naming_identity._STATE_CACHE.clear()  # noqa: SLF001
    monkeypatch.setattr(naming_identity, "MAX_CACHED_GUILDS", 3)

    for guild_id in range(1, 8):
        naming_identity._cache_state(  # noqa: SLF001
            guild_id,
            naming_identity.remember_alias(
                {},
                kind="role",
                resource_id=guild_id,
                alias=f"role-{guild_id}",
                updated_at=float(guild_id),
            ),
        )

    assert len(naming_identity._STATE_CACHE) == 3  # noqa: SLF001
    naming_identity._STATE_CACHE.clear()  # noqa: SLF001


def test_live_semantic_search_finds_styled_role_without_db_alias_lookup() -> None:
    role = _FakeResource(123, f"✅・{_styled('Verified')}", position=50)
    matches = naming_identity._search_resources(  # noqa: SLF001
        [role],
        kind="role",
        current="Verified",
        state=None,
    )
    assert matches == [role]


def test_saved_previous_alias_finds_resource_after_real_semantic_rename() -> None:
    role = _FakeResource(123, f"🌿・{_styled('Lounge')}", position=50)
    state = naming_identity.remember_alias(
        {},
        kind="role",
        resource_id=role.id,
        alias="Stoner",
        updated_at=1.0,
    )
    matches = naming_identity._search_resources(  # noqa: SLF001
        [role],
        kind="role",
        current="Stoner",
        state=state,
    )
    assert matches == [role]


def test_role_autocomplete_returns_styled_role_for_plain_query_without_history_read(monkeypatch) -> None:
    role = _FakeResource(123, f"✅・{_styled('Verified')}", position=50)
    guild = _FakeGuild(roles=[role])
    interaction = SimpleNamespace(guild=guild)

    async def fail_load(_guild_id: int):
        raise AssertionError("live semantic matches must not hit durable alias storage")

    monkeypatch.setattr(naming_identity, "_load_state", fail_load)
    choices = asyncio.run(naming_identity.role_autocomplete(interaction, "Verified"))

    assert len(choices) == 1
    assert choices[0].value == "123"
    assert "Verified" in choices[0].name


def test_runtime_is_event_driven_and_never_registers_an_on_ready_scan() -> None:
    bot = _FakeBot()
    assert naming_identity.install_naming_identity_runtime(bot) is True
    assert naming_identity.install_naming_identity_runtime(bot) is False

    events = [event for _callback, event in bot.listeners]
    assert events == [
        "on_guild_role_create",
        "on_guild_role_update",
        "on_guild_channel_create",
        "on_guild_channel_update",
        "on_guild_role_delete",
        "on_guild_channel_delete",
    ]
    assert "on_ready" not in events


def test_style_only_gateway_rename_does_not_queue_persistence(monkeypatch) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(naming_identity, "_queue_flush", lambda guild_id: scheduled.append(guild_id))
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001

    styled = f"✅・{_styled('Verified')}"
    queued = naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=123,
        before_name="✅・Verified",
        after_name=styled,
    )

    assert queued is False
    assert scheduled == []
    assert naming_identity._PENDING_ALIASES == {}  # noqa: SLF001


def test_native_runtime_discovery_recognizes_styled_role_and_channel_names() -> None:
    verified = _FakeResource(1, f"✅・{_styled('Verified')}")
    verify_channel = _FakeResource(2, f"✅・{_styled('verify')}")
    guild = _FakeGuild(roles=[verified], channels=[verify_channel])

    assert guild_config._find_role_by_names(guild, ["verified"]) is verified  # noqa: SLF001
    assert guild_config._find_text_channel_by_names(guild, ["verify"]) is verify_channel  # noqa: SLF001


def test_rapid_alias_flush_preserves_event_chronology(monkeypatch) -> None:
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001
    naming_identity._PENDING_DELETES.clear()  # noqa: SLF001
    naming_identity._FLUSH_TASKS.clear()  # noqa: SLF001
    monkeypatch.setattr(naming_identity, "PERSIST_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr(naming_identity, "_queue_flush", lambda _guild_id: None)

    assert naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=42,
        before_name="Alpha",
        after_name="Beta",
    )
    assert naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=42,
        before_name="Beta",
        after_name="Charlie",
    )
    assert naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=42,
        before_name="Charlie",
        after_name="Delta",
    )

    captured: dict[str, object] = {}

    async def fake_mutate(_guild_id: int, transform, *, attempts: int = 5):
        _ = attempts
        state = dict(transform({}))
        captured["state"] = state
        return state

    monkeypatch.setattr(naming_identity, "_mutate_state_cas", fake_mutate)

    asyncio.run(naming_identity._debounced_flush(999))  # noqa: SLF001

    state = captured["state"]
    assert isinstance(state, dict)
    assert naming_identity.aliases_for(state, kind="role", resource_id=42) == (
        "charlie",
        "beta",
        "alpha",
    )


def test_failed_alias_persistence_requeues_old_events_ahead_of_newer_events(monkeypatch) -> None:
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001
    naming_identity._PENDING_DELETES.clear()  # noqa: SLF001
    naming_identity._FLUSH_TASKS.clear()  # noqa: SLF001
    naming_identity._PENDING_ALIASES[999] = {"role:42": ["alpha"]}  # noqa: SLF001
    naming_identity._PENDING_DELETES[999] = {"channel:88"}  # noqa: SLF001
    monkeypatch.setattr(naming_identity, "PERSIST_DEBOUNCE_SECONDS", 0.0)

    scheduled: list[int] = []
    monkeypatch.setattr(
        naming_identity,
        "_queue_flush",
        lambda guild_id: scheduled.append(int(guild_id)),
    )

    async def fail_after_new_event(_guild_id: int, _transform, *, attempts: int = 5):
        _ = attempts
        naming_identity._PENDING_ALIASES.setdefault(999, {})["role:42"] = ["beta"]  # noqa: SLF001
        raise RuntimeError("temporary database outage")

    monkeypatch.setattr(naming_identity, "_mutate_state_cas", fail_after_new_event)

    asyncio.run(naming_identity._debounced_flush(999))  # noqa: SLF001

    assert naming_identity._PENDING_ALIASES[999]["role:42"] == ["alpha", "beta"]  # noqa: SLF001
    assert naming_identity._PENDING_DELETES[999] == {"channel:88"}  # noqa: SLF001
    assert scheduled == [999]


def test_exact_saved_alias_outranks_unrelated_partial_live_role_match(monkeypatch) -> None:
    partial = _FakeResource(1, "Verified Team", position=50)
    alias_target = _FakeResource(2, "Members", position=40)
    guild = _FakeGuild(roles=[partial, alias_target])
    interaction = SimpleNamespace(guild=guild)

    state = naming_identity.remember_alias(
        {},
        kind="role",
        resource_id=alias_target.id,
        alias="Verified",
        updated_at=1.0,
    )

    async def fake_load(_guild_id: int):
        return state

    monkeypatch.setattr(naming_identity, "_load_state", fake_load)
    choices = asyncio.run(naming_identity.role_autocomplete(interaction, "Verified"))

    assert [choice.value for choice in choices[:2]] == ["2", "1"]


def test_exact_live_role_match_still_avoids_alias_state_read(monkeypatch) -> None:
    exact = _FakeResource(1, "Verified", position=50)
    partial = _FakeResource(2, "Verified Team", position=40)
    guild = _FakeGuild(roles=[exact, partial])
    interaction = SimpleNamespace(guild=guild)

    async def fail_load(_guild_id: int):
        raise AssertionError("exact live match must stay on the no-I/O fast path")

    monkeypatch.setattr(naming_identity, "_load_state", fail_load)
    choices = asyncio.run(naming_identity.role_autocomplete(interaction, "Verified"))

    assert choices[0].value == "1"


def test_naming_state_cas_replays_transform_on_concurrent_worker_winner(monkeypatch) -> None:
    initial = naming_identity._normalize_state({})  # noqa: SLF001
    winner = naming_identity.remember_alias(
        initial,
        kind="role",
        resource_id=2,
        alias="winner",
        updated_at=1.0,
    )
    calls: list[dict[str, object]] = []

    async def fake_fresh(_guild_id: int):
        return None, initial

    async def fake_cas(_guild_id: int, *, expected_raw, updated):
        calls.append(
            {
                "expected": expected_raw,
                "updated": naming_identity._normalize_state(updated),  # noqa: SLF001
            }
        )
        if len(calls) == 1:
            return False, winner, winner
        return True, updated, naming_identity._normalize_state(updated)  # noqa: SLF001

    monkeypatch.setattr(naming_identity, "_fresh_state_source", fake_fresh)
    monkeypatch.setattr(naming_identity, "_cas_state", fake_cas)

    def transform(current):
        return naming_identity.remember_alias(
            current,
            kind="role",
            resource_id=1,
            alias="mine",
            updated_at=2.0,
        )

    saved = asyncio.run(naming_identity._mutate_state_cas(999, transform))  # noqa: SLF001

    assert len(calls) == 2
    assert naming_identity.aliases_for(saved, kind="role", resource_id=1) == ("mine",)
    assert naming_identity.aliases_for(saved, kind="role", resource_id=2) == ("winner",)


def test_shutdown_flush_persists_pending_aliases_without_debounce(monkeypatch) -> None:
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001
    naming_identity._PENDING_DELETES.clear()  # noqa: SLF001
    naming_identity._FLUSH_TASKS.clear()  # noqa: SLF001
    naming_identity._PENDING_ALIASES[999] = {  # noqa: SLF001
        "role:42": ["alpha", "beta"],
    }

    saved: list[dict[str, object]] = []

    async def fake_mutate(_guild_id: int, transform, **_kwargs):
        current = naming_identity._normalize_state({})  # noqa: SLF001
        updated = transform(current)
        saved.append(updated)
        return updated

    monkeypatch.setattr(naming_identity, "_mutate_state_cas", fake_mutate)

    result = asyncio.run(
        naming_identity.flush_pending_naming_identity(timeout_seconds=1.0)
    )

    assert result == {"guilds": 1, "flushed": 1, "pending": 0}
    assert len(saved) == 1
    assert naming_identity.aliases_for(saved[0], kind="role", resource_id=42) == (
        "beta",
        "alpha",
    )
    assert naming_identity._PENDING_ALIASES == {}  # noqa: SLF001


def test_cancelled_claimed_flush_requeues_events(monkeypatch) -> None:
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001
    naming_identity._PENDING_DELETES.clear()  # noqa: SLF001
    naming_identity._FLUSH_TASKS.clear()  # noqa: SLF001
    naming_identity._PENDING_ALIASES[999] = {"role:42": ["alpha"]}  # noqa: SLF001

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_mutate(_guild_id: int, _transform, **_kwargs):
        entered.set()
        await release.wait()
        return {}

    monkeypatch.setattr(naming_identity, "_mutate_state_cas", blocked_mutate)

    async def scenario() -> None:
        task = asyncio.create_task(naming_identity._flush_pending_once(999))  # noqa: SLF001
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    assert naming_identity._PENDING_ALIASES[999]["role:42"] == ["alpha"]  # noqa: SLF001


def test_future_naming_schema_reads_fail_closed_without_reinterpreting_records() -> None:
    future = {
        "version": naming_identity.NAMING_IDENTITY_VERSION + 1,
        "policy": {
            "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
            "roles": True,
            "channels": True,
            "categories": True,
        },
        "records": {
            "role:42": {
                "aliases": ["future-alias"],
                "updated_at": 1.0,
            }
        },
    }

    policy = naming_identity.naming_policy(future)
    assert policy["mode"] == naming_identity.NAMING_MODE_PRESERVE
    assert policy["categories"] is False
    assert naming_identity.aliases_for(future, kind="role", resource_id=42) == ()


def test_future_naming_schema_rejects_mutation_instead_of_downgrading() -> None:
    future = {
        "version": naming_identity.NAMING_IDENTITY_VERSION + 1,
        "policy": {"mode": naming_identity.NAMING_MODE_SEARCH_SAFE},
        "records": {},
    }

    try:
        naming_identity.remember_alias(
            future,
            kind="role",
            resource_id=42,
            alias="Verified",
        )
    except naming_identity.UnsupportedNamingIdentityVersion:
        pass
    else:
        raise AssertionError("future schema mutation must be rejected")


def test_set_naming_mode_does_not_overwrite_future_schema(monkeypatch) -> None:
    future = {
        "version": naming_identity.NAMING_IDENTITY_VERSION + 1,
        "policy": {"mode": naming_identity.NAMING_MODE_SEARCH_SAFE},
        "records": {},
    }
    persisted: list[object] = []

    async def fake_fresh(_guild_id: int):
        return future, naming_identity._normalize_state(future)  # noqa: SLF001

    async def fake_cas(*args, **kwargs):
        persisted.append((args, kwargs))
        raise AssertionError("CAS must not run for a future schema")

    monkeypatch.setattr(naming_identity, "_fresh_state_source", fake_fresh)
    monkeypatch.setattr(naming_identity, "_cas_state", fake_cas)

    async def scenario() -> None:
        try:
            await naming_identity.set_naming_mode(
                999,
                naming_identity.NAMING_MODE_PRESERVE,
            )
        except naming_identity.UnsupportedNamingIdentityVersion:
            return
        raise AssertionError("set_naming_mode must reject a newer schema")

    asyncio.run(scenario())
    assert persisted == []
