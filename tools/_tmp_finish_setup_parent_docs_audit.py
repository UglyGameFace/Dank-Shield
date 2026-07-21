from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).resolve()

PERMISSION = ROOT / "stoney_verify/setup_permission_repair_services.py"
ACTIVITY = ROOT / "stoney_verify/setup_activity_access.py"
MODLOG = ROOT / "stoney_verify/modlog_tracking_service.py"
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
BOT_ACCESS_TEST = ROOT / "tests/test_setup_bot_access_feature.py"
PERMISSION_TEST = ROOT / "tests/test_setup_permission_repair_native_route.py"
MODLOG_TEST = ROOT / "tests/test_modlog_tracking_service_native.py"
README = ROOT / "README.md"
PROD_DOC = ROOT / "docs/public-production-env.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def replace_required(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count < 1:
        raise RuntimeError(f"{label}: expected at least 1 match, found 0")
    return text.replace(old, new)


def compile_text(path: Path, text: str) -> None:
    compile(text, str(path), "exec")


# ===========================================================================
# 1. Permission Repair: preserve the logical setup parent.
# ===========================================================================
permission = PERMISSION.read_text(encoding="utf-8")

permission = replace_once(
    permission,
    """`/dank setup -> Safety & Repair.
It extends the older guard implementation with exact-name discovery and clearer
fix boundaries, without blindly overwriting unrelated server channels.
""",
    """`/dank setup -> Manage Setup -> All Features & Settings -> Security & SpamGuard`.
It extends the older guard implementation with exact-name discovery and clearer
fix boundaries, without blindly overwriting unrelated server channels.
""",
    "permission repair module route doc",
)

permission = replace_required(
    permission,
    "Save the intended channel in Core Setup → Use Existing Roles/Channels.",
    "Save the intended channel in Setup Plan & Server Items → Choose Roles & Channels.",
    "permission exact-name guidance",
)

permission = replace_required(
    permission,
    "Run Core Setup first, or use existing-server mapping to save the intended roles/channels.",
    "Use Setup Plan & Server Items → Choose Roles & Channels to save the intended roles/channels.",
    "permission no-target guidance",
)

permission = replace_required(
    permission,
    "map it in Core Setup first.",
    "map it in Setup Plan & Server Items → Choose Roles & Channels.",
    "permission fix-boundary guidance",
)

permission = replace_required(
    permission,
    "**Test / Launch** is available.",
    "**Test Your Setup** is available.",
    "permission readiness wording",
)

old_parent_helper = '''async def _back_to_advanced_options(
    interaction: discord.Interaction,
) -> None:
    from stoney_verify.commands_ext import (
        public_setup_recommend as recommend,
    )

    await recommend._open_manage_setup(interaction)
'''
new_parent_helper = '''async def _back_to_parent(
    interaction: discord.Interaction,
    parent: str,
) -> None:
    from stoney_verify.commands_ext import (
        public_setup_recommend as recommend,
    )

    clean_parent = str(parent or "security").strip().lower()
    if clean_parent == "logs":
        await recommend._open_advanced_logs_activity(interaction)
        return

    await recommend._open_advanced_security(interaction)
'''
permission = replace_once(
    permission,
    old_parent_helper,
    new_parent_helper,
    "permission parent router",
)

permission = replace_once(
    permission,
    '''class PermissionRepairPreviewView(discord.ui.View):
    """Canonical preview controls for Advanced Permission Repair."""

    def __init__(self) -> None:
        super().__init__(timeout=900)
''',
    '''class PermissionRepairPreviewView(discord.ui.View):
    """Canonical preview controls that remember the setup parent."""

    def __init__(self, *, parent: str = "security") -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "security").strip().lower()
''',
    "permission preview parent state",
)

permission = replace_once(
    permission,
    "        await apply_permission_repair(interaction)",
    "        await apply_permission_repair(interaction, parent=self.parent)",
    "permission apply preserves parent",
)
permission = replace_once(
    permission,
    "        await open_permission_repair(interaction)",
    "        await open_permission_repair(interaction, parent=self.parent)",
    "permission preview preserves parent",
)

permission = replace_once(
    permission,
    '''        label="Back to Advanced",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission:advanced",
        row=0,
    )
    async def back_to_advanced(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _back_to_advanced_options(interaction)
''',
    '''        label="Back",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission:back",
        row=0,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _back_to_parent(interaction, self.parent)
''',
    "permission preview back route",
)

