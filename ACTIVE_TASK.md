# ACTIVE TASK

## Active task / desired outcome

**P0-MEMBER-LIFECYCLE-LOGGING-005 — restore detailed Modlog member exits and reliable operational join/leave logging**

The access-repair task is closed: PR #314 merged as
`344b148fda3999c703cfeb9bf22972a0f82e1a7a` and every exact-head workflow
passed.

The current production concern is that Modlog member events are less detailed
than they used to be and the configured join/leave log appears unreliable.

## Single active task lock

Only member lifecycle logging is active:

- canonical operational join/leave delivery;
- staff Modlog detail for member departures;
- Member Logs route/access/runtime health.

Do not reopen access repair, verification, tickets, AntiNuke, or unrelated
Modlog event families unless concrete tracing proves a direct dependency.

## Confirmed findings / root cause

### 1. Useful lifecycle detail was lost during duplicate-sender retirement

The historical `public_member_lifecycle_logs` module still contains richer
join/leave cards, but production intentionally does not register that module.
That retirement is correct because re-registering it would create a second
`on_member_join` / `on_member_remove` sender.

The surviving authoritative router,
`member_lifecycle_router_guard`, had been reduced to an operational card with
only member identity and member count.

The staff Modlog voluntary-leave path in `events.py` was even thinner: it
emitted only a **User** field. Kick paths already retained the canonical rich
member-context builder, proving the detail service still exists.

### 2. Member Logs repair skipped the actual operational join/leave channel

`public_logging_contextual_permission_repair._member_log_targets()` included:

- live Welcome Card channel;
- live Exit Card channel;
- staff audit channel;

but omitted the configured operational join/leave log itself.

A broken permission overwrite on the channel that receives join/leave events
therefore could not be repaired from the Member Logs repair control.

### 3. Operational event delivery was conditionally suppressed by card delivery

The router described the join/leave log as an independent event stream, but it
suppressed the operational event whenever Welcome/Exit Card Studio successfully
posted to the same channel.

That means a configured join/leave log did not actually record every event.
The member-facing card and operational event record are separate products and
must not silently replace one another.

### 4. Member Logs configuration silently retargeted Exit Card Studio

Choosing the operational `join_leave_log` argument in `/dank member-logs`
also wrote `exit_card_channel_id`.

That crossed ownership boundaries: configuring an audit/event route silently
changed the member-facing Exit Card Studio target.

### 5. Production startup proves the router and operational route existed

A recent production startup line from September 24 shows:

`member lifecycle routes ready guild=1098088221457514609 ... join_log=1516001635023716443 ... staff=1516001634113687613`

The same line reported the old member-facing welcome/exit compatibility route as
missing channel `1499880759622631475`.

This proves, for that deployment:

- the authoritative lifecycle router was active;
- an operational join/leave route was configured and resolved;
- the staff audit route was configured and resolved;
- the member-facing welcome/exit compatibility mapping had stale channel state.

The old startup line did **not** test whether the operational channel was
writable, so it could not distinguish a healthy route from missing View Channel
/ Send Messages / Embed Links. No real failing join/leave event line was present
in the available production sample, so the exact live event-time skip branch is
still not invented.

The router now records operational route writability during startup and exposes
the same health on the Member Logs screen.

## Implementation

### Canonical operational join/leave detail

The one surviving router now builds richer, public-safe operational events with:

- member identity;
- account creation time and account age;
- display name and bot-account status;
- member count;
- server membership duration on leave;
- avatar;
- explicit operational-log footer.

No invite intelligence, identity links, staff verdicts, or moderator audit data
is leaked into the operational route.

### Staff Modlog voluntary-leave detail

`modlog.build_member_leave_embed()` is now the canonical detailed voluntary
leave record.

It restores:

- user identity;
- account creation and age;
- server join date and membership duration;
- roles at exit;
- existing canonical member assessment/context/identity-link fields;
- avatar and clear voluntary-leave footer.

Kick/ban attribution paths remain unchanged and continue to use their existing
audit-log evidence.

`events.on_member_remove` still owns the staff Modlog path and calls this
builder. No second member-remove listener is added.

### Operational route access repair

Member Logs contextual repair now resolves and includes the exact configured
operational join/leave log as a `logs` repair target.

The repair UI no longer claims Member Logs is healthy while skipping the route
that actually records joins/leaves.

### Route ownership separation

`/dank member-logs` no longer writes `exit_card_channel_id` when the admin
selects an operational join/leave log. Exit Card Studio owns its own route.

Operational join/leave events are no longer suppressed merely because a
member-facing Welcome/Exit card was delivered to the same channel.

### Runtime health visibility

The Member Lifecycle Routing response now reports:

- operational route resolution;
- exact View Channel / Send Messages / Embed Links readiness;
- whether this bot process requested the Server Members intent;
- whether the canonical join listener is registered;
- whether the canonical leave listener is registered.

Startup route logging now records the same operational-channel writability
result alongside the resolved channel ID.

This makes a future non-delivery diagnosable from Discord and from one startup
line instead of requiring guesswork.

## Safety invariants

- exactly one canonical operational join/leave sender remains;
- retired `public_member_lifecycle_logs` stays unreachable from command
  profiles;
- Welcome Card Studio and Exit Card Studio remain member-facing card owners;
- staff Modlog remains separate from operational/public-safe lifecycle events;
- no new Discord event listener owner is introduced;
- no staff-only risk/identity/audit context is exposed in the operational log;
- kick and ban dedupe/attribution semantics remain unchanged;
- permission repair still delegates to the shared contextual repair core.

## Validation required

- operational join event detail test;
- operational leave membership-detail test;
- detailed voluntary-leave Modlog test;
- same-channel Studio delivery must not suppress the operational log;
- Member Logs repair must include the operational route;
- Member Logs must not retarget Exit Card Studio;
- canonical listener ownership/static wiring tests;
- Modlog semantic dedupe tests;
- Python compile;
- full test suite;
- all GitHub workflow gates;
- currentness / mergeability / review / diff hygiene.

## Status

**IN PROGRESS — implementation under focused validation**

Branch: `fix/member-lifecycle-log-detail-runtime-20260924`

Base: merged PR #314 / current `main`.

## Next step

Finish focused regression coverage, open the draft PR, validate the exact head,
repair only failures causally related to this logging task, then merge only when
all required checks are green.
