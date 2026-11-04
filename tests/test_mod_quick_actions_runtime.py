from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from stoney_verify.commands_ext import member_role_browser_actions as actions
from stoney_verify.commands_ext.public_mod_command_center import (
    ModMemberSelect,
    ModerationCenterView,
    _center_embed,
)


def _role(name: str, *, default: bool = False) -> SimpleNamespace:
    return SimpleNamespace(mention=f"@{name}", is_default=lambda: default)


def _member(*, timed_out: bool = False, role_count: int = 3) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    roles = [_role("everyone", default=True)] + [_role(f"r{i}") for i in range(role_count)]
    return SimpleNamespace(
        id=42,
        mention="<@42>",
        bot=False,
        display_name="mellowjay",
        color=SimpleNamespace(value=0),
        display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        joined_at=now - timedelta(days=3),
        created_at=now - timedelta(days=14),
        timed_out_until=now + timedelta(hours=1) if timed_out else None,
        is_timed_out=lambda: timed_out,
        roles=roles,
    )


def _labels_by_row(view: discord.ui.View) -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            rows.setdefault(int(item.row or 0), []).append(str(item.label))
    return rows


def test_mod_opens_on_a_member_picker_and_keeps_every_old_path() -> None:
    view = ModerationCenterView(1)
    first_row = [item for item in view.children if (item.row or 0) == 0]

    assert len(first_row) == 1 and isinstance(first_row[0], ModMemberSelect)
    labels = {str(item.label) for item in view.children if isinstance(item, discord.ui.Button)}
    assert {
        "Member Command Center",
        "Cleanup Tools",
        "Ban / Unban by ID",
        "Gateway Intents",
        "Dank Shield Home",
    } <= labels


def test_mod_center_is_short_and_works_as_back_target() -> None:
    embed = _center_embed()
    assert not embed.fields
    assert len(embed.description) <= 280
    assert "choose" in embed.description.lower()

    view = ModerationCenterView(1)
    assert view.role is None
    view.rebuild()
    assert view.render_embed().title == embed.title


def test_member_card_puts_actions_first() -> None:
    view = actions.MemberActionView(owner_id=1, member=_member(), browser=ModerationCenterView(1))
    rows = _labels_by_row(view)

    assert rows[0] == ["Timeout", "Kick", "Ban"]
    assert {"Verify", "Review history", "Message"} == set(rows[1])
    assert "Back" in rows[3]


def test_member_card_summary_is_three_short_facts() -> None:
    embed = actions.member_detail_embed(_member(), None)
    inline = [field.name for field in embed.fields if field.inline]

    assert inline == ["Joined", "Account made", "Status"]
    assert embed.fields[2].value == "Active"
    assert "mellowjay" in embed.title

    timed_out = actions.member_detail_embed(_member(timed_out=True), None)
    assert "Timed out until" in timed_out.fields[2].value


def test_member_card_caps_long_role_lists() -> None:
    embed = actions.member_detail_embed(_member(role_count=30), None)
    roles_field = embed.fields[-1]

    assert roles_field.name == "Roles (30)"
    assert f"+{30 - actions._MEMBER_CARD_ROLE_LIMIT} more" in roles_field.value


def test_timeout_length_resolution() -> None:
    assert actions.resolve_timeout_minutes("60", "") == (60, "")
    assert actions.resolve_timeout_minutes("10080", "") == (10080, "")
    assert actions.resolve_timeout_minutes("60", " 15 ") == (15, "")
    assert actions.resolve_timeout_minutes("", "")[0] == actions.TIMEOUT_DEFAULT_MINUTES
    assert actions.resolve_timeout_minutes("60", "abc")[0] is None
    assert actions.resolve_timeout_minutes("60", "0")[0] is None
    assert actions.resolve_timeout_minutes("60", "40321")[0] is None
    assert actions.describe_minutes(1440) == "1 day"
    assert actions.describe_minutes(15) == "15 minute(s)"


def test_timeout_modal_defaults_to_one_hour_and_reads_choices() -> None:
    modal = actions.MemberTimeoutModal(parent=None)
    select = modal.duration.component

    assert [option.value for option in select.options if option.default] == [str(actions.TIMEOUT_DEFAULT_MINUTES)]
    assert modal._chosen_minutes() == (actions.TIMEOUT_DEFAULT_MINUTES, "")

    select._values = ["360"]
    assert modal._chosen_minutes() == (360, "")

    modal.exact_minutes.component._value = "25"
    assert modal._chosen_minutes() == (25, "")

    modal.reason.component._value = "  spamming invites  "
    assert modal._reason_text() == "spamming invites"


def test_timeout_modals_do_not_share_state() -> None:
    first = actions.MemberTimeoutModal(parent=None)
    second = actions.MemberTimeoutModal(parent=None)
    first.duration.component._values = ["10"]

    assert second._chosen_minutes() == (actions.TIMEOUT_DEFAULT_MINUTES, "")
