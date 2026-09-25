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

    v2_text = SimpleNamespace(
        content="Bump card: https://discord.gg/visiblev2",
        url=None,
        children=[],
        accessory=None,
    )
    v2_container = SimpleNamespace(
        content=None,
        url=None,
        children=[v2_text, support_button],
        accessory=None,
    )
    app_v2 = SimpleNamespace(
        content="",
        author=SimpleNamespace(id=3, bot=True),
        embeds=[rich],
        components=[v2_container],
        attachments=[],
        interaction_metadata=SimpleNamespace(
            id=999,
            user=SimpleNamespace(id=1),
        ),
        interaction=None,
    )
    found = invite_policy.extract_invite_codes_from_message(app_v2)
    if found != ["visiblev2"]:
        failures.append(f"interaction visible Components V2 invite was not preserved -> {found}")

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

# Live invite ownership is canonical: globals -> invite_policy_engine.
globals_source = text("stoney_verify/globals.py")
if "enforce_live_invite_message" not in globals_source:
    failures.append("globals live invite listener does not call enforce_live_invite_message")
if 'source="globals_live_enforcer"' not in globals_source:
    failures.append("globals live invite listener source marker is missing")

policy_source = text("stoney_verify/invite_policy_engine.py")
if "async def enforce_live_invite_message(" not in policy_source:
    failures.append("invite_policy_engine is missing canonical live enforcement boundary")

recovery_source = text("stoney_verify/invite_reconciliation_runtime.py")
if "policy.scan_channel_invites(" not in recovery_source:
    failures.append("invite reconciliation runtime is not using the central scanner")

for filename in (
    "invite_live_enforcer_guard.py",
    "discord_invite_blocker_runtime_guard.py",
    "spam_guard_invite_hard_block.py",
    "spam_guard_invite_override_options.py",
    "protection_invite_target_precedence_guard.py",
):
    if (ROOT / "startup_guards" / filename).exists():
        failures.append(f"retired legacy invite runtime guard still exists: {filename}")

# Content-redacted app-card enforcement must stay generic, narrow, and centralized.
policy_source = text("stoney_verify/invite_policy_engine.py")
if "1028956609382199346" in policy_source:
    failures.append("canonical invite policy still hardcodes the OneBump application identity")
if "is_contentless_protected_poster_candidate" not in policy_source:
    failures.append("canonical invite policy is missing generic contentless protected-poster boundary")
if "protected_contentless_poster" not in policy_source:
    failures.append("canonical invite policy is missing explicit-target contentless delete rule")
if "allow_contentless_protected_posters" not in policy_source:
    failures.append("historical scanner cannot receive explicit contentless protected-poster authority")
if "_SCAN_HISTORY_MAX = 2000" not in policy_source:
    failures.append("canonical invite history scanner is not bounded for deep cleanup")

recovery_source = text("stoney_verify/invite_reconciliation_runtime.py")
if "policy.is_contentless_protected_poster_candidate(message)" not in recovery_source:
    failures.append("live invite reconciliation cannot trigger on generic content-redacted protected posters")
if "_INVITE_CHECKPOINT_KEY" not in recovery_source:
    failures.append("invite reconciliation is missing its own durable checkpoint")
if "persisted_last_heartbeat_at" in recovery_source:
    failures.append("invite reconciliation is still incorrectly coupled to the activity heartbeat")
if '"on_raw_message_edit"' not in recovery_source:
    failures.append("invite reconciliation is missing uncached raw-message edit recovery")

native_source = text("stoney_verify/commands_ext/public_protection_invite_ui.py")
if "limit=1000" not in native_source:
    failures.append("Protection Center historical invite cleanup is not using the deeper bounded pass")
if "allow_contentless_protected_posters=True" not in native_source:
    failures.append("Protection Center cleanup does not explicitly authorize protected contentless posters")

print("=== Invite Link Safety Audit ===")
if failures:
    for item in failures:
        print("FAIL:", item)
    raise SystemExit(1)

print("PASS: invite extraction and invite-delete paths are centralized/safe.")
