from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CLEANUP = ROOT / "stoney_verify/commands_ext/public_setup_cleanup.py"
HISTORY = ROOT / "stoney_verify/config_history_ui.py"
TESTS = ROOT / "tests/test_setup_nested_navigation_behavior.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly 1 match, found {count}"
        )
    return text.replace(old, new, 1)


def compile_text(path: Path, text: str) -> None:
    compile(text, str(path), "exec")


cleanup = CLEANUP.read_text(encoding="utf-8")
history = HISTORY.read_text(encoding="utf-8")
tests = TESTS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Repair / Restart navigation
# ---------------------------------------------------------------------------

nav_block = '''async def _open_cleanup_preview_screen(
    interaction: discord.Interaction,
) -> None:
    """Open the canonical cleanup preview without losing setup navigation."""

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    await solid._safe_defer_update(interaction)
    embed = await build_cleanup_preview_embed(guild)
    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=CleanupPreviewView(),
    )


async def _open_repair_parent(
    interaction: discord.Interaction,
    parent: str,
) -> None:
    """Return Repair / Restart screens to their actual logical parent."""

    from . import public_setup_recommend as recommend

    clean_parent = str(parent or "section").strip().lower()
    if clean_parent == "cleanup":
        await _open_cleanup_preview_screen(interaction)
        return
    if clean_parent == "center":
        await recommend._open_recovery_center(interaction)
        return

    await recommend._open_advanced_danger_zone(interaction)


class RepairNavigationView(discord.ui.View):
    """One logical Back route plus Setup Home and Close."""

    def __init__(self, *, parent: str = "section") -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "section").strip().lower() or "section"

    @discord.ui.button(
        label="Back",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_repair_nav:back",
        row=4,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_repair_parent(interaction, self.parent)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_repair_nav:home",
        row=4,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from . import public_setup_recommend as recommend
        await recommend._home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_repair_nav:close",
        row=4,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from . import public_setup_recommend as recommend
        await recommend._close_setup(interaction)


'''

cleanup = replace_once(
    cleanup,
    "class RemoveOneSelect(discord.ui.Select):\n",
    nav_block + "class RemoveOneSelect(discord.ui.Select):\n",
    "insert repair navigation owner",
)

cleanup = replace_once(
    cleanup,
    '''class RemoveOneView(solid.BackToSetupView):
    def __init__(self, candidates: list[CleanupCandidate]) -> None:
        super().__init__()
        self.add_item(RemoveOneSelect(candidates))
''',
    '''class RemoveOneView(RepairNavigationView):
    def __init__(self, candidates: list[CleanupCandidate]) -> None:
        super().__init__(parent="cleanup")
        self.add_item(RemoveOneSelect(candidates))
''',
    "RemoveOneView logical parent",
)

cleanup = replace_once(
    cleanup,
    '''class ConfirmOneView(solid.BackToSetupView):
    def __init__(self, selected_value: str) -> None:
        super().__init__()
        self.selected_value = selected_value
''',
    '''class ConfirmOneView(RepairNavigationView):
    def __init__(self, selected_value: str) -> None:
        super().__init__(parent="cleanup")
        self.selected_value = selected_value
''',
    "ConfirmOneView logical parent",
)

cleanup = replace_once(
    cleanup,
    '''    @discord.ui.button(
        label="Back to Cleanup Preview",
        emoji="🔎",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_cleanup:back_preview",
        row=1,
    )
    async def back_preview(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await solid._safe_defer_update(interaction)
        embed = await build_cleanup_preview_embed(guild)
        await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=CleanupPreviewView(),
        )


''',
    "",
    "remove duplicate ConfirmOne back route",
)

cleanup = replace_once(
    cleanup,
    "class CleanupPreviewView(solid.BackToSetupView):\n",
    '''class CleanupPreviewView(RepairNavigationView):
    def __init__(self) -> None:
        super().__init__(parent="center")

''',
    "CleanupPreviewView logical parent",
)

cleanup = replace_once(
    cleanup,
    '''    @discord.ui.button(
        label="Back to Repair & Restart",
        emoji="🛟",
        style=discord.ButtonStyle.secondary,
        custom_id="stoney_cleanup:back_recovery",
        row=3,
    )
    async def back_recovery(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await solid._safe_defer_update(interaction)
        embed = await patched_recovery_embed(guild)
        await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=PatchedRecoveryCenterView(),
        )


''',
    "",
    "remove duplicate CleanupPreview back route",
)

