from __future__ import annotations

import asyncio
from collections import Counter
from types import SimpleNamespace

import discord

from stoney_verify import setup_activity_access, setup_permission_repair_services
from stoney_verify.commands_ext import public_diagnostics_group as diagnostics
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_assistant as setup_assistant
from stoney_verify.commands_ext import public_setup_solid as solid
from stoney_verify.members_new.activity_scope import ActivityScopeProblem, ActivityScopeReport
from stoney_verify.services import setup_permission_policy


def _rows(view: discord.ui.View) -> Counter[int]:
    return Counter(int(getattr(child, "row", 0) or 0) for child in view.children)


def _button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [child for child in view.children if str(getattr(child, "label", "") or "") == label]
    assert len(matches) == 1
    assert isinstance(matches[0], discord.ui.Button)
    return matches[0]


def test_security_section_separates_access_check_from_permission_repair() -> None:
    view = recommend.AdvancedSecurityView()

    access = _button(view, "Check Bot Access")
    repair = _button(view, "Fix Channel Permissions")

    assert access.custom_id == "dank_setup_security:access"
    assert access.row == 0
    assert repair.custom_id == "dank_setup_security:repair"
    assert repair.row == 1


def test_setup_route_calls_owned_activity_access_service(monkeypatch) -> None:
    calls: list[object] = []

    async def allow_setup(_interaction) -> bool:
        return True

    async def open_check(interaction, *, parent="logs") -> None:
        calls.append((interaction, parent))

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow_setup)
    monkeypatch.setattr(setup_activity_access, "open_activity_access_check", open_check)

    interaction = object()
    asyncio.run(recommend._open_bot_access_check(interaction))

    assert calls == [(interaction, "logs")]


