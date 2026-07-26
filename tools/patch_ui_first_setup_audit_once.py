from pathlib import Path

path = Path("tools/audit_setup_safety.py")
text = path.read_text(encoding="utf-8")
old = '''                "design",
                "profiles",
                "history",
'''
new = '''                "design",
                "welcome_join",
                "profiles",
                "history",
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one AdvancedSettingsHubView anchor, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
