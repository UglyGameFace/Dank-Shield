from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

REPO = "UglyGameFace/Dank-Shield"
BRANCH = "fix/finish-dank-shield-stability-mission"
PATH = "stoney_verify/members_new/activity_service.py"


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

    import_anchor = '''from stoney_verify.members_new.activity_tracker import (
    get_activity_coverage_status,
)
'''
    import_replacement = import_anchor + '''from stoney_verify.members_new.activity_scope import (
    audit_activity_scope,
    format_activity_scope_problems,
)
'''
    if "from stoney_verify.members_new.activity_scope import" not in text:
        if import_anchor not in text:
            raise RuntimeError("activity scope import anchor missing")
        text = text.replace(import_anchor, import_replacement, 1)

    dataclass_anchor = '''    coverage_required_days: int = 0
    coverage_storage_ready: bool = False
'''
    dataclass_replacement = dataclass_anchor + '''    activity_scope_total_channels: int = 0
    activity_scope_accessible_channels: int = 0
    activity_scope_coverage_percent: int = 0
    activity_scope_complete: bool = False
    activity_scope_problems: list[str] = field(default_factory=list)
'''
    if "activity_scope_coverage_percent: int = 0" not in text:
        if dataclass_anchor not in text:
            raise RuntimeError("inactive report dataclass anchor missing")
        text = text.replace(dataclass_anchor, dataclass_replacement, 1)

    old_coverage = '''    @property
    def data_coverage_percent(self) -> int:
        try:
            if self.data_sources_attempted <= 0:
                return 0
            return max(0, min(100, round((self.data_sources_read / self.data_sources_attempted) * 100)))
        except Exception:
            return 0

    @property
    def data_confidence_label(self) -> str:
        if self.data_sources_attempted > 0 and self.data_sources_read >= self.data_sources_attempted:
            return "Good"
'''
    new_coverage = '''    @property
    def data_coverage_percent(self) -> int:
        try:
            source_percent = 0
            if self.data_sources_attempted > 0:
                source_percent = max(0, min(100, round((self.data_sources_read / self.data_sources_attempted) * 100)))
            scope_percent = max(0, min(100, int(self.activity_scope_coverage_percent or 0)))
            if self.activity_scope_total_channels > 0 or not self.activity_scope_complete:
                return min(source_percent, scope_percent)
            return source_percent
        except Exception:
            return 0

    @property
    def data_confidence_label(self) -> str:
        if not self.activity_scope_complete:
            return "Incomplete channel scope"
        if self.data_sources_attempted > 0 and self.data_sources_read >= self.data_sources_attempted:
            return "Good"
'''
    if old_coverage in text:
        text = text.replace(old_coverage, new_coverage, 1)

    scan_anchor = '''    now = now_utc()
    protected_role_ids, verified_resident_role_ids = await _load_role_sets(guild)
    members = list(getattr(guild, "members", []) or [])
'''
    scan_replacement = scan_anchor + '''    scope_report = audit_activity_scope(guild)
    scope_problem_lines = format_activity_scope_problems(scope_report, limit=20)
'''
    if "scope_report = audit_activity_scope(guild)" not in text:
        if scan_anchor not in text:
            raise RuntimeError("scan scope anchor missing")
        text = text.replace(scan_anchor, scan_replacement, 1)

    early_anchor = '''            configuration_errors=[
                "verified_role_id/resident_role_id missing or invalid"
            ],
        )
'''
    early_replacement = '''            configuration_errors=[
                "verified_role_id/resident_role_id missing or invalid"
            ],
            activity_scope_total_channels=scope_report.total_channels,
            activity_scope_accessible_channels=scope_report.accessible_channels,
            activity_scope_coverage_percent=scope_report.coverage_percent,
            activity_scope_complete=scope_report.complete,
            activity_scope_problems=scope_problem_lines,
        )
'''
    if "activity_scope_total_channels=scope_report.total_channels" not in text:
        if early_anchor not in text:
            raise RuntimeError("early report scope anchor missing")
        text = text.replace(early_anchor, early_replacement, 1)

    coverage_anchor = '''    data_warnings.append(coverage.reason)

    candidates: list[InactiveMemberCandidate] = []
'''
    coverage_replacement = '''    data_warnings.append(coverage.reason)
    if not scope_report.complete:
        data_warnings.append(scope_report.summary(limit=12))

    candidates: list[InactiveMemberCandidate] = []
'''
    if "data_warnings.append(scope_report.summary(limit=12))" not in text:
        if coverage_anchor not in text:
            raise RuntimeError("coverage warning anchor missing")
        text = text.replace(coverage_anchor, coverage_replacement, 1)

    action_anchor = '''    report_actionable = bool(coverage.actionable)
    actionability_reason = str(coverage.reason)
'''
    action_replacement = '''    report_actionable = bool(coverage.actionable and scope_report.complete)
    actionability_reason = str(coverage.reason)
    if not scope_report.complete:
        scope_reason = scope_report.summary(limit=8)
        actionability_reason = (
            f"{actionability_reason} {scope_reason}".strip()
            if actionability_reason
            else scope_reason
        )
'''
    if action_anchor in text:
        text = text.replace(action_anchor, action_replacement, 1)

    final_anchor = '''        coverage_required_days=coverage.required_days,
        coverage_storage_ready=coverage.storage_ready,
    )
'''
    final_replacement = '''        coverage_required_days=coverage.required_days,
        coverage_storage_ready=coverage.storage_ready,
        activity_scope_total_channels=scope_report.total_channels,
        activity_scope_accessible_channels=scope_report.accessible_channels,
        activity_scope_coverage_percent=scope_report.coverage_percent,
        activity_scope_complete=scope_report.complete,
        activity_scope_problems=scope_problem_lines,
    )
'''
    if text.count("activity_scope_total_channels=scope_report.total_channels") < 2:
        if final_anchor not in text:
            raise RuntimeError("final report scope anchor missing")
        text = text.replace(final_anchor, final_replacement, 1)

    if text == original:
        print("activity scope cleanup already applied")
        return text

    required = (
        "scope_report = audit_activity_scope(guild)",
        "report_actionable = bool(coverage.actionable and scope_report.complete)",
        'return "Incomplete channel scope"',
        "return min(source_percent, scope_percent)",
    )
    for marker in required:
        if marker not in text:
            raise RuntimeError(f"activity scope integration missing marker: {marker}")
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
            "message": "fix: make inactivity coverage account for inaccessible channels",
            "content": base64.b64encode(cleaned.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("updated_commit", result.get("commit", {}).get("sha"))


if __name__ == "__main__":
    main()
