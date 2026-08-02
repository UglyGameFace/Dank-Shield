from __future__ import annotations

"""Final mobile-first refinement for the canonical ``/dank setup`` test flow.

The compact setup layer already removed the oversized setup hubs. This module
keeps its feature services and session cache, but turns testing into a linear
one-button sequence: open the next enabled test, confirm it passed, and continue.
The existing select remains available only as an optional jump control.
"""

from typing import Any, Optional

import discord

from . import public_setup_compact as compact

setup = compact.setup
_PATCHED = False


def next_pending_test_key(
    state: dict[str, Any],
    confirmed: Optional[set[str] | frozenset[str]] = None,
) -> Optional[str]:
    """Return the first enabled test that has not been confirmed yet."""

    checked = set(compact._confirmed(state, confirmed))
    for key in compact.required_test_keys(state):
        if key not in checked:
            return key
    return None


def _position(state: dict[str, Any], key: str) -> tuple[int, int]:
    keys = compact.required_test_keys(state)
    try:
        return keys.index(key) + 1, len(keys)
    except ValueError:
        return 0, len(keys)


class GuidedSetupHomeView(compact.CompactSetupHomeView):
    def __init__(self, *, ready: bool = False, started: bool = False, completed: bool = False) -> None:
        super().__init__(ready=ready, started=started, completed=completed)
        if ready and not completed:
            for item in self.children:
                if getattr(item, "custom_id", "") == "dank_setup_home:continue":
                    item.label = "Start Guided Test"
                    item.emoji = "🧪"
                    break


class GuidedReviewView(compact.CompactReviewView):
    def __init__(self, *, ready: bool) -> None:
        super().__init__(ready=ready)
        if ready:
            for item in self.children:
                if getattr(item, "custom_id", "") == "dank_setup_review:next":
                    item.label = "Start Guided Test"
                    item.emoji = "🧪"
                    break


