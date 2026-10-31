# Member Connection Intelligence: ban-related review

## Placement and purpose

One canonical staff surface: `/dank members` → **Intelligence**.
- Select a current member → **Connections** for an evidence-led report.
- **Recent Ban Connections** browses up to ten recent banned accounts. No IDs need to be remembered.
- **Connections by ID** remains available for historical accounts.
- **Stored History** and existing **Intelligence Review** / staff-verdict controls remain available. No commands or moderation features were removed.

The feature does **not** reveal Discord friendships, other private servers, DMs, off-platform accounts, or screenshots shared by members. It does not infer that a person knowing a banned member is a leak.

## Evidence model

The read-only analyzer uses existing guild-scoped `guild_members`, `member_joins`, and `manual_alt_links` data. The dedicated primary Supabase project has `identity_proofs` but no `identity_proof_matches` relationship table, so this feature deliberately does **not** query the nonexistent table or infer another account from a single proof. It never queries other guilds or gathers private identity/device information.

The four distinct evidence classes are:
1. Reviewed duplicate account relationship recorded by staff.
2. Staff-marked possible same person.
3. Member invite or vouch relationship recorded by the server.
4. Unconfirmed invite attribution, clearly labeled as weak.

A `not_linked` staff decision suppresses same-identity claims but does not erase a legitimate historical invitation. Unresolved joins do not become proven links. No evidence available, incomplete data, and insufficient coverage are not clean bills of health. Findings are never considered proof of leaking information.

## Automatic review

- **Member ban event**: read a bounded set of existing relationships and, when any exist, post a private moderator-log lead. No public announcement, role change, kick, ban, or timeout.
- **Member join event**: after the existing join verification/safety path, reuse its risk profile and invite evidence. Only explicit linked IDs are checked against Discord's current ban endpoint. Similar names and weak risk clusters do not trigger ban assertions.
- **Staff panels**: re-read evidence on demand using the Refresh Evidence button. Data is not cached as an authoritative friendship graph.

All work runs with the existing moderation and storage permissions. The analysis is not part of the bot's verification decision. Backend query errors are marked as partial data, never silently turned into innocence or guilt.

## Limits and operations

- Guild-scoped database queries have bounded results; the UI shows a capped first page and flags truncated results.
- Actual Discord Ban Members privileges are required to browse recent bans or verify ban status. A failed Discord ban lookup is not an assertion.
- Discord does not supply ordinary bots with friendship or external server lists.
- New schemas, OAuth scopes, external services, and new persistent personal-data collections are **not** introduced.
- Existing authorization, staff-verdict, moderation hierarchies, RLS, and log-channel configuration remain authoritative.
- Moderators must review evidence and any reports of leaked content. Do not automatically restrict people merely for shared invitations or knowing someone.
- Confirm production visibility and storage permissions with a controlled canary before treating the alert system as operational. The feature cannot reconstruct relationships never recorded.
