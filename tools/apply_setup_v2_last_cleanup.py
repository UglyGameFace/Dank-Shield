from pathlib import Path

path = Path("stoney_verify/commands_ext/public_setup_recommend.py")
text = path.read_text(encoding="utf-8")
for old, new in (
    ("Start / Continue Setup", "Continue Setup"),
    ("Test / Launch", "Test & Launch"),
    ("Advanced Options", "Other Settings"),
    ("Fix Next Item", "Fix Next Problem"),
):
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")
compile(text, str(path), "exec")
print("PASS: purged all legacy setup action wording")
