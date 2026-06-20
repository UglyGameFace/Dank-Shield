from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


BAD_USER_COPY = [
    "Fix Mismatches",
    "Find & Fix Inconsistencies",
    "Fix All Inconsistencies",
    "Preview & Apply",
    "Preview / Apply",
    "Format Locks / Layouts",
    "Manage Saved Locks",
    "Current draft format",
    "current draft format",
    "Lock Current Format",
    "Back to Design Studio",
    "Advanced Tools",
    "Protection Manager",
    "Apply These Changes",
]


def test_no_old_dank_design_workflow_copy_remains():
    for text in BAD_USER_COPY:
        assert text not in SOURCE, f"Old confusing copy still present: {text}"


def test_new_workflow_terms_exist():
    required = [
        "Review Repairs",
        "Preview Server",
        "Category Editor",
        "Channel Editor",
        "Exact Format",
        "Saved Rules",
        "Protection Settings",
        "Back to Studio",
        "Apply Reviewed Changes",
    ]
    for text in required:
        assert text in SOURCE, f"Missing new workflow term: {text}"
