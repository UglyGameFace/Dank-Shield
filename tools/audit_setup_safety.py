from __future__ import annotations

"""Safety audit for the public /dank setup surface.

This catches regressions that are easy to miss in Discord UI testing:
- broad setup UX monkey-patches leaking into unrelated screens
- optional verification idle kick not documented as off-by-default/per-server
- private server IDs/names in setup-facing code
"""

from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]

PRIVATE_MARKERS = (
    "1098088221457514609",
    "1232631147649830992",
    "1317042307903651901",
    "1357215261001912320",
    "1514374173517152418",
    "Stoney Balonney",
    "The 420 Lobby",
    "DickHeads",
)

SETUP_FILES = [
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_solid.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_group.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_start.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_feature_health_scoreboard.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_scoreboard_command.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_verification_idle_kick_controls.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_ux_clarity_guard.py",
    ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _assert_setup_ux_guard_is_scoped(failures: list[str]) -> None:
    path = ROOT / "stoney_verify" / "startup_guards" / "setup_ux_clarity_guard.py"
    text = _read(path)
    required = (
        "_is_setup_embed",
        "_view_has_setup_controls",
        "should_polish",
        "_SETUP_CUSTOM_ID_PREFIXES",
    )
    for needle in required:
        if needle not in text:
            failures.append(f"{path.relative_to(ROOT)}: setup UX guard is missing scoped gate `{needle}`")


def _assert_idle_kick_is_per_guild_and_off_by_default(failures: list[str]) -> None:
    feature = ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py"
    controls = ROOT / "stoney_verify" / "startup_guards" / "setup_verification_idle_kick_controls.py"
    feature_text = _read(feature)
    controls_text = _read(controls)

    required_feature = (
        '"verification_idle_kick_enabled"',
        '"verification_idle_kick_minutes"',
        "enabled = _safe_bool",
        "False)",
        "member.guild.id",
        "guild.id",
        "_is_pending",
        "_open_verification_ticket",
    )
    for needle in required_feature:
        if needle not in feature_text:
            failures.append(f"{feature.relative_to(ROOT)}: idle-kick feature missing required per-guild/off-by-default marker `{needle}`")

    required_controls = (
        "Optional per-server feature",
        "off by default",
        "verification_idle_kick_enabled",
        "verification_idle_kick_minutes",
        "Enable / Set Minutes",
        "Disable",
    )
    for needle in required_controls:
        if needle not in controls_text:
            failures.append(f"{controls.relative_to(ROOT)}: setup controls missing plain-language marker `{needle}`")


def _assert_no_private_markers(failures: list[str]) -> None:
    for path in SETUP_FILES:
        text = _read(path)
        for marker in PRIVATE_MARKERS:
            if marker in text:
                failures.append(f"{path.relative_to(ROOT)}: private marker must not appear in public setup code: {marker}")


def _assert_python_parseable(failures: list[str]) -> None:
    for path in SETUP_FILES:
        if not path.exists():
            continue
        try:
            ast.parse(_read(path), filename=str(path))
        except SyntaxError as e:
            failures.append(f"{path.relative_to(ROOT)}:{e.lineno}: syntax error: {e.msg}")


def main() -> int:
    failures: list[str] = []
    _assert_python_parseable(failures)
    _assert_no_private_markers(failures)
    _assert_setup_ux_guard_is_scoped(failures)
    _assert_idle_kick_is_per_guild_and_off_by_default(failures)

    if failures:
        print("Setup safety audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Setup safety audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
