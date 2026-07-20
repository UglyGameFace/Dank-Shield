from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request

REPO = "UglyGameFace/Dank-Shield"
BRANCH = "fix/setup-access-spamguard-defaults-startup-cleanup"
PATH = "stoney_verify/commands_ext/public_setup_recommend.py"


def _api(method: str, url: str, *, payload: dict | None = None) -> dict:
    token = os.environ["GH_TOKEN"]
    request = urllib.request.Request(
        url,
        data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
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


def _fetch() -> tuple[str, str]:
    query = urllib.parse.urlencode({"ref": BRANCH})
    url = f"https://api.github.com/repos/{REPO}/contents/{PATH}?{query}"
    row = _api("GET", url)
    content = base64.b64decode(row["content"]).decode("utf-8")
    return content, row["sha"]


def _transform(text: str) -> str:
    original = text

    permission_marker = "\nasync def _open_permission_repair(\n"
    if "async def _open_bot_access_check(" not in text:
        insertion = '''\nasync def _open_bot_access_check(\n    interaction: discord.Interaction,\n) -> None:\n    \"\"\"Open the read-only activity coverage access check.\"\"\"\n\n    if not await solid._require_setup_permission(interaction):\n        return\n\n    from stoney_verify import setup_activity_access\n\n    await setup_activity_access.open_activity_access_check(interaction)\n\n'''
        if permission_marker not in text:
            raise RuntimeError("permission repair insertion marker not found")
        text = text.replace(permission_marker, insertion + permission_marker, 1)

    old_section = '''        title="🛡️ Logs & Safety",\n        description="Choose what gets logged, change spam and raid protection, or fix channel access.",\n        items=(\n            "🧾 **Choose What Gets Logged** — choose which server actions are saved in the log.",\n            "🛡️ **Spam & Raid Protection** — change spam and raid safety settings.",\n            "🛠️ **Fix Channel Permissions** — check and fix access to Dank Shield channels.",\n        ),\n'''
    new_section = '''        title="🛡️ Logs & Safety",\n        description="Choose what gets logged, change spam and raid protection, check bot activity access, or repair channel permissions.",\n        items=(\n            "🧾 **Choose What Gets Logged** — choose which server actions are saved in the log.",\n            "🛡️ **Spam & Raid Protection** — change spam and raid safety settings.",\n            "🔐 **Check Bot Access** — see exactly which channels Dank Shield cannot inspect for accurate activity tracking.",\n            "🛠️ **Fix Channel Permissions** — preview broader channel permission repairs before applying anything.",\n        ),\n'''
    if old_section in text:
        text = text.replace(old_section, new_section, 1)
    elif "🔐 **Check Bot Access**" not in text:
        raise RuntimeError("Logs & Safety section marker not found")

    old_button = '''    @discord.ui.button(label="Fix Channel Permissions", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_monitoring:permission_repair", row=1)\n    async def permission_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        await _open_permission_repair(interaction)\n'''
    new_button = '''    @discord.ui.button(label="Check Bot Access", emoji="🔐", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_monitoring:bot_access", row=1)\n    async def bot_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        await _open_bot_access_check(interaction)\n\n    @discord.ui.button(label="Fix Channel Permissions", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_monitoring:permission_repair", row=1)\n    async def permission_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        await _open_permission_repair(interaction)\n'''
    if "custom_id=\"dank_setup_advanced_monitoring:bot_access\"" not in text:
        if old_button not in text:
            raise RuntimeError("Fix Channel Permissions button marker not found")
        text = text.replace(old_button, new_button, 1)

    if text == original:
        print("PR #99 setup UI already applied")
        return text

    compile(text, PATH, "exec")
    return text


def main() -> None:
    source, sha = _fetch()
    updated = _transform(source)
    if updated == source:
        return

    endpoint = f"https://api.github.com/repos/{REPO}/contents/{PATH}"
    result = _api(
        "PUT",
        endpoint,
        payload={
            "message": "feat: add Check Bot Access to Logs and Safety",
            "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": BRANCH,
        },
    )
    print("updated_commit", result.get("commit", {}).get("sha"))


if __name__ == "__main__":
    main()
