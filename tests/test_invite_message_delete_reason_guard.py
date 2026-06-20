from pathlib import Path


FILES = [
    Path("stoney_verify/startup_guards/discord_invite_blocker_runtime_guard.py"),
    Path("stoney_verify/startup_guards/spam_guard_invite_hard_block.py"),
]


def test_invite_runtime_message_delete_does_not_pass_reason_keyword():
    for path in FILES:
        source = path.read_text()
        assert ".delete(reason=" not in source, f"{path} passes unsupported reason= to message.delete"


def test_invite_runtime_still_deletes_messages():
    combined = "\n".join(path.read_text() for path in FILES)
    assert "await effective_message.delete()" in combined
    assert "await message.delete()" in combined
