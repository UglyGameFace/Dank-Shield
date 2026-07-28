from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    replace_once(
        "stoney_verify/commands_ext/public_profile_cards_core.py",
        "        current = bool(preferences.get(key, True))\n",
        "        current = bool(preferences.get(key, DEFAULT_PROFILE_PREFERENCES.get(key, True)))\n",
    )
    replace_once(
        "stoney_verify/commands_ext/public_profile_cards_core.py",
        "            current = bool(dict(user_row.get(\"preferences\") or {}).get(self.preference_key, True))\n",
        "            default = DEFAULT_PROFILE_PREFERENCES.get(self.preference_key, True)\n"
        "            current = bool(dict(user_row.get(\"preferences\") or {}).get(self.preference_key, default))\n",
    )

    replace_once(
        "stoney_verify/commands_ext/public_profile_cards.py",
        '''        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button) or not child.url:
                continue
            self.add_item(
                discord.ui.Button(
                    label=str(child.label or "Profile")[:80],
                    emoji=child.emoji,
                    style=discord.ButtonStyle.link,
                    url=str(child.url),
                )
            )
''',
        '''        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button):
                continue
            if child.url:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Profile")[:80],
                        emoji=child.emoji,
                        style=discord.ButtonStyle.link,
                        url=str(child.url),
                    )
                )
            elif child.custom_id:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Username")[:80],
                        emoji=child.emoji,
                        style=child.style,
                        custom_id=str(child.custom_id),
                    )
                )
''',
    )

    test_path = ROOT / "tests" / "test_profile_platform_privacy_preview_ux.py"
    source = test_path.read_text(encoding="utf-8")
    marker = "\ndef test_every_platform_detail_uses_explicit_display_modes_and_private_control():\n"
    addition = '''

def test_server_roles_default_hidden_button_is_truthful():
    view = public_profile_cards.ProfileSettingsView(
        author_id=42,
        guild_id=7,
        user_preferences={},
        guild_settings={},
    )
    labels = {str(child.label) for child in view.children if isinstance(child, discord.ui.Button)}
    assert "Show Server Roles Everywhere" in labels
    assert "Hide Server Roles Everywhere" not in labels


def test_privacy_preview_keeps_copy_ready_username_controls():
    source_view = discord.ui.View(timeout=None)
    source_view.add_item(
        discord.ui.Button(
            label="UglyGameFace",
            custom_id="dank:profilecopy:v1:42:xbox",
            style=discord.ButtonStyle.secondary,
        )
    )
    preview = public_profile_cards._ProfilePreviewView(author_id=42, source_view=source_view)
    copied = next(
        child
        for child in preview.children
        if isinstance(child, discord.ui.Button) and child.custom_id == "dank:profilecopy:v1:42:xbox"
    )
    assert copied.label == "UglyGameFace"

'''
    if "test_server_roles_default_hidden_button_is_truthful" not in source:
        if marker not in source:
            raise RuntimeError("privacy preview test insertion point was not found")
        test_path.write_text(source.replace(marker, addition + marker, 1), encoding="utf-8")

    Path(__file__).unlink()
    print("Fixed privacy defaults and retained username controls in privacy previews.")


if __name__ == "__main__":
    main()
