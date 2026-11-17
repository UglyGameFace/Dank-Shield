# SpamGuard single-message promotion detection

SpamGuard's canonical on-message and on-message-edit handlers now recognize
**high-signal combinations** of content instead of requiring every incident to
repeat, use URLs, or ping everyone twice. This also covers accounts that clicked
the basic verification button, as long as the guild's SpamGuard setting
"Apply to verified users" is ON.

## Current patterns
- Unsolicited personal advertising: a private-contact request **and** multiple
  templated romantic/adult promotional lures in a substantial message.
- Mass-ping device giveaway: @everyone/@here (or equivalent Discord mention)
  **and** a device giveaway claim **and** a request for private contact.

The system does **not** score gender, ethnicity, names, or appearance. It does
not claim that every giveaway is fraudulent or that passing a button proves
someone is human. Normal conversations, legitimate no-ping giveaways, and
ordinary community announcements should not match these strict combinations.

## Enforcement safety
- All existing global SpamGuard enable/disable, staff, exempt-user,
  exempt-role, and allowed-channel settings are respected.
- Only a content-classified message is selected for removal when no other
  burst rule fired. The existing burst rules and invite policy remain intact.
- For a **content-only** first hit, logged-only/deletion-only guild settings
  stay unchanged. Recently joined (within 2 days) or recently created
  (within 7 days) users can receive a reversible timeout or configured
  quarantine. Established users receive deletion + modlog, without timeout.
- One content-only heuristic cannot automatically kick or ban an account even
  if the guild has set kick/ban for *confirmed burst* behavior.
- Subsequent content-spam messages are still deleted during an action cooldown,
  without repeating the disciplinary action.
- Message edits are checked, too.

Staff should review incidents and restore messages or lift restrictions
promptly when a false positive is reported. There is no infallible
"real person vs bot" classifier.

## Setup checklist
1. Confirm SpamGuard is **enabled** for the guild and **Apply to verified
   users** is ON in the Protection/SpamGuard panel.
2. Confirm the bot has permission to View Channels, Read Message History,
   Manage Messages, and Moderate Members, and that Discord has approved the
   application's privileged Message Content intent where required.
3. For stronger verification than one button, use the already existing
   verification-flow controls: account-age, membership delay, and optional
   staff review. Do not silently change each guild's configured verification
   policy in code.
4. Verify the dedicated advertising/partner channel is in the correct allow
   list where promotions are permitted.
5. Deploy this revision to Discloud and reproduce from a non-staff,
   newly verified test account. Confirm removal, a SpamGuard modlog entry,
   safe permissions, and no false positives for a normal game/event post.

This is an incremental explainable content rule, **not** a remotely hosted LLM
or an external reputation API.
