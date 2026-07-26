from pathlib import Path

service = Path("stoney_verify/profile_card_service.py")
service_text = service.read_text(encoding="utf-8")
old_import = "from .globals import get_supabase, reset_supabase\n"
new_import = (
    "from .globals import get_supabase, reset_supabase\n"
    "from .profile_signature_style import (\n"
    "    DEFAULT_MEMBER_PROFILE_STYLE,\n"
    "    normalize_member_profile_style,\n"
    ")\n"
)
if service_text.count(old_import) != 1:
    raise SystemExit("profile service import anchor changed")
service.write_text(service_text.replace(old_import, new_import, 1), encoding="utf-8")

patch = Path("tools/apply_ui_first_command_overhaul.py")
patch_text = patch.read_text(encoding="utf-8")
old_block = """    '''            permissions.embed_links
            and permissions.read_message_history
        )
''',
    '''            permissions.embed_links
            and permissions.read_message_history
            and permissions.attach_files
        )
''',
"""
new_block = """    '''            permissions.view_channel
            and permissions.send_messages
            and permissions.embed_links
            and permissions.read_message_history
        )
''',
    '''            permissions.view_channel
            and permissions.send_messages
            and permissions.embed_links
            and permissions.read_message_history
            and permissions.attach_files
        )
''',
"""
if patch_text.count(old_block) != 1:
    raise SystemExit("runtime permission patch anchor changed")
patch.write_text(patch_text.replace(old_block, new_block, 1), encoding="utf-8")
