from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

REPO = "UglyGameFace/Dank-Shield"
BRANCH = "fix/finish-dank-shield-stability-mission"
PATH = "stoney_verify/members_new/sync_service.py"


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
    import_anchor = "from ..globals import *  # noqa: F401,F403\n"
    import_line = "from .membership_authority import collect_membership_snapshot, departure_reconciliation_allowed\n"
    if import_line not in text:
        if import_anchor not in text:
            raise RuntimeError("sync_service import anchor missing")
        text = text.replace(import_anchor, import_anchor + import_line, 1)

    full_start = text.index("async def run_full_member_sync_for_guild(")
    departed_start = text.index("async def run_departed_reconciliation_for_guild(", full_start)
    all_start = text.index("async def run_full_member_sync_for_all_guilds(", departed_start)

    full_function = '''async def run_full_member_sync_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "active_members_synced": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        members = list(snapshot.members)
        summary["membership_source"] = snapshot.source
        summary["membership_authoritative"] = bool(snapshot.authoritative)
        if snapshot.error:
            summary["member_fetch_error"] = snapshot.error
        summary["checked"] = len(members)

        active_ids: set[str] = set()
        for idx, member in enumerate(members, start=1):
            try:
                member_id = int(getattr(member, "id", 0) or 0)
                if member_id <= 0:
                    summary["errors"] += 1
                    continue
                active_ids.add(str(member_id))
                await sync_member_to_supabase(member, in_guild=True)
                summary["active_members_synced"] += 1

                if idx % 10 == 0:
                    await asyncio.sleep(0)
            except Exception:
                summary["errors"] += 1
                continue

        if not departure_reconciliation_allowed(snapshot):
            summary["errors"] += 1
            summary["departure_reconciliation_skipped"] = True
            summary["departure_skip_reason"] = "authoritative_member_fetch_failed"
            print(
                "⚠️ Member departure reconciliation skipped during full sync: authoritative Discord member fetch failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        try:
            summary["marked_departed"] = await _bulk_mark_departed_members_async(
                sb,
                str(guild.id),
                active_ids,
            )
        except Exception as e:
            summary["errors"] += 1
            summary["departure_reconciliation_error"] = f"{type(e).__name__}: {str(e)[:350]}"

        return summary

    except Exception as e:
        summary["error"] = repr(e)
        summary["errors"] = max(1, int(summary.get("errors") or 0))
        print("⚠️ members_new.sync_service.run_full_member_sync_for_guild error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return summary


'''

    departed_function = '''async def run_departed_reconciliation_for_guild(guild: discord.Guild) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "guild_id": str(getattr(guild, "id", "")),
        "checked": 0,
        "marked_departed": 0,
        "errors": 0,
    }

    try:
        sb = get_supabase()
        if not sb:
            summary["error"] = "supabase_unavailable"
            return summary

        snapshot = await collect_membership_snapshot(guild)
        active_ids = {str(user_id) for user_id in snapshot.active_user_ids}
        summary["membership_source"] = snapshot.source
        summary["membership_authoritative"] = bool(snapshot.authoritative)
        if snapshot.error:
            summary["member_fetch_error"] = snapshot.error
        summary["checked"] = len(active_ids)

        if not departure_reconciliation_allowed(snapshot):
            summary["errors"] = 1
            summary["departure_reconciliation_skipped"] = True
            summary["departure_skip_reason"] = "authoritative_member_fetch_failed"
            print(
                "⚠️ Departed-member reconciliation skipped: authoritative Discord member fetch failed; "
                f"guild={getattr(guild, 'id', 'unknown')} cached_positive_members={len(active_ids)} "
                f"error={snapshot.error or 'unknown'}"
            )
            return summary

        summary["marked_departed"] = await _bulk_mark_departed_members_async(
            sb,
            str(guild.id),
            active_ids,
        )
        return summary

    except Exception as e:
        summary["error"] = repr(e)
        summary["errors"] = 1
        print("⚠️ members_new.sync_service.run_departed_reconciliation_for_guild error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return summary


'''

    text = text[:full_start] + full_function + departed_function + text[all_start:]

    if text == original:
        print("native member reconciliation already applied")
        return text
    required = (
        "from .membership_authority import collect_membership_snapshot, departure_reconciliation_allowed",
        "snapshot = await collect_membership_snapshot(guild)",
        'summary["departure_reconciliation_skipped"] = True',
        'summary["departure_skip_reason"] = "authoritative_member_fetch_failed"',
        "if not departure_reconciliation_allowed(snapshot):",
    )
    for marker in required:
        if marker not in text:
            raise RuntimeError(f"native reconciliation marker missing: {marker}")
    if text.count("async def run_full_member_sync_for_guild(") != 1:
        raise RuntimeError("full sync function count changed unexpectedly")
    if text.count("async def run_departed_reconciliation_for_guild(") != 1:
        raise RuntimeError("departed reconciliation function count changed unexpectedly")
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
            "message": "fix: make member reconciliation natively authoritative",
            "content": base64.b64encode(cleaned.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("updated_commit", result.get("commit", {}).get("sha"))


if __name__ == "__main__":
    main()
