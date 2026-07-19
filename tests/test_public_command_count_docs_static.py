from pathlib import Path

from stoney_verify.command_surface_contract import (
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)

DOC = Path("docs/public-production-env.md").read_text(encoding="utf-8")
COMMANDS = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_public_production_docs_match_current_command_surface():
    assert PUBLIC_GLOBAL_COMMAND_COUNT == 9
    assert PUBLIC_GLOBAL_COMMAND_NAMES == (
        "dank",
        "mod",
        "ticket",
        "tickets",
        "ticket-intake",
        "ticket-category",
        "ticket-panel",
        "verify",
        "View Dank Profile",
    )
    assert "final_global=9 final_guild=0 profile=public" in DOC
    assert "final_global=7" not in DOC
    for command_name in PUBLIC_GLOBAL_COMMAND_NAMES:
        assert command_name in DOC

    for module in (
        "public_setup_group",
        "public_mod_group",
        "public_ticket_group_clean",
        "public_tickets_group",
        "public_ticket_intake_group",
        "public_ticket_category_group",
        "public_ticket_panel_clean",
        "public_verify_group",
        "public_self_roles_group",
    ):
        assert module in COMMANDS