permission = replace_once(
    permission,
    '''class PermissionRepairResultView(discord.ui.View):
    """Canonical post-repair controls."""

    def __init__(self) -> None:
        super().__init__(timeout=900)
''',
    '''class PermissionRepairResultView(discord.ui.View):
    """Canonical post-repair controls that remember the setup parent."""

    def __init__(self, *, parent: str = "security") -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "security").strip().lower()
''',
    "permission result parent state",
)

# The result view has the second Preview Again and Back to Advanced occurrences.
permission = replace_once(
    permission,
    "        await open_permission_repair(interaction)",
    "        await open_permission_repair(interaction, parent=self.parent)",
    "permission result preview preserves parent",
)
permission = replace_once(
    permission,
    '''        label="Back to Advanced",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission_done:advanced",
        row=0,
    )
    async def back_to_advanced(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _back_to_advanced_options(interaction)
''',
    '''        label="Back",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission_done:back",
        row=0,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _back_to_parent(interaction, self.parent)
''',
    "permission result back route",
)

permission = replace_once(
    permission,
    "async def open_permission_repair(interaction: discord.Interaction) -> None:",
    '''async def open_permission_repair(
    interaction: discord.Interaction,
    *,
    parent: str = "security",
) -> None:''',
    "permission open parent signature",
)
permission = replace_once(
    permission,
    "        view=PermissionRepairPreviewView(),",
    "        view=PermissionRepairPreviewView(parent=parent),",
    "permission preview parent forwarding",
)

permission = replace_once(
    permission,
    "async def apply_permission_repair(interaction: discord.Interaction) -> None:",
    '''async def apply_permission_repair(
    interaction: discord.Interaction,
    *,
    parent: str = "security",
) -> None:''',
    "permission apply parent signature",
)
permission = replace_once(
    permission,
    "        view=PermissionRepairResultView(),",
    "        view=PermissionRepairResultView(parent=parent),",
    "permission result parent forwarding",
)


# ===========================================================================
# 2. Activity Access: preserve Security vs Logs & Activity parent.
# ===========================================================================
activity = ACTIVITY.read_text(encoding="utf-8")

activity = replace_once(
    activity,
    '''`/dank setup -> Other Settings -> Logs & Safety -> Check Bot Access` uses the
same shared activity-scope audit as inactivity safety diagnostics. This module
never edits Discord permissions. Owners can deliberately open the existing
preview-first Fix Channel Permissions tool if they want to repair access.
''',
    '''`/dank setup -> Manage Setup -> All Features & Settings` opens this check from
Security & SpamGuard or Logs & Activity. It uses the same shared activity-scope
audit as inactivity safety diagnostics and never edits Discord permissions.
Owners can deliberately open the preview-first Fix Channel Permissions tool.
''',
    "activity module route doc",
)

activity = replace_once(
    activity,
    '''class ActivityAccessView(discord.ui.View):
    def __init__(self, *, needs_repair: bool) -> None:
        super().__init__(timeout=900)
        self.fix_permissions.disabled = not bool(needs_repair)
''',
    '''class ActivityAccessView(discord.ui.View):
    def __init__(
        self,
        *,
        needs_repair: bool,
        parent: str = "logs",
    ) -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "logs").strip().lower()
        self.fix_permissions.disabled = not bool(needs_repair)
''',
    "activity parent state",
)
activity = replace_once(
    activity,
    "        await open_activity_access_check(interaction)",
    "        await open_activity_access_check(interaction, parent=self.parent)",
    "activity recheck preserves parent",
)
activity = replace_once(
    activity,
    "        await setup_permission_repair_services.open_permission_repair(interaction)",
    '''        await setup_permission_repair_services.open_permission_repair(
            interaction,
            parent=self.parent,
        )''',
    "activity repair preserves parent",
)
activity = replace_once(
    activity,
    '''        label="Back to Logs & Safety",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_bot_access:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        await recommend._open_advanced_monitoring_repair(interaction)
''',
    '''        label="Back",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_bot_access:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.commands_ext import public_setup_recommend as recommend

        if self.parent == "security":
            await recommend._open_advanced_security(interaction)
            return

        await recommend._open_advanced_logs_activity(interaction)
''',
    "activity back route",
)
activity = replace_once(
    activity,
    '        label="Back Home",',
    '        label="Setup Home",',
    "activity setup home label",
)
activity = replace_once(
    activity,
    "async def open_activity_access_check(interaction: discord.Interaction) -> None:",
    '''async def open_activity_access_check(
    interaction: discord.Interaction,
    *,
    parent: str = "logs",
) -> None:''',
    "activity open parent signature",
)
activity = replace_once(
    activity,
    "        view=ActivityAccessView(needs_repair=not report.complete),",
    "        view=ActivityAccessView(needs_repair=not report.complete, parent=parent),",
    "activity parent forwarding",
)