def test_access_check_reports_exact_missing_permissions_and_coverage() -> None:
    report = ActivityScopeReport(
        total_channels=4,
        accessible_channels=1,
        problems=(
            ActivityScopeProblem(
                channel_id=11,
                channel_name="moderator-only",
                channel_kind="text",
                missing_permissions=("View Channel", "Read Message History"),
            ),
            ActivityScopeProblem(
                channel_id=12,
                channel_name="song-recommendations",
                channel_kind="text",
                missing_permissions=("Read Message History",),
            ),
            ActivityScopeProblem(
                channel_id=13,
                channel_name="private-thread-parent",
                channel_kind="text",
                missing_permissions=("Manage Threads",),
            ),
        ),
        bot_member_resolved=True,
    )

    embed = setup_activity_access.build_activity_access_embed(report)
    rendered = "\n".join(
        [embed.description or ""]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "25% activity scope" in rendered
    assert "#moderator-only" in rendered
    assert "View Channel" in rendered
    assert "Read Message History" in rendered
    assert "#song-recommendations" in rendered
    assert "#private-thread-parent" in rendered
    assert "Manage Threads" in rendered
    assert "purge-safe" in rendered


def test_open_access_check_is_read_only_and_renders_audit_result(monkeypatch) -> None:
    class GuardedChannel:
        mutation_attempted = False

        async def set_permissions(self, *_args, **_kwargs) -> None:
            self.mutation_attempted = True
            raise AssertionError("read-only access check attempted to change permissions")

        async def edit(self, *_args, **_kwargs) -> None:
            self.mutation_attempted = True
            raise AssertionError("read-only access check attempted to edit a channel")

    class FakeGuild:
        def __init__(self) -> None:
            self.channels = [GuardedChannel()]

    class FakeInteraction:
        def __init__(self) -> None:
            self.guild = FakeGuild()

    report = ActivityScopeReport(
        total_channels=1,
        accessible_channels=0,
        problems=(
            ActivityScopeProblem(
                channel_id=99,
                channel_name="private-room",
                channel_kind="text",
                missing_permissions=("View Channel",),
            ),
        ),
        bot_member_resolved=True,
    )
    captured: dict[str, object] = {}

    async def allow_setup(_interaction) -> bool:
        return True

    def audit(guild):
        captured["guild"] = guild
        return report

    async def defer(interaction) -> None:
        captured["deferred"] = interaction

    async def edit_or_followup(interaction, *, embed, view) -> None:
        captured["interaction"] = interaction
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(solid, "_require_setup_permission", allow_setup)
    monkeypatch.setattr(solid, "_safe_defer_update", defer)
    monkeypatch.setattr(solid, "_edit_or_followup", edit_or_followup)
    monkeypatch.setattr(setup_activity_access, "audit_activity_scope", audit)

    interaction = FakeInteraction()
    asyncio.run(setup_activity_access.open_activity_access_check(interaction))

    assert captured["guild"] is interaction.guild
    assert captured["deferred"] is interaction
    assert captured["interaction"] is interaction
    assert isinstance(captured["embed"], discord.Embed)
    assert isinstance(captured["view"], setup_activity_access.ActivityAccessView)
    assert interaction.guild.channels[0].mutation_attempted is False


def test_diagnostics_repair_bot_access_includes_full_activity_scope(monkeypatch) -> None:
    calls: list[object] = []

    async def open_repair(
        interaction,
        *,
        parent="security",
        include_activity_coverage=False,
    ) -> None:
        calls.append((interaction, parent, include_activity_coverage))

    monkeypatch.setattr(
        setup_permission_repair_services,
        "open_permission_repair",
        open_repair,
    )
    monkeypatch.setattr(diagnostics, "_admin_or_manage_guild", lambda _interaction: True)

    interaction = type(
        "I",
        (),
        {
            "user": type("U", (), {"id": 123})(),
            "response": type("R", (), {"send_message": None})(),
        },
    )()

    view = diagnostics.DiagnosticsActionView(actor_id=123)
    fix = _button(view, "Repair Bot Access")
    asyncio.run(fix.callback(interaction))

    assert calls == [(interaction, "logs", True)]


def test_activity_repair_targets_authoritative_scope_only_and_preserves_bot_overwrite(
    monkeypatch,
) -> None:
    class FakeChannel:
        id = 100
        name = "private-thread-parent"

        def overwrites_for(self, _target):
            return discord.PermissionOverwrite(
                send_messages=False,
                manage_messages=True,
            )

        async def set_permissions(self, *_args, **_kwargs) -> None:
            return None

    class FakeThread:
        id = 200
        name = "private-thread"
        parent = None

    parent = FakeChannel()
    thread = FakeThread()
    thread.parent = parent

    class FakeGuild:
        channels = [parent]
        threads = [thread]

        def get_channel_or_thread(self, channel_id):
            return {100: parent, 200: thread}.get(channel_id)

    me = object()
    report = ActivityScopeReport(
        total_channels=2,
        accessible_channels=0,
        problems=(
            ActivityScopeProblem(
                channel_id=200,
                channel_name="private-thread",
                channel_kind="thread",
                missing_permissions=("View Channel", "Read Message History"),
            ),
            ActivityScopeProblem(
                channel_id=100,
                channel_name="private-thread-parent",
                channel_kind="text",
                missing_permissions=("Manage Threads",),
            ),
        ),
        bot_member_resolved=True,
    )

    monkeypatch.setattr(
        setup_permission_repair_services.repair_core,
        "_bot_member",
        lambda _guild: me,
    )
    monkeypatch.setattr(
        setup_permission_repair_services,
        "audit_activity_scope",
        lambda _guild, **_kwargs: report,
    )

    targets, notes, mappings, manual = asyncio.run(
        setup_permission_repair_services._build_expanded_targets(
            FakeGuild(),
            include_activity_coverage=True,
        )
    )

    assert mappings == []
    assert manual == []
    assert len(targets) == 1
    target = targets[0]
    assert target.channel is parent
    assert set(target.overwrites) == {me}

    expected = target.overwrites[me]
    assert expected.view_channel is True
    assert expected.read_message_history is True
    assert expected.manage_threads is True
    assert expected.send_messages is False
    assert expected.manage_messages is True
    assert any("2 Diagnostics gap(s)" in note for note in notes)


def test_setup_assistant_refresh_preserves_existing_bot_manage_permissions() -> None:
    class Bot:
        id = 42

        def __hash__(self) -> int:
            return hash(self.id)

    me = Bot()
    guild = SimpleNamespace(me=me)

    class Channel:
        def __init__(self, value):
            self.value = value

        def overwrites_for(self, target):
            assert target is me
            return discord.PermissionOverwrite(manage_roles=self.value)

    baseline = {
        me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
        )
    }

    preserved_allow = setup_assistant._preserve_existing_bot_manage_permissions(
        guild,
        Channel(True),
        baseline,
    )
    assert preserved_allow is not None
    assert preserved_allow[me].manage_roles is True
    assert baseline[me].manage_roles is None

    preserved_deny = setup_assistant._preserve_existing_bot_manage_permissions(
        guild,
        Channel(False),
        baseline,
    )
    assert preserved_deny is not None
    assert preserved_deny[me].manage_roles is False
    assert baseline[me].manage_roles is None


