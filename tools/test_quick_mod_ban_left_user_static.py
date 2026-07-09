from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_quick_mod_ban_has_left_user_fallback() -> None:
    assert "discord.Object(id=int(self.target_user_id))" in MODLOG
    assert "await interaction.guild.ban(self._ban_object()" in MODLOG
    assert "They had already left or been kicked" in MODLOG


def test_quick_mod_member_only_actions_still_require_current_member() -> None:
    assert "Use Ban to ban by user ID" in MODLOG
    assert "target.kick(reason=reason)" in MODLOG
    assert "target.timeout(until, reason=reason)" in MODLOG


def test_quick_mod_ban_by_id_checks_permissions() -> None:
    assert "_bot_can_ban_by_id" in MODLOG
    assert "Bot needs **Ban Members** to ban a user who already left" in MODLOG
    assert "You cannot ban yourself" in MODLOG
    assert "Bot cannot ban the server owner" in MODLOG


if __name__ == "__main__":
    for test in (
        test_quick_mod_ban_has_left_user_fallback,
        test_quick_mod_member_only_actions_still_require_current_member,
        test_quick_mod_ban_by_id_checks_permissions,
    ):
        test()
        print(f"PASS {test.__name__}")
