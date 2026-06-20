# Dank Shield TicketTool Parity Audit

Last updated: 2026-05-08

## Product rule

Dank Shield should beat Ticket Tool by being faster and easier, not by forcing users through more steps.

Default workflow priority:

1. Buttons first
2. Select menus second
3. Forms/modals last

Forms/modals must be optional and off by default. The default ticket flow should be instant.

## Plain-language rule

Dank Shield setup must use simple words that normal server owners and members understand immediately.

Avoid unclear labels like:

- "verification heavy"
- "advanced workflow"
- "intake flow"
- "security posture"
- "template profile"

Use direct labels like:

- "Basic server"
- "Help desk"
- "ID check"
- "Voice check"
- "Custom setup"

Every setup option should answer these questions in plain English:

1. What does this do?
2. Who is it for?
3. What will members see?
4. Can I change it later?

## Confirmed direction

- Default ticket creation should not force a form.
- Users should be able to click and get a ticket open quickly.
- Optional reason/category selection is acceptable when it saves time.
- Long setup flows should be avoided.
- Public setup should explain what is enabled, what is missing, and how to fix it.
- Multi-guild isolation must remain strict.
- Caching/scaling work comes after TicketTool parity is solid.
- Do not assume every server wants the one specific server flow.
- legacy single-server style verification must be available as an optional setup choice, not the only default.
- Setup wording must be clear enough for young users, new Discord users, tired moderators, and users who need extra-simple instructions.

## Active TicketTool parity audit

### 1. Fast public ticket opening

Goal: user opens a ticket with the fewest possible steps.

Required behavior:

- [ ] Default ticket button opens a ticket immediately.
- [ ] If multiple ticket types exist, use a select menu before opening.
- [ ] Do not require a modal/form by default.
- [ ] If a form is enabled, label it clearly as optional.
- [ ] Ticket channel should immediately show helpful next-step buttons after creation.

Acceptance:

- New user can open a normal support ticket in 1 click when only one ticket type exists.
- New user can open a categorized ticket in 2 interactions when multiple types exist.
- No default “describe your issue” modal interrupts the flow.

### 2. Ticket setup simplicity

Goal: server owners can configure tickets without guessing.

Required behavior:

- [ ] `/dank setup` has a clear Tickets section.
- [ ] Tickets section shows On/Off instead of technical status words when possible.
- [ ] Tickets section shows missing permissions with real fixes.
- [ ] Tickets section has Preview Panel.
- [ ] Tickets section has Publish Panel.
- [ ] Tickets section has Edit Ticket Types.
- [ ] Ticket setup should not create duplicate/confusing panels.

Acceptance:

- Server owner can set up and publish a working ticket panel from setup without separate hidden commands.
- Every warning shown has an actionable fix.

### 3. Staff ticket controls

Goal: staff can manage tickets as fast as Ticket Tool or faster.

Required behavior:

- [ ] Claim/unclaim ticket.
- [ ] Assign/add staff.
- [ ] Add user.
- [ ] Remove user.
- [ ] Rename ticket.
- [ ] Close ticket.
- [ ] Reopen ticket.
- [ ] Delete ticket.
- [ ] Private staff notes.
- [ ] Priority/status controls.

Acceptance:

- Staff can complete common actions through buttons/select menus, not slash-command hunting.

### 4. Transcript and close flow

Goal: closing a ticket is reliable and professional.

Required behavior:

- [ ] Close confirmation.
- [ ] Transcript generated before delete/archive.
- [ ] Transcript sent to configured log channel.
- [ ] Transcript handles attachments/embeds where possible.
- [ ] Reopen works after close if configured.
- [ ] Delete respects confirmation/safety settings.

Acceptance:

- No ticket can be deleted before transcript handling succeeds or clearly reports failure.

### 5. Persistent panels and restart safety

Goal: panels should survive bot restarts and not expire constantly.

Required behavior:

- [ ] Public ticket panel buttons are persistent.
- [ ] Ticket channel action buttons are persistent.
- [ ] Setup panels either refresh cleanly or explain when expired.
- [ ] No “command outdated” loops after a successful boot.
- [ ] Startup repairs missing ticket channel panels.

Acceptance:

- After restart, existing ticket panels and open ticket controls still work.

### 6. Multi-guild production safety

Goal: no server leaks settings or branding into another server.

Required behavior:

- [ ] Every ticket lookup is guild-scoped.
- [ ] Every panel config is guild-scoped.
- [ ] Every staff role/category/log channel is guild-scoped.
- [ ] No Dank Shield branding remains in public scope.
- [ ] No guild-specific hardcoded channel/role IDs in public workflows.

Acceptance:

- Installing Dank Shield in a second server cannot expose or reuse the first server’s ticket config.

### 7. Better-than-TicketTool polish

Goal: feel easier and more modern than Ticket Tool.

Required behavior:

- [ ] Clean ticket embed design.
- [ ] Simple user-facing copy.
- [ ] Staff-only controls clearly separated from user controls.
- [ ] Setup preview matches the real published panel.
- [ ] Error messages explain what happened and how to fix it.
- [ ] Optional smart setup choices per server type.

Acceptance:

- A non-technical server owner can understand setup without reading docs.

### 8. Plain setup choices

Goal: stop assuming every server wants the same setup while keeping the legacy single-server style verification panel available as a simple choice.

Setup choices should use plain labels:

- [ ] Basic server — simple welcome/check-in and basic tickets.
- [ ] Help desk — ticket support for members/customers.
- [ ] ID check — users need to verify with an upload link.
- [ ] Voice check — users can ask staff to verify them in voice chat.
- [ ] ID + voice check — same style as the legacy single-server setup, but without hardcoded server branding.
- [ ] Custom setup — choose only what this server needs.

Required behavior:

- [ ] `/dank setup` shows simple setup choices instead of technical labels.
- [ ] Each choice has a one-sentence explanation.
- [ ] Each choice has Preview before Publish.
- [ ] Verification panel style is stored per guild.
- [ ] legacy single-server style setup is selectable but not assumed.
- [ ] Template choice must not hardcode server-specific channel IDs, role IDs, or branding into other guilds.
- [ ] Switching setup choices should not erase existing config without explicit confirmation.

Acceptance:

- A new server owner can choose a setup without knowing bot/developer terminology.
- The legacy single-server style verification panel can still be selected and published when desired.
- Setup remains button/select driven and avoids forms unless absolutely necessary.

## Already handled / removed from active TODO

These items are not active blockers unless a regression appears:

- Global `/dank` command surface exists in public profile.
- `/dank members` command group exists.
- Spam guard has been integrated into the public `/dank spam` surface.
- Member scan lock/unlock workflow exists.
- Member activity notice DM workflow exists.
- Notice worker startup no longer uses `bot.loop` before login.
- Notice Supabase calls have been moved off the Discord event loop in the nonblocking bundle.

## Later, after TicketTool parity

These are important, but they should not distract from ticket parity first:

- Bot-wide smart caching system.
- Background scan refresh cache.
- Cache diagnostics.
- Larger multi-guild scale testing.
- Membership/subscription tier enforcement.

## Non-negotiables

- Do not force ticket users into forms by default.
- Do not use confusing setup labels when plain words work.
- Do not assume one server’s verification/service setup is the universal default.
- Do not hardcode server-specific branding, channel IDs, or role IDs into public guild workflows.
- Do not add tiny patch files for core ticket behavior.
- Update existing owner files when behavior is wrong.
- Keep server-specific configuration isolated.
- Moderation actions must re-check live permissions and hierarchy before action.