def test_activity_emergency_recovery_replaces_only_bot_explicit_access_denies(
    monkeypatch,
) -> None:
    class Bot:
        def __init__(self) -> None:
            self.id = 42
            self.guild_permissions = SimpleNamespace(
                administrator=True,
                manage_roles=True,
            )

        def __hash__(self) -> int:
            return hash(self.id)

    me = Bot()

    class Channel:
        id = 100
        name = "locked-room"

        def overwrites_for(self, target):
            assert target is me
            return discord.PermissionOverwrite(
                view_channel=False,
                read_message_history=False,
                manage_threads=False,
                manage_roles=False,
            )

        async def set_permissions(self, *_args, **_kwargs):
            return None

    channel = Channel()

    class Guild:
        channels = [channel]
        threads = []

        def get_channel_or_thread(self, channel_id):
            return channel if channel_id == 100 else None

    report = ActivityScopeReport(
        total_channels=1,
        accessible_channels=0,
        problems=(
            ActivityScopeProblem(
                channel_id=100,
                channel_name="locked-room",
                channel_kind="text",
                missing_permissions=(
                    "View Channel",
                    "Read Message History",
                    "Manage Threads",
                ),
            ),
        ),
        bot_member_resolved=True,
    )

    monkeypatch.setattr(
        setup_permission_repair_services.repair_core,
        "_bot_member",
        lambda _guild: me,
    )
    monkeypatch.setattr(
        setup_permission_repair_services,
        "audit_activity_scope",
        lambda _guild, **_kwargs: report,
    )
    monkeypatch.setattr(
        setup_permission_policy,
        "permissions_without_administrator",
        lambda _channel, _member: SimpleNamespace(manage_roles=False),
    )

    targets: list[object] = []
    notes: list[str] = []
    manual: list[str] = []
    setup_permission_repair_services._merge_activity_coverage_targets(
        Guild(),
        targets=targets,
        seen=set(),
        notes=notes,
        manual_actions=manual,
    )

    assert manual == []
    assert len(targets) == 1
    expected = targets[0].overwrites[me]
    assert expected.view_channel is True
    assert expected.read_message_history is True
    assert expected.manage_threads is True
    assert expected.manage_roles is True


def test_underlying_permission_resolver_ignores_only_admin_shortcut() -> None:
    class Role:
        def __init__(self, role_id: int, **permissions: bool) -> None:
            self.id = role_id
            self.permissions = discord.Permissions.none()
            for name, enabled in permissions.items():
                setattr(self.permissions, name, enabled)

    everyone = Role(1, view_channel=True, read_message_history=True)
    bot_role = Role(
        2,
        administrator=True,
        manage_roles=True,
        view_channel=True,
        read_message_history=True,
        manage_threads=True,
    )
    member = SimpleNamespace(id=42, roles=[everyone, bot_role])
    guild = SimpleNamespace(
        id=1,
        owner_id=999,
        default_role=everyone,
    )

    class Channel:
        def __init__(self) -> None:
            self.guild = guild

        def permissions_for(self, _member):
            return discord.Permissions.all()

        def overwrites_for(self, target):
            if target is everyone:
                return discord.PermissionOverwrite()
            if target is bot_role:
                return discord.PermissionOverwrite(manage_roles=False)
            if target is member:
                return discord.PermissionOverwrite(view_channel=False)
            return discord.PermissionOverwrite()

    underlying = setup_permission_policy.permissions_without_administrator(
        Channel(),
        member,
    )

    assert underlying is not None
    assert underlying.administrator is False
    assert underlying.manage_roles is False
    assert underlying.view_channel is False
    assert underlying.read_message_history is False


def test_activity_repair_asks_for_underlying_scope_while_temporary_admin_is_active(
    monkeypatch,
) -> None:
    member = SimpleNamespace(
        id=42,
        guild_permissions=SimpleNamespace(
            administrator=True,
            manage_roles=True,
        ),
    )
    report = ActivityScopeReport(
        total_channels=0,
        accessible_channels=0,
        problems=(),
        bot_member_resolved=True,
    )
    seen: list[bool] = []

    monkeypatch.setattr(
        setup_permission_repair_services.repair_core,
        "_bot_member",
        lambda _guild: member,
    )

    def audit(_guild, *, ignore_administrator=False):
        seen.append(bool(ignore_administrator))
        return report

    monkeypatch.setattr(
        setup_permission_repair_services,
        "audit_activity_scope",
        audit,
    )

    targets: list[object] = []
    notes: list[str] = []
    manual_actions: list[str] = []
    setup_permission_repair_services._merge_activity_coverage_targets(
        object(),
        targets=targets,
        seen=set(),
        notes=notes,
        manual_actions=manual_actions,
    )

    assert seen == [True]
    assert manual_actions == []


