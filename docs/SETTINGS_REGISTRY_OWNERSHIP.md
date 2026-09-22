# Settings Registry Ownership

## Purpose

`stoney_verify/settings_registry.py` is the canonical owner of setting meaning.

It defines:

- canonical key names;
- value types;
- defaults;
- legacy aliases and alias precedence;
- feature owner;
- persistence owner.

It deliberately does **not** perform database I/O, caching, Discord calls, or UI work.

## Storage ownership remains separate

- `guild_config.py` owns guild-config persistence, cache behavior, public-server isolation, and config invalidation.
- `spam_guard.py` owns its security-settings table and runtime Spam Guard cache.
- feature-native services may own specialized persisted state where already established.

The registry tells those systems what a setting means. It does not become another database layer.

## First migrated family

The first slice covers Protection / Automod / Invite Shield settings whose semantics were duplicated across:

- Protection Center;
- Invite Policy Engine;
- Invite reconciliation preflight;
- Invite Shield scope settings;
- Spam Guard compatibility reads.

Registered keys include:

- `automod_enabled`
- `automod_block_invites`
- `automod_block_links`
- `automod_link_policy`
- `automod_bad_words`
- `invite_shield_enabled`
- `invite_hard_block_enabled`
- `block_invites`
- `block_external_invites_only`
- `allow_server_invites`
- `invite_hard_block_target_all_bots`
- `invite_hard_block_target_bot_ids`
- `invite_hard_block_target_channel_ids`
- `invite_protected_poster_rule_enabled`

Legacy `spam_*` and older invite-target aliases remain readable where current behavior requires them.

## Compatibility rule

Canonicalization must not silently change precedence.

Example: Spam Guard historically reads persisted `spam_allow_server_invites` and `spam_block_external_invites_only` ahead of unprefixed compatibility values when both exist. The registry records that precedence explicitly.

For Invite Shield target metadata, canonical guild-config keys remain preferred, followed by their historical `spam_*` and old short aliases.

## Effective-state helpers

The registry owns the legacy-compatible effective answers for:

- whether Invite Shield is enabled;
- whether Link Shield is enabled;
- normalized Invite Shield target scope.

That prevents the live policy, recovery preflight, and Protection Center from maintaining separate key lists.

## Migration rule for future families

Migrate one feature family at a time:

1. inventory current keys, defaults, aliases, precedence, and persistence owner;
2. add specs without changing storage;
3. add compatibility tests;
4. route existing readers through registry helpers;
5. validate exact behavior;
6. only then consider removing obsolete aliases or consolidating storage.

Do not bulk-register unknown settings and do not move persistence merely to make the registry look more central.
