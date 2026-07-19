from __future__ import annotations

"""Fail-closed authority boundary for member departure reconciliation.

This guard is imported by ``main.py`` before ``stoney_verify.app``. At that point
no runtime event module has captured the reconciliation callables yet, so replacing
the two unsafe orchestration entry points here makes every later caller use the
authoritative-member implementation while leaving the mature persistence helpers
inside ``members_new.sync_service`` unchanged.

The legacy functions remain preserved on private attributes for diagnostics and
rollback inspection; normal runtime callers never receive them.
"""

from stoney_verify.members_new import membership_authority, sync_service

_INSTALLED = False


def install_member_reconciliation_authority_guard() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    if not hasattr(sync_service, "_legacy_run_full_member_sync_for_guild"):
        sync_service._legacy_run_full_member_sync_for_guild = sync_service.run_full_member_sync_for_guild  # type: ignore[attr-defined]
    if not hasattr(sync_service, "_legacy_run_departed_reconciliation_for_guild"):
        sync_service._legacy_run_departed_reconciliation_for_guild = sync_service.run_departed_reconciliation_for_guild  # type: ignore[attr-defined]

    sync_service.run_full_member_sync_for_guild = membership_authority.run_safe_full_member_sync_for_guild
    sync_service.run_departed_reconciliation_for_guild = membership_authority.run_safe_departed_reconciliation_for_guild
    sync_service._MEMBERSHIP_AUTHORITY_GUARD_ACTIVE = True  # type: ignore[attr-defined]

    _INSTALLED = True
    print("🛡️ member_reconciliation_authority_guard active; departure marking requires authoritative Discord membership")
    return True


install_member_reconciliation_authority_guard()

__all__ = ["install_member_reconciliation_authority_guard"]