def test_repair_button_routes_to_activity_scoped_preview_first_permission_tool(monkeypatch) -> None:
    calls: list[object] = []

    async def open_repair(
        interaction,
        *,
        parent="security",
        include_activity_coverage=False,
    ) -> None:
        calls.append((interaction, parent, include_activity_coverage))

    monkeypatch.setattr(setup_permission_repair_services, "open_permission_repair", open_repair)

    interaction = object()
    view = setup_activity_access.ActivityAccessView(needs_repair=True)
    fix = _button(view, "Fix Channel Permissions")

    assert fix.disabled is False
    asyncio.run(fix.callback(interaction))
    assert calls == [(interaction, "logs", True)]

    complete_view = setup_activity_access.ActivityAccessView(needs_repair=False)
    assert _button(complete_view, "Fix Channel Permissions").disabled is True


def test_bot_access_view_keeps_mobile_rows_to_two_buttons_max() -> None:
    view = setup_activity_access.ActivityAccessView(needs_repair=True)
    counts = _rows(view)
    assert counts
    assert max(counts.values()) <= 2
    assert {str(getattr(child, "label", "") or "") for child in view.children} == {
        "Check Again",
        "Fix Channel Permissions",
        "Back",
        "Setup Home",
    }


def test_complete_access_report_is_clear_and_does_not_offer_fake_problem_count() -> None:
    report = ActivityScopeReport(
        total_channels=7,
        accessible_channels=7,
        problems=(),
        bot_member_resolved=True,
    )
    embed = setup_activity_access.build_activity_access_embed(report)
    rendered = "\n".join(str(field.value) for field in embed.fields)
    assert "100% activity scope" in rendered
    assert "No activity-tracking channel access gaps were detected" in rendered


def test_activity_access_back_preserves_security_or_logs_parent(monkeypatch) -> None:
    events: list[str] = []

    async def security(_interaction) -> None:
        events.append("security")

    async def logs(_interaction) -> None:
        events.append("logs")

    monkeypatch.setattr(recommend, "_open_advanced_security", security)
    monkeypatch.setattr(recommend, "_open_advanced_logs_activity", logs)

    security_view = setup_activity_access.ActivityAccessView(
        needs_repair=False,
        parent="security",
    )
    asyncio.run(_button(security_view, "Back").callback(object()))

    logs_view = setup_activity_access.ActivityAccessView(
        needs_repair=False,
        parent="logs",
    )
    asyncio.run(_button(logs_view, "Back").callback(object()))

    assert events == ["security", "logs"]



def test_activity_repair_filters_unrelated_global_capabilities(monkeypatch) -> None:
    async def no_targets(_guild, *, include_activity_coverage=False):
        assert include_activity_coverage is True
        return [], [], [], []

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_build_expanded_targets",
        no_targets,
    )
    monkeypatch.setattr(
        setup_permission_repair_services,
        "_bot_blockers",
        lambda _guild: [
            "Dank Shield is missing **Manage Channels** at the server level.",
            "Dank Shield is missing **View Audit Log**; audit-backed setup checks will be less reliable.",
        ],
    )

    healthy = asyncio.run(
        setup_permission_repair_services.preview_or_apply(
            object(),
            apply=False,
            include_activity_coverage=True,
        )
    )
    assert healthy["manual_actions"] == []
    assert healthy["notes"] == []
    assert healthy["reauthorize_recommended"] is False

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_bot_blockers",
        lambda _guild: [
            "Dank Shield is missing **Manage Roles** at the server level. Discord requires Manage Roles to repair channel overwrites.",
            "Dank Shield is missing **View Audit Log**; audit-backed setup checks will be less reliable.",
        ],
    )
    blocked = asyncio.run(
        setup_permission_repair_services.preview_or_apply(
            object(),
            apply=False,
            include_activity_coverage=True,
        )
    )
    assert len(blocked["manual_actions"]) == 1
    assert "Manage Roles" in blocked["manual_actions"][0]
    assert all("View Audit Log" not in item for item in blocked["notes"])
    assert blocked["reauthorize_recommended"] is True
