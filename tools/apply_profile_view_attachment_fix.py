from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "stoney_verify/commands_ext/public_profile_cards_core.py"
TEST_PATH = ROOT / "tests/test_profile_role_display_separation.py"


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    replace_once(
        CORE_PATH,
        '''    await _send_private(
        interaction,
        embed=rendered_embed,
        view=view if view.children else None,
    )


''',
        '''    await _send_private(
        interaction,
        embed=rendered_embed,
        view=view if view.children else None,
        file=rendered.file if rendered is not None else None,
    )


''',
        label="member profile banner attachment",
    )

    test_text = TEST_PATH.read_text(encoding="utf-8")
    marker = "def test_member_profile_view_attaches_generated_wide_banner()"
    if marker in test_text:
        raise RuntimeError("member profile attachment regression already exists")
    test_block = '''


def test_member_profile_view_attaches_generated_wide_banner() -> None:
    profile_send = PRIVACY_CORE.split("async def send_privacy_aware_profile", 1)[1].split(
        "def _live_status_embed", 1
    )[0]
    assert "file=rendered.file if rendered is not None else None" in profile_send
    assert "render_live_profile_card(" in profile_send
'''
    TEST_PATH.write_text(test_text.rstrip() + test_block.rstrip() + "\n", encoding="utf-8")

    Path(__file__).unlink()
    print("Attached the generated wide banner to member-profile responses and added regression coverage.")


if __name__ == "__main__":
    main()