# ===========================================================================
# 3. Canonical setup wrapper forwards the parent to Activity Access.
# ===========================================================================
recommend = RECOMMEND.read_text(encoding="utf-8")

recommend = replace_once(
    recommend,
    '''async def _open_bot_access_check(
    interaction: discord.Interaction,
) -> None:
    """Open the read-only activity coverage access check."""

    if not await solid._require_setup_permission(interaction):
        return

    from stoney_verify import setup_activity_access

    await setup_activity_access.open_activity_access_check(interaction)
''',
    '''async def _open_bot_access_check(
    interaction: discord.Interaction,
    *,
    parent: str = "logs",
) -> None:
    """Open the read-only activity coverage access check."""

    if not await solid._require_setup_permission(interaction):
        return

    from stoney_verify import setup_activity_access

    await setup_activity_access.open_activity_access_check(
        interaction,
        parent=parent,
    )
''',
    "setup access wrapper parent",
)

bot_access_call = "        await _open_bot_access_check(interaction)"
if recommend.count(bot_access_call) != 2:
    raise RuntimeError(
        "setup access parent calls: expected exactly 2 advanced call sites, "
        f"found {recommend.count(bot_access_call)}"
    )
recommend = recommend.replace(
    bot_access_call,
    '        await _open_bot_access_check(interaction, parent="security")',
    1,
)
recommend = recommend.replace(
    bot_access_call,
    '        await _open_bot_access_check(interaction, parent="logs")',
    1,
)

# Security opens Permission Repair directly, so make its parent explicit too.
recommend = replace_once(
    recommend,
    "    await setup_permission_repair_services.open_permission_repair(\n        interaction\n    )",
    '''    await setup_permission_repair_services.open_permission_repair(
        interaction,
        parent="security",
    )''',
    "security permission parent",
)


# ===========================================================================
# 4. Modlog Tracking goes Back to its real parent: Logs & Activity.
# ===========================================================================
modlog = MODLOG.read_text(encoding="utf-8")
modlog = replace_once(
    modlog,
    '''        label="Back to Advanced",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=4,
''',
    '''        label="Back",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=4,
''',
    "modlog back label",
)
modlog = replace_once(
    modlog,
    '''        await recommend._open_manage_setup(
            interaction
        )''',
    '''        await recommend._open_advanced_logs_activity(
            interaction
        )''',
    "modlog back route",
)


# ===========================================================================
# 5. Behavioral tests for parent preservation.
# ===========================================================================
bot_tests = BOT_ACCESS_TEST.read_text(encoding="utf-8")

bot_tests = replace_once(
    bot_tests,
    '''    async def open_check(interaction) -> None:
        calls.append(interaction)
''',
    '''    async def open_check(interaction, *, parent="logs") -> None:
        calls.append((interaction, parent))
''',
    "bot access wrapper stub",
)
bot_tests = replace_once(
    bot_tests,
    "    assert calls == [interaction]",
    '    assert calls == [(interaction, "logs")]\n',
    "bot access wrapper expectation",
)

bot_tests = replace_once(
    bot_tests,
    '''    async def open_repair(interaction) -> None:
        calls.append(interaction)
''',
    '''    async def open_repair(interaction, *, parent="security") -> None:
        calls.append((interaction, parent))
''',
    "activity repair stub",
)
bot_tests = replace_once(
    bot_tests,
    "    assert calls == [interaction]",
    '    assert calls == [(interaction, "logs")]\n',
    "activity repair expectation",
)

bot_tests = replace_once(
    bot_tests,
    '''    assert {str(getattr(child, "label", "") or "") for child in view.children} == {
        "Check Again",
        "Fix Channel Permissions",
        "Back to Logs & Safety",
        "Back Home",
    }
''',
    '''    assert {str(getattr(child, "label", "") or "") for child in view.children} == {
        "Check Again",
        "Fix Channel Permissions",
        "Back",
        "Setup Home",
    }
''',
    "activity labels expectation",
)

activity_parent_tests = '''\n\ndef test_activity_access_back_preserves_security_or_logs_parent(monkeypatch) -> None:\n    events: list[str] = []\n\n    async def security(_interaction) -> None:\n        events.append("security")\n\n    async def logs(_interaction) -> None:\n        events.append("logs")\n\n    monkeypatch.setattr(recommend, "_open_advanced_security", security)\n    monkeypatch.setattr(recommend, "_open_advanced_logs_activity", logs)\n\n    security_view = setup_activity_access.ActivityAccessView(\n        needs_repair=False,\n        parent="security",\n    )\n    asyncio.run(_button(security_view, "Back").callback(object()))\n\n    logs_view = setup_activity_access.ActivityAccessView(\n        needs_repair=False,\n        parent="logs",\n    )\n    asyncio.run(_button(logs_view, "Back").callback(object()))\n\n    assert events == ["security", "logs"]\n'''
bot_tests += activity_parent_tests