class GuidedTestView(discord.ui.View):
    def __init__(
        self,
        state: Optional[dict[str, Any]] = None,
        *,
        confirmed: Optional[set[str] | frozenset[str]] = None,
    ) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})
        self.confirmed = compact._confirmed(self.state, confirmed)
        required = set(compact.required_test_keys(self.state))
        pending = next_pending_test_key(self.state, self.confirmed)

        if required:
            jump = compact.TestAreaSelect(self.state, self.confirmed)
            jump.placeholder = "Jump to a specific test…"
            self.add_item(jump)

        if pending is not None:
            compact._add(
                self,
                "Start Next Test" if not self.confirmed else "Continue Guided Test",
                "▶️",
                discord.ButtonStyle.success,
                "dank_setup_guided_test:next",
                self._next,
                1,
            )
        elif not self.state.get("completed"):
            compact._add(
                self,
                "Finish Setup",
                "🏁",
                discord.ButtonStyle.success,
                "dank_setup_test:finish",
                self._finish,
                1,
                disabled=not required.issubset(self.confirmed),
            )

        compact._add(
            self,
            "Setup Home",
            "🏠",
            discord.ButtonStyle.secondary,
            "dank_setup_test:home",
            self._home,
            2,
        )
        compact._add(
            self,
            "Close",
            "✖️",
            discord.ButtonStyle.danger,
            "dank_setup_test:close",
            self._close,
            2,
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        key = next_pending_test_key(self.state, self.confirmed)
        if key is None:
            await render_guided_tests(interaction, self.state, self.confirmed)
            return
        await open_guided_feature_test(interaction, self.state, self.confirmed, key)

    async def _finish(self, interaction: discord.Interaction) -> None:
        await compact._finish(interaction, self.confirmed)

    async def _home(self, interaction: discord.Interaction) -> None:
        await setup._home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        compact._clear_test_session(interaction)
        await setup._close_setup(interaction)


class GuidedFeatureTestView(discord.ui.View):
    def __init__(self, state: dict[str, Any], confirmed: frozenset[str], key: str) -> None:
        super().__init__(timeout=900)
        self.state = dict(state)
        self.confirmed = frozenset(confirmed)
        self.key = key

        if key == "tickets":
            compact._add(
                self,
                "Post / Refresh Ticket Panel",
                "🎫",
                discord.ButtonStyle.primary,
                "dank_setup_test:ticket_panel",
                self._ticket_panel,
                0,
            )
            compact._add(
                self,
                "Create Test Ticket",
                "🧪",
                discord.ButtonStyle.primary,
                "dank_setup_test:create_ticket",
                self._ticket,
                0,
            )
        elif key == "simple_verify":
            compact._add(
                self,
                "Post / Refresh Verify Panel",
                "✅",
                discord.ButtonStyle.primary,
                "dank_setup_test:verify_panel",
                self._verify_panel,
                0,
            )

        compact._add(
            self,
            "Continue to Next Test" if key in confirmed else "Mark Passed & Continue",
            "✅",
            discord.ButtonStyle.success,
            "dank_setup_test:mark",
            self._pass_and_continue,
            1,
        )
        compact._add(
            self,
            "Back to Test List",
            "↩️",
            discord.ButtonStyle.secondary,
            "dank_setup_test:back",
            self._back,
            1,
        )
        compact._add(
            self,
            "Close",
            "✖️",
            discord.ButtonStyle.danger,
            "dank_setup_test:close_feature",
            self._close,
            1,
        )

    async def _ticket_panel(self, interaction: discord.Interaction) -> None:
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback

            await post_ticket_panel_callback(interaction)
        except Exception as exc:
            await compact._action_error(interaction, "Could not post the ticket panel", exc)

    async def _ticket(self, interaction: discord.Interaction) -> None:
        await setup._create_setup_test_ticket(interaction)

    async def _verify_panel(self, interaction: discord.Interaction) -> None:
        try:
            from .public_verify_basic_panel import verify_panel

            await verify_panel(interaction)
        except Exception as exc:
            await compact._action_error(interaction, "Could not post the Verify panel", exc)

    async def _pass_and_continue(self, interaction: discord.Interaction) -> None:
        if not await setup.solid._require_setup_permission(interaction):
            return
        await setup.solid._safe_defer_update(interaction)
        checked = compact._save_test_session(
            interaction,
            self.state,
            frozenset(set(self.confirmed) | {self.key}),
        )
        pending = next_pending_test_key(self.state, checked)
        if pending is None:
            await render_guided_tests(interaction, self.state, checked)
            return
        await open_guided_feature_test(interaction, self.state, checked, pending)

    async def _back(self, interaction: discord.Interaction) -> None:
        await setup.solid._safe_defer_update(interaction)
        await render_guided_tests(interaction, self.state, self.confirmed)

    async def _close(self, interaction: discord.Interaction) -> None:
        compact._clear_test_session(interaction)
        await setup._close_setup(interaction)


async def render_guided_tests(
    interaction: discord.Interaction,
    state: dict[str, Any],
    confirmed: Optional[set[str] | frozenset[str]] = None,
) -> None:
    checked = compact._save_test_session(interaction, state, confirmed)
    keys = compact.required_test_keys(state)
    pending = next_pending_test_key(state, checked)
    complete = pending is None

    lines = [
        f"{'✅' if key in checked else '⬜'} {compact.TEST_SPECS[key][0]}"
        for key in keys
    ]
    if state.get("completed"):
        next_text = "Setup is already finished. Use the button to retest any enabled feature."
    elif complete:
        next_text = "All enabled features passed. Press **Finish Setup**."
    else:
        next_text = f"Next: **{compact.TEST_SPECS[pending][0]}**. Press the green button to continue."

    embed = discord.Embed(
        title="🧪 Guided Setup Test",
        description=(
            "Test one enabled feature at a time. The green button always takes you "
            "to the next unfinished test."
        ),
        color=discord.Color.green() if complete else discord.Color.blurple(),
    )
    embed.add_field(
        name=f"Progress · {len(checked)}/{len(keys)}",
        value="\n".join(lines) or "No enabled features require testing.",
        inline=False,
    )
    embed.add_field(name="Next", value=next_text, inline=False)
    await setup.solid._edit_or_followup(
        interaction,
        embed=embed,
        view=GuidedTestView(state, confirmed=checked),
    )


async def open_guided_tests(
    interaction: discord.Interaction,
    *,
    confirmed: Optional[set[str] | frozenset[str]] = None,
) -> None:
    guild = await compact._require_guild(interaction)
    if guild is None:
        return
    await setup.solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await setup._guided_setup_target(guild)
    if target != "ready":
        await setup._open_health_check(interaction, already_deferred=True)
        return

    state = await compact._launch_state(guild)
    if confirmed is None:
        confirmed = compact._load_test_session(interaction, state)
    await render_guided_tests(interaction, state, confirmed)


async def open_guided_feature_test(
    interaction: discord.Interaction,
    state: dict[str, Any],
    confirmed: frozenset[str],
    key: str,
) -> None:
    if not await setup.solid._require_setup_permission(interaction):
        return
    if key not in compact.required_test_keys(state):
        await render_guided_tests(interaction, state, confirmed)
        return

    label, emoji, instructions = compact.TEST_SPECS[key]
    step, total = _position(state, key)
    tested = key in confirmed
    action_text = (
        "Press **Continue to Next Test** when you are ready to move on."
        if tested
        else "Confirm the real result, then press **Mark Passed & Continue**."
    )
    embed = discord.Embed(
        title=f"{emoji} Step {step} of {total} · Test {label}",
        description=f"{instructions}\n\n{action_text}",
        color=discord.Color.green() if tested else discord.Color.blurple(),
    )
    await setup.solid._edit_or_followup(
        interaction,
        embed=embed,
        view=GuidedFeatureTestView(state, confirmed, key),
    )


def apply_guided_test_patch() -> None:
    """Apply the linear test flow after the compact setup patch is installed."""

    global _PATCHED
    if _PATCHED:
        return

    compact.CompactSetupHomeView = GuidedSetupHomeView
    compact.CompactReviewView = GuidedReviewView
    compact.CompactTestView = GuidedTestView
    compact.FeatureTestView = GuidedFeatureTestView
    compact._render_tests = render_guided_tests
    compact._open_tests = open_guided_tests
    compact._open_feature_test = open_guided_feature_test

    setup.ProductSetupHomeView = GuidedSetupHomeView
    setup.SetupReviewView = GuidedReviewView
    setup.LaunchTestView = GuidedTestView
    setup._open_test_launch = open_guided_tests

    _PATCHED = True


__all__ = [
    "GuidedFeatureTestView",
    "GuidedReviewView",
    "GuidedSetupHomeView",
    "GuidedTestView",
    "apply_guided_test_patch",
    "next_pending_test_key",
    "open_guided_feature_test",
    "open_guided_tests",
    "render_guided_tests",
]
