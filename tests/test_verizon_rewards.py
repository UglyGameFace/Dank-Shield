from __future__ import annotations

import unittest
from datetime import datetime, timezone

from stoney_verify.verizon_rewards.dedupe import should_alert
from stoney_verify.verizon_rewards.models import VerizonRewardConfig
from stoney_verify.verizon_rewards.parsing import (
    fingerprint_hash,
    parse_reward_text,
    parse_rewards_from_text,
)
from stoney_verify.verizon_rewards.scheduler import plan_reminder_times


class VerizonRewardParsingTests(unittest.TestCase):
    def test_parse_daily_drop_countdown(self) -> None:
        now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
        reward = parse_reward_text(
            "$25 Amazon gift card Daily Drop\nAvailable in 30m\nOpen My Verizon app > Shine",
            guild_id=123,
            source="manual",
            now=now,
        )
        self.assertIsNotNone(reward)
        assert reward is not None
        self.assertEqual(reward.type, "Daily Drop")
        self.assertIn(reward.status, {"coming_soon", "unknown"})
        self.assertEqual(reward.priority, "high")
        self.assertIsNotNone(reward.available_at)

    def test_parse_weekday_time(self) -> None:
        now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)  # Tuesday
        reward = parse_reward_text(
            "FIFA ticket access\nDrops Thursday at 2:00 PM ET",
            guild_id=123,
            source="manual",
            now=now,
        )
        self.assertIsNotNone(reward)
        assert reward is not None
        self.assertEqual(reward.type, "Daily Drop")
        self.assertIsNotNone(reward.available_at)

    def test_multi_block_parsing(self) -> None:
        rewards = parse_rewards_from_text(
            "Daily Drop Gift Card\nAvailable at 1 PM ET\n\nEpic Wins Sweepstakes\nEnds Sunday at 9 PM ET",
            guild_id=123,
            source="manual",
        )
        self.assertEqual(len(rewards), 2)


class VerizonRewardDedupeTests(unittest.TestCase):
    def test_fingerprint_is_stable_for_spacing_case(self) -> None:
        a = fingerprint_hash("  $25 Gift Card  ", "Coming Soon", "Manual")
        b = fingerprint_hash("$25 gift   card", "coming soon", "manual")
        self.assertEqual(a, b)

    def test_duplicate_unchanged_suppressed(self) -> None:
        incoming = parse_reward_text("Daily Drop Gift Card\nAvailable now", guild_id=1, source="manual")
        self.assertIsNotNone(incoming)
        assert incoming is not None
        existing = incoming
        alert, reason = should_alert(existing=existing, incoming=incoming, config=VerizonRewardConfig(guild_id=1))
        self.assertFalse(alert)
        self.assertEqual(reason, "unchanged_duplicate")

    def test_status_change_alerts(self) -> None:
        existing = parse_reward_text("Daily Drop Gift Card\nComing soon", guild_id=1, source="manual")
        incoming = parse_reward_text("Daily Drop Gift Card\nAvailable now", guild_id=1, source="manual")
        assert existing is not None and incoming is not None
        alert, reason = should_alert(existing=existing, incoming=incoming, config=VerizonRewardConfig(guild_id=1))
        self.assertTrue(alert)
        self.assertEqual(reason, "status_changed")


class VerizonReminderSchedulingTests(unittest.TestCase):
    def test_only_future_reminders_are_planned(self) -> None:
        now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
        reward = parse_reward_text("Daily Drop Gift Card\nAvailable in 10m", guild_id=1, source="manual", now=now)
        assert reward is not None
        cfg = VerizonRewardConfig(guild_id=1, reminder_offsets_minutes=(30, 10, 1), reminders_enabled=True)
        planned = plan_reminder_times(reward, cfg, now=now)
        self.assertEqual([offset for offset, _when in planned], [10, 1])

    def test_reminders_off_plans_none(self) -> None:
        now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
        reward = parse_reward_text("Daily Drop Gift Card\nAvailable in 30m", guild_id=1, source="manual", now=now)
        assert reward is not None
        cfg = VerizonRewardConfig(guild_id=1, reminders_enabled=False)
        self.assertEqual(plan_reminder_times(reward, cfg, now=now), [])


if __name__ == "__main__":
    unittest.main()
