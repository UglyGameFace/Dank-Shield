from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools/_tmp_fix_voice_verify_setup_contract.py"
SELF = Path(__file__).resolve()

text = TARGET.read_text(encoding="utf-8")
old = '''    "def _voice_overwrites(\\n",
    "def _target_label(target: Any) -> str:\\n",
'''
new = '''    "def _voice_overwrites(guild: discord.Guild, staff_role: Optional[discord.Role], control_role: Optional[discord.Role], unverified_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:\\n",
    "def _target_label(target: Any) -> str:\\n",
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(
        "Voice setup helper marker repair expected exactly 1 match, "
        f"found {count}"
    )

updated = text.replace(old, new, 1)
compile(updated, str(TARGET), "exec")
TARGET.write_text(updated, encoding="utf-8")
SELF.unlink()

print("✅ Corrected the Voice Verify helper's one-line function anchor.")
print("✅ Repaired helper compiles.")
print("✅ Temporary anchor repair helper removed.")
