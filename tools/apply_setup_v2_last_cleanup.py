from pathlib import Path

path = Path("stoney_verify/commands_ext/public_setup_recommend.py")
text = path.read_text(encoding="utf-8")
text = text.replace("Test / Launch", "Test & Launch")
path.write_text(text, encoding="utf-8")
compile(text, str(path), "exec")
print("PASS: removed final legacy Test / Launch wording")
