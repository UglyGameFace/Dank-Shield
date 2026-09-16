# Protection Invite Guard Retirement

Base: `734916ba151ccc408ce9fa07a4635fd6ecb24776` (merged PR #242)

This follow-up records the coordinated retirement of the obsolete Invite Shield startup-guard UI chain after `/dank protection` gained native Invite Shield ownership.

## Canonical production owners

Production Invite Shield behavior is now split deliberately:

- `stoney_verify.commands_ext.public_protection_invite_ui` owns the public targeting and historical-cleanup UI.
- `stoney_verify.invite_scope_settings` owns guild-scoped target metadata.
- `stoney_verify.invite_policy_engine` owns invite extraction, policy decisions, live deletion decisions, and historical scan/delete decisions.
- `stoney_verify.commands` installs the native UI and policy-scope binding explicitly during normal boot.

No startup guard is required for that product path.

## Retired duplicate UI/cleanup guards

The following modules duplicated the now-native UI or existed only to support that old guard chain and are removed together:

- `protection_center_invite_simple_flow_guard.py`
- `protection_center_invite_controls_guard.py`
- `protection_center_invite_status_guard.py`
- `spam_guard_invite_scope_pagination_guard.py`
- `invite_hard_block_all_bots_controls_guard.py`
- `protection_invite_cleanup_picker_guard.py`
- `protection_invite_toggle_cleanup_guard.py`

Deleting these as a coordinated set matters because the historical modules imported and patched one another. Removing one file while leaving the rest would preserve a charming little museum exhibit of broken imports.

## Surviving guards adjusted

`protection_center_clear_categories_guard.py` remains a dormant general Protection Center wording compatibility guard. Its Invite Shield editor imports and editor monkey patches were removed; it no longer depends on any retired Invite Shield UI guard.

`protection_center_filter_list_guard.py` remains a dormant content-filter helper. Its hidden chain into the retired invite-cleanup picker was removed.

The inert historical startup-guard inventory no longer advertises the retired Invite Shield UI owners.

## Validation contract

Regression coverage must prove all of the following:

1. every retired file above is absent;
2. the historical registry does not list those owners;
3. surviving general Protection Center guards do not import or chain them;
4. normal boot explicitly installs the native Invite Shield UI and target-policy binding;
5. the native UI uses `DankGuildResourceBrowserView`, not Discord raw resource selectors;
6. historical cleanup delegates to `invite_policy_engine.scan_channel_invites` and contains no direct `message.delete` path;
7. the existing invite-link safety audit points at the native owner rather than a deleted guard.

The older `STARTUP_GUARD_RUNTIME_OWNERSHIP_AUDIT.md` remains a historical audit of the earlier repository base. This document is the current disposition for the Invite Shield UI subset that audit intentionally deferred.