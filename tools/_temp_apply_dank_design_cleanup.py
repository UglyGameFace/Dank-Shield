from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

REPO = "UglyGameFace/Dank-Shield"
BRANCH = "fix/finish-dank-shield-stability-mission"
PATH = "stoney_verify/services/server_design_majority_layout.py"


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
    old_top = '''def _top_majority(counter: Counter[Any], *, total: int) -> tuple[Any, int, bool]:
    if not counter:
        return None, 0, False
    winners = counter.most_common()
    value, count = winners[0]
    tied = len(winners) > 1 and winners[1][1] == count
    return value, count, bool(tied or count <= max(1, total // 2))
'''
    new_top = '''def _top_majority(counter: Counter[Any], *, total: int) -> tuple[Any, int, bool]:
    if not counter:
        return None, 0, False
    count = max(counter.values())
    top_values = [value for value, value_count in counter.items() if value_count == count]
    value = sorted(top_values, key=repr)[0]
    tied = len(top_values) > 1
    return value, count, bool(tied or count <= max(1, total // 2))
'''
    if old_top in text:
        text = text.replace(old_top, new_top, 1)

    old_example = '''        separator_counts[key] += 1
        separator_examples.setdefault(key, dict(sep))
        if sep.get("separator_in_name_text"):
'''
    new_example = '''        separator_counts[key] += 1
        candidate = dict(sep)
        current_example = separator_examples.get(key)
        if current_example is None or repr(sorted(candidate.items())) < repr(sorted(current_example.items())):
            separator_examples[key] = candidate
        if sep.get("separator_in_name_text"):
'''
    if old_example in text:
        text = text.replace(old_example, new_example, 1)

    marker = "\ndef _fail_repair_item(item: dict[str, Any], reason: str) -> None:\n"
    first = text.find(marker)
    second = text.find(marker, first + len(marker)) if first >= 0 else -1
    if second >= 0:
        end = text.find("\ndef annotate_plan_items(\n", second)
        if end < 0:
            raise RuntimeError("duplicate helper cleanup end marker not found")
        text = text[:second] + text[end:]

    text = text.replace(
        "for category_id, rows in groups.items()\n        if rows",
        "for category_id, rows in sorted(groups.items(), key=lambda item: item[0])\n        if rows",
        1,
    )
    text = text.replace(
        '"category_names": category_names,',
        '"category_names": dict(sorted(category_names.items(), key=lambda item: item[0])),',
        1,
    )
    text = text.replace(
        'out["__auto_detect_ephemeral_channel_ids"] = ephemeral_ids\n'
        '    out["__auto_detect_preserve_ids"] = preserve_ids\n'
        '    out["__auto_detect_category_analyses"] = dict(analyses)',
        'out["__auto_detect_ephemeral_channel_ids"] = sorted(set(ephemeral_ids))\n'
        '    out["__auto_detect_preserve_ids"] = sorted(set(preserve_ids))\n'
        '    out["__auto_detect_category_analyses"] = dict(sorted(analyses.items(), key=lambda item: str(item[0])))',
        1,
    )

    if text == original:
        print("Dank Design cleanup already applied; no update needed.")
        return text
    if text.count("def _fail_repair_item(") != 1:
        raise RuntimeError("expected exactly one _fail_repair_item after cleanup")
    if text.count("def validate_majority_repair_items(") != 1:
        raise RuntimeError("expected exactly one validate_majority_repair_items after cleanup")
    if 'out["__auto_detect_preserve_ids"] = sorted(set(preserve_ids))' not in text:
        raise RuntimeError("deterministic preserve-id ordering was not applied")
    compile(text, PATH, "exec")
    return text


def main() -> None:
    path = Path(PATH)
    source = path.read_text(encoding="utf-8")
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
            "message": "fix: make Dank Design auto-detect deterministic",
            "content": base64.b64encode(cleaned.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("updated_commit", result.get("commit", {}).get("sha"))


if __name__ == "__main__":
    main()
