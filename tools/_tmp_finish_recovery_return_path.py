from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "stoney_verify/commands_ext/public_setup_recovery.py"
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
RECOVERY_TESTS = ROOT / "tests/test_setup_recovery_behavior.py"
ADVANCED_TESTS = ROOT / "tests/test_setup_advanced_options_behavior.py"
HELPER = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def compile_text(path: Path, text: str) -> None:
    compile(text, str(path), "exec")


recovery = RECOVERY.read_text(encoding="utf-8")
recommend = RECOMMEND.read_text(encoding="utf-8")
recovery_tests = RECOVERY_TESTS.read_text(encoding="utf-8")
advanced_tests = ADVANCED_TESTS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Keep every active recovery return path inside the canonical Repair Center.
# The legacy RecoveryCenterView remains a fallback only when the cleanup UX
# cannot be imported.
# ---------------------------------------------------------------------------

canonical_view_helper = '''\n\ndef _canonical_recovery_view() -> discord.ui.View:\n    """Return the canonical Repair Center when its UX layer is available."""\n\n    try:\n        from . import public_setup_cleanup as cleanup\n\n        view_cls = getattr(\n            cleanup,\n            "PatchedRecoveryCenterView",\n            None,\n        )\n        if callable(view_cls):\n            return view_cls()\n    except Exception:\n        pass\n\n    return RecoveryCenterView()\n'''

recovery = replace_once(
    recovery,
    "\n\nclass RecoveryButton(discord.ui.Button):",
    canonical_view_helper + "\n\nclass RecoveryButton(discord.ui.Button):",
    "insert canonical recovery view helper",
)

old_view = "view=RecoveryCenterView(),"
view_count = recovery.count(old_view)
if view_count != 5:
    raise RuntimeError(
        "canonical recovery return paths: expected exactly 5 legacy view "
        f"returns, found {view_count}"
    )
recovery = recovery.replace(
    old_view,
    "view=_canonical_recovery_view(),",
)

recovery = replace_once(
    recovery,
    '''                "Run `/dank setup` and choose **Quick Setup**. "\n                "It will start from a fresh saved state."''',
    '''                "Run `/dank setup` and press **Start Setup**. "\n                "It will start from a fresh saved state."''',
    "safe start over next-step wording",
)


# ---------------------------------------------------------------------------
# Match the advanced-section footer to the actual Back button label.
# ---------------------------------------------------------------------------

recommend = replace_once(
    recommend,
    '"All Features & Settings • use Back to All Features & Settings to return"',
    '"All Features & Settings • use Back to All Features to return"',
    "advanced section footer wording",
)


# ---------------------------------------------------------------------------
# Behavioral regressions.
# ---------------------------------------------------------------------------

recovery_test_marker = '''\ndef test_fallback_recovery_view_matches_current_recovery_language() -> None:\n'''
recovery_test_block = '''\ndef test_recovery_results_return_to_canonical_repair_center() -> None:\n    view = recovery._canonical_recovery_view()\n\n    assert isinstance(view, cleanup.PatchedRecoveryCenterView)\n    labels = set(button_labels(view))\n    assert "Back" in labels\n    assert "Back to All Features" not in labels\n    assert "Setup Home" in labels\n    assert "Close" in labels\n\n\n'''

recovery_tests = replace_once(
    recovery_tests,
    recovery_test_marker,
    recovery_test_block + recovery_test_marker,
    "canonical recovery result regression",
)

advanced_test_marker = '''\ndef test_repair_is_not_mixed_into_normal_feature_sections() -> None:\n'''
advanced_test_block = '''\ndef test_advanced_section_footer_matches_back_button_label() -> None:\n    embed = recommend._advanced_section_embed(\n        title="Test",\n        description="Test",\n        items=("Test",),\n    )\n    footer = str(embed.footer.text or "")\n\n    assert "Back to All Features" in footer\n    assert "Back to All Features & Settings" not in footer\n\n\n'''

advanced_tests = replace_once(
    advanced_tests,
    advanced_test_marker,
    advanced_test_block + advanced_test_marker,
    "advanced footer regression",
)


for path, text in (
    (RECOVERY, recovery),
    (RECOMMEND, recommend),
    (RECOVERY_TESTS, recovery_tests),
    (ADVANCED_TESTS, advanced_tests),
):
    compile_text(path, text)
    path.write_text(text, encoding="utf-8")

# Remove this temporary staging helper from the final branch tree.
HELPER.unlink()

subprocess.run(
    ["git", "diff", "--check"],
    cwd=ROOT,
    check=True,
)

print("✅ Final recovery return-path patch applied safely.")
print("✅ Recovery actions now return to the canonical Repair Center.")
print("✅ Advanced footer wording matches the actual Back button.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
