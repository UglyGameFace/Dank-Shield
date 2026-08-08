from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import lifecycle_template_renderer as renderer


class FakeTextChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"


class FakeGuild:
    def __init__(self) -> None:
        self.id = 44
        self.name = "Vibers Paradise"
        self.member_count = 125
        self._channels = {
            10: FakeTextChannel(10, "rules"),
            11: FakeTextChannel(11, "verification"),
            12: FakeTextChannel(12, "support"),
        }
        self.text_channels = list(self._channels.values())
        self.categories = []

    def get_channel(self, channel_id: int):
        return self._channels.get(int(channel_id))


class FakeMember:
    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild
        self.id = 77
        self.name = "9byte"
        self.display_name = "Nine Byte"
        self.mention = "<@77>"
        now = discord.utils.utcnow()
        self.created_at = now - timedelta(days=400)
        self.joined_at = now - timedelta(minutes=5)

    def __str__(self) -> str:
        return "9byte"


@pytest.fixture(autouse=True)
def discord_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer.discord, "TextChannel", FakeTextChannel)


def _world() -> tuple[FakeMember, dict[str, object]]:
    guild = FakeGuild()
    member = FakeMember(guild)
    cfg = {
        "rules_channel_id": "10",
        "verify_channel_id": "11",
        "support_channel_id": "12",
    }
    return member, cfg


def test_username_variants_never_leak_braces() -> None:
    member, cfg = _world()
    text = (
        "{username}|{ UserName }|{{ username }}|"
        "{\u200busername\u200b}|{display_name}|{ server_name }"
    )

    rendered = renderer.render_lifecycle_template(text, member, cfg)

    assert rendered == "9byte|9byte|9byte|9byte|Nine Byte|Vibers Paradise"
    assert renderer.unresolved_known_placeholders(rendered) == ()


def test_channels_count_age_and_join_time_share_one_renderer() -> None:
    member, cfg = _world()
    rendered = renderer.render_lifecycle_template(
        "{member_count} {rules_channel} {verify_channel} {support_channel} "
        "{account_age} {joined_at}",
        member,
        cfg,
    )

    assert "125" in rendered
    assert "<#10>" in rendered
    assert "<#11>" in rendered
    assert "<#12>" in rendered
    assert "1y" in rendered
    assert "<t:" in rendered
    assert "{joined_at}" not in rendered


def test_known_invite_placeholders_get_safe_fallback_without_fake_attribution() -> None:
    member, cfg = _world()
    live = renderer.render_lifecycle_template(
        "Invite {invite_code} from {invite_inviter}",
        member,
        cfg,
    )
    preview = renderer.render_lifecycle_template(
        "Invite {invite_code} from {invite_inviter}",
        member,
        cfg,
        preview=True,
    )

    assert live == "Invite unavailable from unavailable"
    assert preview == "Invite real join only from real join only"
    assert "{" not in live


def test_real_invite_values_can_be_supplied_without_changing_templates() -> None:
    member, cfg = _world()
    rendered = renderer.render_lifecycle_template(
        "Used { invite_code } • invited by {INVITE_INVITER}",
        member,
        cfg,
        invite_values={"invite_code": "420420", "invite_inviter": "Stoney"},
    )
    assert rendered == "Used 420420 • invited by Stoney"


def test_unknown_owner_brace_text_is_preserved() -> None:
    member, cfg = _world()
    rendered = renderer.render_lifecycle_template(
        "Keep {custom_owner_token} and JSON {not_a_real_placeholder}.",
        member,
        cfg,
    )
    assert rendered == "Keep {custom_owner_token} and JSON {not_a_real_placeholder}."
