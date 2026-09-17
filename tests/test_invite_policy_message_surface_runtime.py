from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify import invite_policy_engine as policy
from stoney_verify import invite_policy_message_surface_runtime as surface


def _embed_with_remote_invite(*, embed_type: str = "rich"):
    return SimpleNamespace(
        type=embed_type,
        title="Video downloader",
        description="Download videos here. Join support: https://discord.gg/remotehelp",
        url="https://example-video-downloader.invalid/",
        fields=[],
        footer=None,
        author=None,
    )


def _support_button():
    return SimpleNamespace(
        url="https://discord.gg/remotehelp",
        children=[],
    )


def _message(
    *,
    content: str,
    bot: bool,
    embeds=None,
    components=None,
    interaction_metadata=None,
    interaction=None,
):
    return SimpleNamespace(
        content=content,
        author=SimpleNamespace(id=123, bot=bot),
        embeds=list(embeds or []),
        components=list(components or []),
        attachments=[],
        interaction_metadata=interaction_metadata,
        interaction=interaction,
    )


def _install_for_test():
    original = policy.message_text
    had_flag = hasattr(policy, surface._INSTALL_FLAG)  # noqa: SLF001
    old_flag = getattr(policy, surface._INSTALL_FLAG, None)  # noqa: SLF001
    had_original = hasattr(policy, surface._ORIGINAL_ATTR)  # noqa: SLF001
    old_original = getattr(policy, surface._ORIGINAL_ATTR, None)  # noqa: SLF001

    if had_flag:
        delattr(policy, surface._INSTALL_FLAG)  # noqa: SLF001
    if had_original:
        delattr(policy, surface._ORIGINAL_ATTR)  # noqa: SLF001

    assert surface.install_invite_policy_message_surface_runtime() is True

    def restore() -> None:
        policy.message_text = original
        if had_flag:
            setattr(policy, surface._INSTALL_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(policy, surface._INSTALL_FLAG):  # noqa: SLF001
            delattr(policy, surface._INSTALL_FLAG)  # noqa: SLF001
        if had_original:
            setattr(policy, surface._ORIGINAL_ATTR, old_original)  # noqa: SLF001
        elif hasattr(policy, surface._ORIGINAL_ATTR):  # noqa: SLF001
            delattr(policy, surface._ORIGINAL_ATTR)  # noqa: SLF001

    return restore


def test_human_normal_url_ignores_discord_invite_from_generated_preview() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="https://example-video-downloader.invalid/watch?v=123",
            bot=False,
            embeds=[_embed_with_remote_invite(embed_type="link")],
        )
        assert policy.extract_invite_codes_from_message(message) == []
    finally:
        restore()


def test_human_explicit_invite_in_content_is_still_detected() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="join this https://discord.gg/realinvite",
            bot=False,
            embeds=[_embed_with_remote_invite(embed_type="link")],
        )
        assert policy.extract_invite_codes_from_message(message) == ["realinvite"]
    finally:
        restore()


def test_bot_custom_rich_embed_invite_remains_detectable() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="",
            bot=True,
            embeds=[_embed_with_remote_invite(embed_type="rich")],
        )
        assert policy.extract_invite_codes_from_message(message) == ["remotehelp"]
    finally:
        restore()


def test_bot_normal_url_ignores_generated_link_preview_metadata() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="https://example-video-downloader.invalid/watch?v=456",
            bot=True,
            embeds=[_embed_with_remote_invite(embed_type="link")],
        )
        assert policy.extract_invite_codes_from_message(message) == []
    finally:
        restore()


def test_interaction_response_ignores_support_server_embed_and_button() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="",
            bot=True,
            embeds=[_embed_with_remote_invite(embed_type="rich")],
            components=[_support_button()],
            interaction_metadata=SimpleNamespace(id=999, user=SimpleNamespace(id=321)),
        )
        assert policy.extract_invite_codes_from_message(message) == []
    finally:
        restore()


def test_interaction_followup_explicit_content_invite_is_still_detected() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="Join https://discord.gg/explicitappinvite",
            bot=True,
            embeds=[_embed_with_remote_invite(embed_type="rich")],
            components=[_support_button()],
            interaction_metadata=SimpleNamespace(
                id=999,
                original_response_message_id=777,
                user=SimpleNamespace(id=321),
            ),
        )
        assert policy.extract_invite_codes_from_message(message) == ["explicitappinvite"]
    finally:
        restore()


def test_legacy_interaction_marker_uses_content_only() -> None:
    restore = _install_for_test()
    try:
        message = _message(
            content="https://x.com/example/status/123",
            bot=True,
            embeds=[_embed_with_remote_invite(embed_type="rich")],
            components=[_support_button()],
            interaction=SimpleNamespace(id=888),
        )
        assert policy.extract_invite_codes_from_message(message) == []
    finally:
        restore()


def test_runtime_is_idempotent() -> None:
    restore = _install_for_test()
    try:
        assert surface.install_invite_policy_message_surface_runtime() is False
    finally:
        restore()


def test_main_installs_message_surface_guard_before_invite_reconciliation() -> None:
    source = Path("main.py").read_text(encoding="utf-8")
    guard = source.index("    _install_invite_policy_message_surface_runtime()")
    reconciliation = source.index("    _install_invite_reconciliation_runtime()", guard)
    app_import = source.index("from stoney_verify.app import run as _run_dank_shield")
    assert guard < reconciliation < app_import