permission_tests = PERMISSION_TEST.read_text(encoding="utf-8")
permission_tests = replace_once(
    permission_tests,
    '''    async def open_repair(interaction: Any) -> None:
        events.append(interaction)
''',
    '''    async def open_repair(
        interaction: Any,
        *,
        parent: str = "security",
    ) -> None:
        events.append((interaction, parent))
''',
    "permission route stub",
)
permission_tests = replace_once(
    permission_tests,
    "    assert events == [interaction]",
    '    assert events == [(interaction, "security")]\n',
    "permission route parent expectation",
)

permission_parent_tests = '''\n\ndef test_permission_repair_back_preserves_setup_parent(monkeypatch) -> None:\n    events: list[str] = []\n\n    async def security(_interaction: Any) -> None:\n        events.append("security")\n\n    async def logs(_interaction: Any) -> None:\n        events.append("logs")\n\n    monkeypatch.setattr(recommend, "_open_advanced_security", security)\n    monkeypatch.setattr(recommend, "_open_advanced_logs_activity", logs)\n\n    security_view = setup_permission_repair_services.PermissionRepairPreviewView(\n        parent="security"\n    )\n    run_target = button(security_view, "Back").callback(SimpleNamespace())\n    asyncio.run(run_target)\n\n    logs_view = setup_permission_repair_services.PermissionRepairResultView(\n        parent="logs"\n    )\n    run_target = button(logs_view, "Back").callback(SimpleNamespace())\n    asyncio.run(run_target)\n\n    assert events == ["security", "logs"]\n\n\ndef test_permission_repair_preview_actions_keep_parent(monkeypatch) -> None:\n    events: list[tuple[str, str]] = []\n\n    async def apply(_interaction: Any, *, parent: str = "security") -> None:\n        events.append(("apply", parent))\n\n    async def preview(_interaction: Any, *, parent: str = "security") -> None:\n        events.append(("preview", parent))\n\n    monkeypatch.setattr(setup_permission_repair_services, "apply_permission_repair", apply)\n    monkeypatch.setattr(setup_permission_repair_services, "open_permission_repair", preview)\n\n    view = setup_permission_repair_services.PermissionRepairPreviewView(parent="logs")\n    asyncio.run(button(view, "Apply Safe Fixes").callback(SimpleNamespace()))\n    asyncio.run(button(view, "Preview Again").callback(SimpleNamespace()))\n\n    assert events == [("apply", "logs"), ("preview", "logs")]\n\n\ndef test_permission_repair_readiness_copy_uses_current_setup_language(monkeypatch) -> None:\n    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy\n\n    monkeypatch.setattr(legacy, "_result_embed", lambda _result: discord.Embed())\n    monkeypatch.setattr(\n        legacy,\n        "_line_list",\n        lambda items, *, empty: "\\n".join(items) or empty,\n    )\n\n    deep = SimpleNamespace(blockers=[], warnings=[], ok=[])\n    embed = setup_permission_repair_services.result_embed({}, deep_audit=deep)\n    rendered = "\\n".join(\n        [str(embed.description or "")]\n        + [str(field.value) for field in embed.fields]\n    )\n\n    assert "Test Your Setup" in rendered\n    assert "Test / Launch" not in rendered\n    assert "Setup Plan & Server Items" in rendered\n'''
permission_tests += permission_parent_tests

modlog_tests = MODLOG_TEST.read_text(encoding="utf-8")
modlog_tests = replace_once(
    modlog_tests,
    '        "Back to Advanced",',
    '        "Back",',
    "modlog control label expectation",
)
modlog_tests = replace_once(
    modlog_tests,
    "def test_back_button_returns_to_advanced_options(",
    "def test_back_button_returns_to_logs_activity(",
    "modlog test name",
)
modlog_tests = replace_once(
    modlog_tests,
    '''    async def open_advanced(
        interaction: Any,
    ) -> None:
        events.append("advanced")
''',
    '''    async def open_logs(
        interaction: Any,
    ) -> None:
        events.append("logs")
''',
    "modlog back stub",
)
modlog_tests = replace_once(
    modlog_tests,
    '''        "_open_manage_setup",
        open_advanced,
''',
    '''        "_open_advanced_logs_activity",
        open_logs,
''',
    "modlog monkeypatch route",
)
modlog_tests = replace_once(
    modlog_tests,
    '        "Back to Advanced",',
    '        "Back",',
    "modlog back lookup",
)
modlog_tests = replace_once(
    modlog_tests,
    '    assert events == ["advanced"]',
    '    assert events == ["logs"]',
    "modlog back event",
)


