# Dank Shield Privacy Policy

_Last updated: 2026-04-26_

> This template is provided for public/beta launch preparation and should be reviewed by the bot owner before publishing. It is not legal advice.

## 1. What Dank Shield is

Dank Shield is a Discord bot that provides server verification, ticket support, moderation assistance, anti-spam protections, audit/modlog features, and related server administration tools.

This policy explains what data Dank Shield may process when the bot is added to a Discord server or when a user interacts with the bot.

## 2. Who controls the data

For each Discord server, the server owner and authorized server administrators decide how Dank Shield is configured and used.

The bot operator maintains the infrastructure that stores and processes bot data.

## 3. Data Dank Shield may collect

Depending on enabled features, Dank Shield may process or store:

- Discord user IDs
- Discord guild/server IDs
- Discord channel IDs, role IDs, message IDs, ticket channel IDs, and thread IDs
- Usernames, display names, avatars, and role membership needed for moderation or ticket context
- Ticket metadata, including ticket creator, assignee, category, status, priority, timestamps, close/reopen details, and staff notes
- Ticket messages and transcript data when transcript features are enabled
- Verification status, verification timestamps, verification channel/session data, and related moderation decisions
- Join/leave timestamps and server membership state used for verification, moderation, or anti-raid protections
- Moderation action logs, including kicks, bans, timeouts, role changes, channel actions, and audit-context metadata where available
- Spam/raid detection metadata, such as message-rate counters, invite detection state, temporary lock/quarantine state, and enforcement outcomes
- Bot configuration for a server, including configured category IDs, channel IDs, role IDs, and feature settings
- Operational logs needed to diagnose crashes, abuse, security events, or service reliability problems

Dank Shield should not intentionally collect passwords, payment-card data, government IDs, private Discord tokens, or unrelated sensitive personal information.

## 4. Why the data is used

Dank Shield uses data to:

- Create, manage, close, archive, and transcript support tickets
- Verify users and manage configured verification roles
- Help staff moderate servers and keep audit records
- Detect spam, raids, repeated abuse, or suspicious automation
- Enforce configured cooldowns, limits, and server safety settings
- Provide dashboards, metrics, server setup health checks, and administrative tools
- Debug bot failures, prevent abuse, and maintain security

## 5. Legal and safety basis

Dank Shield processes data only to provide the requested bot functionality for Discord communities that add and configure the bot.

Server administrators should disclose their own moderation, logging, transcript, and retention practices to their members where required.

## 6. Data sharing

Dank Shield does not sell user data.

Data may be processed through infrastructure and service providers needed to run the bot, such as database hosting, application hosting, logging, and Discord itself.

Data may be disclosed when required to comply with valid legal obligations, protect users, prevent abuse, or enforce the bot's terms.

## 7. Data retention

Dank Shield should retain data only as long as needed for the feature that created it, server safety, moderation accountability, debugging, legal compliance, or abuse prevention.

Recommended default retention targets:

| Data type | Suggested retention |
|---|---:|
| Runtime spam windows, temporary locks, cooldown memory | Minutes to days |
| Verification sessions and temporary tokens | Until expiry plus short cleanup window |
| Ticket records and transcripts | Configurable by server owner |
| Moderation logs and audit context | Configurable by server owner |
| Operational crash/debug logs | As short as practical |
| Guild configuration | Until the server removes the bot or deletes configuration |

Server owners may request deletion of their guild configuration and stored guild data, subject to safety, abuse-prevention, and legal requirements.

## 8. User controls and deletion requests

A Discord user may contact the server owner/staff first for ticket or moderation data controlled by that server.

A server owner or authorized administrator may request deletion/export of server-specific bot data by contacting the bot operator.

Requests should include:

- Discord server/guild ID
- Discord user ID, when the request concerns a specific user
- Description of the data requested for export or deletion
- Proof of authority to act for the server, when applicable

## 9. Server configuration and permissions

Dank Shield stores server-specific configuration by Discord guild ID. This may include role IDs, channel IDs, ticket category IDs, transcript channel IDs, verification channel IDs, and moderation log channel IDs.

Only users with appropriate server permissions, such as Administrator or Manage Server, should be allowed to configure the bot.

## 10. Security

The bot operator should use reasonable technical and organizational safeguards, including:

- Keeping Discord bot tokens and Supabase service-role keys server-side only
- Not exposing service-role keys to browsers, public repositories, or Discord messages
- Requiring authentication for internal bot APIs
- Restricting database access with row-level security where applicable
- Avoiding collection of unnecessary sensitive data
- Reviewing logs for secrets before sharing them publicly

## 11. Children's privacy

Dank Shield is designed for Discord communities and is not intended to knowingly collect personal information from children outside the normal operation of Discord server administration. Server owners are responsible for ensuring their communities follow applicable age, safety, and platform requirements.

## 12. Changes to this policy

This policy may be updated as features, infrastructure, or legal requirements change. The updated date at the top should be changed whenever material updates are made.

## 13. Contact

Bot operator contact:

- Email: `REPLACE_WITH_SUPPORT_EMAIL`
- Discord support server: `REPLACE_WITH_SUPPORT_SERVER_URL`
- Website: `REPLACE_WITH_WEBSITE_URL`
