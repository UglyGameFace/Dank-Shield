from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ROOT = REPO_ROOT / "stoney_verify"

failures: list[str] = []


def text(path: str) -> str:
    return Path(path).read_text(errors="ignore")


# Central extraction must not false-positive normal URLs containing discord.gg in a path/query.
from stoney_verify.invite_policy_engine import extract_invite_codes_from_text

false_positive_samples = [
    "https://google.com",
    "https://discord.com",
    "https://example.com/redirect/discord.gg/testcode",
    "https://example.com/?next=discord.gg/testcode",
    "https://github.com/UglyGameFace/Dank-Shield",
]

for sample in false_positive_samples:
    found = extract_invite_codes_from_text(sample)
    if found:
        failures.append(f"central extractor false-positive {sample!r} -> {found}")

# Discord-generated unfurls and interaction response chrome are not sender-authored
# invite evidence. Utility app responses may contain a support-server button even
# when the user invoked the app for a normal non-invite action.
from stoney_verify import invite_policy_engine as invite_policy
from stoney_verify import invite_policy_message_surface_runtime as message_surface

try:
    setattr(invite_policy, message_surface._INSTALL_FLAG, False)  # noqa: SLF001
    message_surface.install_invite_policy_message_surface_runtime()

    preview = SimpleNamespace(
        type="link",
        title="Video downloader",
        description="Support server: https://discord.gg/remotehelp",
        url="https://example-video-downloader.invalid/",
        fields=[],
        footer=None,
        author=None,
    )
    human_normal = SimpleNamespace(
        content="https://example-video-downloader.invalid/watch?v=123",
        author=SimpleNamespace(id=1, bot=False),
        embeds=[preview],
        components=[],
        attachments=[],
        interaction_metadata=None,
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(human_normal)
    if found:
        failures.append(f"human normal link inherited invite from generated preview -> {found}")

    human_invite = SimpleNamespace(
        content="https://discord.gg/realinvite",
        author=SimpleNamespace(id=1, bot=False),
        embeds=[preview],
        components=[],
        attachments=[],
        interaction_metadata=None,
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(human_invite)
    if found != ["realinvite"]:
        failures.append(f"human explicit invite was not preserved -> {found}")

    bot_preview = SimpleNamespace(
        content="https://example-video-downloader.invalid/watch?v=456",
        author=SimpleNamespace(id=2, bot=True),
        embeds=[preview],
        components=[],
        attachments=[],
        interaction_metadata=None,
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(bot_preview)
    if found:
        failures.append(f"bot normal link inherited invite from generated preview -> {found}")

    rich = SimpleNamespace(
        type="rich",
        title="Server invite",
        description="Join https://discord.gg/botinvite",
        url=None,
        fields=[],
        footer=None,
        author=None,
    )
    bot_rich = SimpleNamespace(
        content="",
        author=SimpleNamespace(id=2, bot=True),
        embeds=[rich],
        components=[],
        attachments=[],
        interaction_metadata=None,
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(bot_rich)
    if found != ["botinvite"]:
        failures.append(f"bot-authored rich invite was not preserved -> {found}")

    support_button = SimpleNamespace(
        url="https://discord.gg/remotehelp",
        children=[],
    )
    app_response = SimpleNamespace(
        content="",
        author=SimpleNamespace(id=3, bot=True),
        embeds=[rich],
        components=[support_button],
        attachments=[],
        interaction_metadata=SimpleNamespace(
            id=999,
            user=SimpleNamespace(id=1),
        ),
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(app_response)
    if found:
        failures.append(f"interaction utility response inherited support invite -> {found}")

    app_explicit = SimpleNamespace(
        content="https://discord.gg/explicitappinvite",
        author=SimpleNamespace(id=3, bot=True),
        embeds=[rich],
        components=[support_button],
        attachments=[],
        interaction_metadata=SimpleNamespace(
            id=999,
            original_response_message_id=777,
            user=SimpleNamespace(id=1),
        ),
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(app_explicit)
    if found != ["explicitappinvite"]:
        failures.append(f"interaction explicit content invite was not preserved -> {found}")
except Exception as exc:
    failures.append(f"message-surface invite audit failed: {type(exc).__name__}: {exc}")

# Protection Center historical invite cleanup is now owned by the native UI and
# must delegate deletion decisions to the central invite policy engine.
native = text("stoney_verify/commands_ext/public_protection_invite_ui.py")
if "scan_channel_invites" not in native:
    failures.append("public_protection_invite_ui cleanup does not call central scan_channel_invites")
if "protection-center-native-invite-cleanup" not in native:
    failures.append("public_protection_invite_ui cleanup source marker is missing")
if "await message.delete(reason=" in native or "await message.delete()" in native:
    failures.append("public_protection_invite_ui contains a direct message.delete path")

retired_invite_ui_guards = (
    "protection_center_invite_simple_flow_guard.py",
    "protection_center_invite_controls_guard.py",
    "protection_center_invite_status_guard.py",
    "spam_guard_invite_scope_pagination_guard.py",
    "invite_hard_block_all_bots_controls_guard.py",
    "protection_invite_cleanup_picker_guard.py",
    "protection_invite_toggle_cleanup_guard.py",
)
for filename in retired_invite_ui_guards:
    if (ROOT / "startup_guards" / filename).exists():
        failures.append(f"retired Invite Shield UI guard still exists: {filename}")

# Spam cleanup may delete normal spam bursts, but invite-containing messages must go through central policy.
psc = text("stoney_verify/commands_ext/public_spam_cleanup_hardening.py")
if "policy.extract_invite_codes_from_message(message)" not in psc:
    failures.append("public_spam_cleanup_hardening does not detect invite messages before delete")
if "policy.delete_message_if_allowed(message, decision)" not in psc:
    failures.append("public_spam_cleanup_hardening does not delegate invite deletes to central policy")

# Live invite paths should use central decision/delete helpers.
live = text("stoney_verify/startup_guards/invite_live_enforcer_guard.py")
if "decide_invite_message" not in live or "delete_message_if_allowed" not in live:
    failures.append("invite_live_enforcer_guard is not using central decision/delete helpers")

runtime = text("stoney_verify/startup_guards/discord_invite_blocker_runtime_guard.py")
if "policy.decide_invite_message" not in runtime or "policy.delete_message_if_allowed" not in runtime:
    failures.append("discord_invite_blocker_runtime_guard is not using central decision/delete helpers")

print("=== Invite Link Safety Audit ===")
if failures:
    for item in failures:
        print("FAIL:", item)
    raise SystemExit(1)

print("PASS: invite extraction and invite-delete paths are centralized/safe.")