cleanup = replace_once(
    cleanup,
    '''class ConfirmTypeView(solid.BackToSetupView):
    def __init__(self, mode: str, label: str) -> None:
        super().__init__()
        self.mode = mode
        self.label = label
''',
    '''class ConfirmTypeView(RepairNavigationView):
    def __init__(self, mode: str, label: str) -> None:
        super().__init__(parent="cleanup")
        self.mode = mode
        self.label = label
''',
    "ConfirmTypeView logical parent",
)

cleanup = replace_once(
    cleanup,
    "class PatchedRecoveryCenterView(solid.BackToSetupView):\n",
    "class PatchedRecoveryCenterView(RepairNavigationView):\n",
    "PatchedRecoveryCenterView logical parent",
)

cleanup = replace_once(
    cleanup,
    "use **Quick Setup** or **Manage Setup** to recreate or remap ",
    "use **Continue Setup** or **Manage Setup** to recreate or remap ",
    "repair completion terminology",
)


# ---------------------------------------------------------------------------
# Backups & History: version detail gets one logical Back route.
# ---------------------------------------------------------------------------

history = replace_once(
    history,
    '''    @discord.ui.button(
        label="Back to All Features",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_settings",
        row=2,
    )
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_all_features(interaction)


''',
    "",
    "remove redundant version-detail All Features route",
)


# ---------------------------------------------------------------------------
# Behavioral expectations for the new hierarchy.
# ---------------------------------------------------------------------------

tests = replace_once(
    tests,
    '''    assert "Back to All Features" in labels(detail)
    assert "Setup Home" in labels(detail)
    assert "Close" in labels(detail)
''',
    '''    detail_labels = labels(detail)
    assert "Back to History" in detail_labels
    assert "Back to All Features" not in detail_labels
    assert "Setup Home" in detail_labels
    assert "Close" in detail_labels
''',
    "config-history detail navigation expectations",
)

tests = replace_once(
    tests,
    '''def test_repair_and_cleanup_views_keep_navigation_available() -> None:
    for view in (
        recovery.RecoveryCenterView(),
        cleanup.PatchedRecoveryCenterView(),
        cleanup.CleanupPreviewView(),
    ):
        view_labels = labels(view)
        assert "Back to All Features" in view_labels
        assert "Setup Home" in view_labels
        assert "Close" in view_labels
        assert len(view.children) <= 25
''',
    '''def test_canonical_repair_views_use_one_logical_back_route() -> None:
    views = (
        (cleanup.PatchedRecoveryCenterView(), "section"),
        (cleanup.CleanupPreviewView(), "center"),
        (cleanup.RemoveOneView([]), "cleanup"),
        (cleanup.ConfirmOneView("text_channel:1"), "cleanup"),
        (
            cleanup.ConfirmTypeView(
                "channels",
                "setup channels",
            ),
            "cleanup",
        ),
    )

    for view, parent in views:
        view_labels = labels(view)
        assert "Back" in view_labels
        assert "Back to All Features" not in view_labels
        assert "Setup Home" in view_labels
        assert "Close" in view_labels
        assert getattr(view, "parent", None) == parent

        row_counts: dict[int, int] = {}
        for child in view.children:
            row = int(getattr(child, "row", 0) or 0)
            row_counts[row] = row_counts.get(row, 0) + 1
        assert all(count <= 5 for count in row_counts.values())
        assert len(view.children) <= 25
''',
    "repair navigation behavior expectations",
)


# Validate syntax before touching the working files.
for path, text in (
    (CLEANUP, cleanup),
    (HISTORY, history),
    (TESTS, tests),
):
    compile_text(path, text)

CLEANUP.write_text(cleanup, encoding="utf-8")
HISTORY.write_text(history, encoding="utf-8")
TESTS.write_text(tests, encoding="utf-8")

subprocess.run(
    ["git", "diff", "--check"],
    cwd=ROOT,
    check=True,
)

# This helper is intentionally temporary. Remove it from the final tree now.
Path(__file__).unlink()

print("✅ Nested setup navigation patch applied safely.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
