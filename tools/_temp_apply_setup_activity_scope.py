from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

REPO = "UglyGameFace/Dank-Shield"
BRANCH = "fix/finish-dank-shield-stability-mission"
PATH = "stoney_verify/commands_ext/public_setup_group.py"


def api(method: str, url: str, *, payload: dict | None = None) -> dict:
    token = os.environ["GH_TOKEN"]
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def transform(text: str) -> str:
    original = text

    import_anchor = "from ..globals import get_supabase, now_utc\n"
    import_line = "from ..members_new.activity_scope import audit_activity_scope, format_activity_scope_problems\n"
    if import_line not in text:
        if import_anchor not in text:
            raise RuntimeError("public_setup_group import anchor missing")
        text = text.replace(import_anchor, import_anchor + import_line, 1)

    block_anchor = '''        if guild_warnings:
            warnings.append(
                "Bot is missing useful server permissions for full moderation/verification coverage: "
                + ", ".join(guild_warnings)
                + "."
            )

    # ------------------------------
    # Saved IDs / aliases
'''
    block_replacement = '''        if guild_warnings:
            warnings.append(
                "Bot is missing useful server permissions for full moderation/verification coverage: "
                + ", ".join(guild_warnings)
                + "."
            )

    # ------------------------------
    # Activity-tracking channel scope
    # ------------------------------
    activity_scope = audit_activity_scope(guild)
    if not activity_scope.bot_member_resolved:
        blockers.append(
            "Activity tracking coverage could not be verified because the bot member could not be resolved."
        )
    elif not activity_scope.complete:
        warnings.append(
            "Activity tracking coverage is incomplete: "
            f"{activity_scope.accessible_channels}/{activity_scope.total_channels} inspectable channels "
            f"({activity_scope.coverage_percent}%). Inactivity cleanup stays fail-closed until access is restored."
        )
        for problem in format_activity_scope_problems(activity_scope, limit=20):
            warnings.append(f"Activity tracking access: {problem}")
    else:
        ok.append(
            "Activity tracking has complete channel scope: "
            f"{activity_scope.accessible_channels}/{activity_scope.total_channels} inspectable channels."
        )

    # ------------------------------
    # Saved IDs / aliases
'''
    if "activity_scope = audit_activity_scope(guild)" not in text:
        if block_anchor not in text:
            raise RuntimeError("public_setup_group activity scope insertion anchor missing")
        text = text.replace(block_anchor, block_replacement, 1)

    if text == original:
        print("setup activity scope already applied")
        return text

    required = (
        "from ..members_new.activity_scope import audit_activity_scope, format_activity_scope_problems",
        "activity_scope = audit_activity_scope(guild)",
        "Activity tracking coverage is incomplete:",
        "Inactivity cleanup stays fail-closed until access is restored.",
        "Activity tracking access: {problem}",
    )
    for marker in required:
        if marker not in text:
            raise RuntimeError(f"setup activity scope marker missing: {marker}")
    compile(text, PATH, "exec")
    return text


def main() -> None:
    source = Path(PATH).read_text(encoding="utf-8")
    cleaned = transform(source)
    if cleaned == source:
        return
    query = urllib.parse.urlencode({"ref": BRANCH})
    endpoint = f"https://api.github.com/repos/{REPO}/contents/{PATH}"
    current = api("GET", f"{endpoint}?{query}")
    result = api(
        "PUT",
        endpoint,
        payload={
            "message": "fix: surface activity permission gaps in Setup Check",
            "content": base64.b64encode(cleaned.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("updated_commit", result.get("commit", {}).get("sha"))


if __name__ == "__main__":
    main()
