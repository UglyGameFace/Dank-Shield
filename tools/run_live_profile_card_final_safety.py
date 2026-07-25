from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "tools" / "apply_live_profile_card_final_safety.py"
source = PATCHER.read_text(encoding="utf-8")
old = r"""replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    effective_preferences,\n    get_effective_profile_settings,\n''',
    '''    display_profile_username,\n    effective_preferences,\n    get_effective_profile_settings,\n''',
)"""
new = r"""replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''    effective_preferences,\n    PLATFORM_SPECS,\n    ProfileStorageUnavailable,\n    get_effective_profile_settings,\n''',
    '''    display_profile_username,\n    effective_preferences,\n    PLATFORM_SPECS,\n    ProfileStorageUnavailable,\n    get_effective_profile_settings,\n''',
)"""
if source.count(old) != 1:
    raise RuntimeError("profile service import patch anchor changed")
PATCHER.write_text(source.replace(old, new, 1), encoding="utf-8")
runpy.run_path(str(PATCHER), run_name="__main__")