# ===========================================================================
# 6. README and public-production docs: document the actual current UX.
# ===========================================================================
readme = README.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    '''Start Setup
Choose what Dank Shield should do
Set Up This Step
Automatic Setup Check
Fix Next Problem or Test & Launch
''',
    '''Start Setup
Choose a setup plan
Set Up This Step (or Continue Setup for Choose Core Features)
Automatic Setup Check
Test Your Setup
Finish Setup
''',
    "README quick setup flow",
)
readme = replace_once(
    readme,
    '''Use **More Options** only for secondary tools such as changing setup type, optional settings, manual setup checks, permission repair, or starting over.''',
    '''Use **Manage Setup** for secondary tools such as changing the setup plan, optional settings, Review Setup, permission repair, backups, Server Design, or starting over.''',
    "README manage setup wording",
)
readme = replace_once(
    readme,
    '''/dank setup → Existing Server → Ticket Basics''',
    '''/dank setup → Manage Setup → All Features & Settings → Setup Plan & Server Items → Choose Roles & Channels''',
    "README ticket category route",
)
readme = replace_required(
    readme,
    '''/dank setup → Advanced Setup → Ticket Menu Options''',
    '''/dank setup → Manage Setup → All Features & Settings → Tickets → Ticket Choices''',
    "README ticket choices route",
)
readme = replace_once(
    readme,
    '''Fix the first blocker shown, then run Health Check again. Do not chase warnings before blockers.''',
    '''Fix the first blocker shown, then use **Continue Setup** or **Review Setup** to check again. Do not chase optional warnings before required blockers.''',
    "README health retry wording",
)
readme = replace_once(
    readme,
    '''Invite bot
/dank setup
Fresh Server
Create Missing Defaults Now
Health Check
Advanced Setup → Ticket Menu Options
Create Recommended Ticket Menu if needed
/ticket-panel post
Open ticket
Close ticket
Reopen ticket
Delete ticket
Check transcript
Check modlog
Restart bot
Confirm /dank setup still works
''',
    '''Invite bot
/dank setup
Start Setup
Choose the setup plan you want to test
Use Set Up This Step until Setup Check reports ready
Test Your Setup
Finish Setup
Manage Setup → All Features & Settings → Tickets → Ticket Choices
Confirm the intended ticket choices
/ticket-panel post
Open ticket
Close ticket
Reopen ticket
Delete ticket
Check transcript
Check modlog
Restart bot
Confirm /dank setup shows the finished Setup Summary
''',
    "README release test flow",
)

prod_doc = PROD_DOC.read_text(encoding="utf-8")
prod_doc = replace_once(
    prod_doc,
    '''3. Choose what Dank Shield should do and follow **Set Up This Step** until Setup Check runs automatically.
4. Fix any reported problem, then use **Test & Launch**.
5. SpamGuard defaults to ON for new/missing settings rows unless an owner explicitly turns it off.
''',
    '''3. Choose a setup plan and follow **Set Up This Step** (or **Continue Setup** for Choose Core Features) until Setup Check runs automatically.
4. Fix any required blocker, then use **Test Your Setup**. When the enabled features work, press **Finish Setup**.
5. SpamGuard defaults to ON for new/missing settings rows unless an owner explicitly turns it off.
''',
    "production setup flow docs",
)


for path, text in (
    (PERMISSION, permission),
    (ACTIVITY, activity),
    (MODLOG, modlog),
    (RECOMMEND, recommend),
    (BOT_ACCESS_TEST, bot_tests),
    (PERMISSION_TEST, permission_tests),
    (MODLOG_TEST, modlog_tests),
):
    compile_text(path, text)
    path.write_text(text, encoding="utf-8")

README.write_text(readme, encoding="utf-8")
PROD_DOC.write_text(prod_doc, encoding="utf-8")

# Temporary staging helper must never remain in the final branch.
HELPER.unlink()

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Parent-aware setup service navigation patch applied.")
print("✅ Activity Access preserves Security vs Logs & Activity context.")
print("✅ Permission Repair preserves its caller context through preview/apply/result.")
print("✅ Modlog Tracking returns to Logs & Activity.")
print("✅ README and production setup docs now match the current UX.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
