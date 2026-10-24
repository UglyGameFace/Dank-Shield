# Embed Studio: reusable media and Smart Text

Entry: **/dank → Design & Branding → Embed Studio**. These features extend the
canonical editor and existing server-scoped atomic config, not a second app.

## Media Library
Use **Media Library → Save Media Link** to store up to 12 named direct HTTPS
image/GIF URLs per server. Choose a saved link and select **Large Image**,
**Thumbnail**, **Author Icon** or **Footer Icon**. The selected image is added
to the active embed in your private draft; Undo and autosave still work.
Removing a saved URL requires a second confirmation and never deletes any
published message or rewrites older drafts.

These are **links, not uploaded files**. If a third-party URL expires, the
image may disappear. No new file storage, CDN proxy, credentials, workers,
or background downloads are introduced in this slice.

## Smart Text
Open **Smart Text** for the supported tokens. Enter them directly into
plain message text, titles, descriptions, authors, footers, or field text.

| Token | Replaced by |
|---|---|
| `{server.name}` | Server's name |
| `{channel.name}` | Destination channel |
| `{server.member_count}` | Discord-reported membership count |
| `{today.utc}` | Today's UTC date |

Unknown text remains literal. No code or arbitrary template expressions are
executed. Real values are substituted **once, when private Preview opens**.
The exact validated preview snapshot is then sent only after explicit Publish
confirmation. Previously rendered messages **do not update automatically**.
Drafts retain the original tokens, and changes after preview force another
preview. If Discord hasn't supplied a member count, a token depending on it
blocks preview rather than publishing a fabricated number. No role pings.

## Acceptance
Confirm legacy drafts, templates, saved themes, role controls, and tracked
posts remain intact. Test storage CAS collisions and outages, invalid media
links, the 12-link limit, Discord row/component limits, escaped mentions,
post-substitution text limits, mobile preview and exact publish snapshot.
Do not claim deployed until the merge SHA is observed on Discloud and the
Android UI works.
