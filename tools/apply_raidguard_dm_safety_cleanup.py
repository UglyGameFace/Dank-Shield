from __future__ import annotations

"""Tighten RaidGuard DM-spam safety and remove misleading low-risk wording.

This does three focused things:
1. Replaces the sloppy suspicious-name regex so `matrixofreality` is not flagged
   just because it contains the substring `real`.
2. Changes low-score profiles with heuristic flags from CLEAR to WATCHLIST in the
   human summary, so logs stop saying CLEAR while listing suspicious flags.
3. Adds quick mod buttons and a DM visibility warning to the staff join audit,
   because Discord bots cannot read member-to-member DMs.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAIDGUARD = ROOT / "stoney_verify/raidguard.py"
AUDIT_GUARD = ROOT / "stoney_verify/startup_guards/member_lifecycle_audit_context_guard.py"

OLD_REGEX = '''_SUSPICIOUS_NAME_RE = re.compile(
    r"(free|nitro|gift|airdrop|support|mod|staff|admin|real|backup|alt|test|temp|burner)",
    re.IGNORECASE,
)
'''

NEW_REGEX = '''_SUSPICIOUS_NAME_RE = re.compile(
    r"(free[\\W_]*nitro|nitro[\\W_]*gift|discord[\\W_]*gift|steam[\\W_]*gift|airdrop)"
    r"|(^|[^a-z0-9])(support|staff|admin|mod|backup|alt|test|temp|burner|real[\\W_]*(support|staff|admin|mod|discord))($|[^a-z0-9])",
    re.IGNORECASE,
)
'''

OLD_SUMMARY = '''    score = int(profile.get("score") or 0)
    level = str(profile.get("level") or "low").upper()
    tier = str(profile.get("evidence_tier") or "clear").replace("_", " ").upper()
    age_human = _humanize_age_days(int(profile.get("account_age_days") or 0))
'''

NEW_SUMMARY = '''    score = int(profile.get("score") or 0)
    level = str(profile.get("level") or "low").upper()
    tier = str(profile.get("evidence_tier") or "clear").replace("_", " ").upper()
    # Do not tell staff an account is CLEAR while also showing heuristic flags.
    # Low-confidence flags are not proof, but they are not "clear" either.
    if tier == "CLEAR" and list(profile.get("suspicion_flags") or []):
        tier = "WATCHLIST"
    age_human = _humanize_age_days(int(profile.get("account_age_days") or 0))
'''

OLD_AUDIT_SEND = '''    embed.add_field(name="Dank Shield context", value=_hidden_dank_context(member)[:1024], inline=False)
    embed.set_footer(text="dank_shield:staff_join_audit:v3")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
'''

NEW_AUDIT_SEND = '''    embed.add_field(name="Dank Shield context", value=_hidden_dank_context(member)[:1024], inline=False)
    embed.add_field(
        name="DM spam limitation",
        value=(
            "Discord does not expose member-to-member DMs to bots. If this member is reported for NSFW/spam DMs, "
            "use the quick moderation buttons immediately. Detection must happen from join/risk signals or user reports."
        ),
        inline=False,
    )
    embed.set_footer(text="dank_shield:staff_join_audit:v3")
    view = None
    try:
        from stoney_verify.modlog import build_quick_mod_view
        view = build_quick_mod_view(int(member.id))
    except Exception:
        view = None
    await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
'''


def replace_required(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"✅ patched {label}: {path.relative_to(ROOT)}")
        return True
    if new in text:
        print(f"✅ already patched {label}: {path.relative_to(ROOT)}")
        return False
    raise SystemExit(f"Could not find target block for {label} in {path}")


def main() -> None:
    replace_required(RAIDGUARD, OLD_REGEX, NEW_REGEX, "suspicious-name regex")
    replace_required(RAIDGUARD, OLD_SUMMARY, NEW_SUMMARY, "WATCHLIST summary")
    replace_required(AUDIT_GUARD, OLD_AUDIT_SEND, NEW_AUDIT_SEND, "staff audit quick mod + DM warning")
    print("✅ RaidGuard DM safety cleanup complete")


if __name__ == "__main__":
    main()
