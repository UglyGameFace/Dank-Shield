from __future__ import annotations

from pathlib import Path
import re

import discord

from stoney_verify import config_history_ui
from stoney_verify.commands_ext import public_setup_cleanup
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid


ROOT = Path(__file__).resolve().parents[1]


def _close_button(view: discord.ui.View) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "label", "") or "") == "Close"
    ]
    assert len(matches) == 1
    return matches[0]


def test_active_setup_close_buttons_are_visually_distinct() -> None:
    history_plan = {
        "changed_items": ["sample_setting"],
        "missing_items": [],
        "item_labels": {"sample_setting": "Sample Setting"},
    }
    views = (
        solid.SetupNavView(),
        solid.SolidSetupView(),
        recommend.ProductSetupHomeView(),
        recommend.ContinueSetupView(target="retry", ready=False),
        recommend.SetupReviewView(ready=False),
        recommend.ManageSetupView(),
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
        recommend.LaunchTestView({}),
        recommend.FinishedSetupView(),
        public_setup_cleanup.RepairNavigationView(),
        config_history_ui.ConfigHistoryView([]),
        config_history_ui.BackupContentsView(),
        config_history_ui.ConfigVersionDetailView(1, history_plan),
        config_history_ui.SelectiveRestorePickerView(1, history_plan),
    )

    for view in views:
        close = _close_button(view)
        assert close.style == discord.ButtonStyle.danger
        assert str(close.emoji) == "✖️"


def test_setup_sources_do_not_define_gray_close_controls() -> None:
    paths = (
        ROOT / "stoney_verify/commands_ext/public_setup_recommend.py",
        ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py",
        ROOT / "stoney_verify/commands_ext/public_setup_solid.py",
        ROOT / "stoney_verify/commands_ext/public_setup_cleanup.py",
        ROOT / "stoney_verify/config_history_ui.py",
    )
    decorator_pattern = re.compile(
        r'label="Close",(?:(?!custom_id=).){0,220}?style='
        r'discord\.ButtonStyle\.secondary',
        re.DOTALL,
    )
    tuple_pattern = re.compile(
        r'\("Close",\s*"✖️",\s*discord\.ButtonStyle\.secondary'
    )

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert decorator_pattern.search(source) is None
        assert tuple_pattern.search(source) is None
