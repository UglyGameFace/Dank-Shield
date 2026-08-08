from __future__ import annotations

from stoney_verify.startup_guards import dank_shield_branding_guard as guard


def test_retired_command_hints_render_as_live_menu_paths() -> None:
    text = (
        "Use `/verify fix-member`, then `/ticket reopen`, and upload art with "
        "`/dank welcome exit-card-upload`. Setup is under `/dank setup`."
    )
    rendered = guard._clean(text)
    assert "`/verify fix-member`" not in rendered
    assert "`/ticket reopen`" not in rendered
    assert "`/dank welcome exit-card-upload`" not in rendered
    assert "`/dank setup`" not in rendered
    assert "`/verify` → choose a member → **Restore Pending**" in rendered
    assert "`/ticket` → **Reopen**" in rendered
    assert "`/dank upload` → **Exit Card Background**" in rendered
    assert "`/dank home` → **Setup & Settings**" in rendered


def test_exact_hint_rewrite_does_not_mangle_other_command_names() -> None:
    original = "Internal diagnostic label: `/dank setup-status` and `/verify-sandbox`."
    assert guard._clean(original) == original
