# The inbox side

Everything so far has been fed by `scripts/load_samples.py`, run by hand. This is the
part that makes it live: a mailbox is watched, and every message that arrives goes
through the same pipeline the samples did.

`imap-to-ingest.json` is the workflow. Import it, connect five IMAP credentials, set
three environment variables, activate.

## One collector mailbox

Every staff mailbox — `info@`, `sales@` and `sales1@` on `tvdservices.com`, plus
`tvdlogistics@outlook.com` and `tvdservices@hotmail.com` — **forwards into a single
collector Gmail**, and n8n watches that one.

This was five collectors for a while, one per staff mailbox, on the grounds that
whoever can open a shared collector can read every email all six staff ever received.
Samir has decided one is enough. The consequence is worth stating plainly rather than
discovering later: **anyone with the collector's password can read everything the six
mailboxes forward**, including whatever arrives at a work address that has nothing to
do with the trade. The privacy gate in **Build the payload** is what limits the damage —
it decides what reaches the database at all — but it does not limit what sits in the
collector's own inbox.

If that changes, the trigger nodes are generated: add mailboxes to `MAILBOXES` in
`build_workflow.py`, re-run it, re-import. Nothing downstream changes, because the Code
node reads `$input.all()` and never names its trigger.

```bash
python n8n/build_workflow.py
```

### Why IMAP at all, rather than a Gmail or Outlook API

Not laziness — the alternatives are worse than they look:

- **Basic authentication for IMAP and POP is already disabled in every Exchange Online
  tenant**, and Microsoft support cannot re-enable it. App passwords died with it, and
  consumer `@outlook.com` went the same way. So `tvdlogistics@outlook.com` cannot be read
  over IMAP at all — it would need an Entra app registration, OAuth consent and
  refresh-token upkeep, and being a *consumer* account it does not even qualify for the
  clean admin routes (Application Access Policy, RBAC for Applications).
- **`gmail.readonly` is a restricted scope.** An External OAuth app needs Google
  verification plus an annual third-party CASA security assessment — weeks. Left in
  Testing mode, its refresh token expires every 7 days, so you would re-authorise five
  mailboxes every week forever.

Two providers, two app registrations and permanent token maintenance — versus an app
password per collector that does not expire. Hence collectors, and hence IMAP.

Gmail's app passwords are still the supported route for IMAP: the September 2024
shutdown ended plain-password access, not app passwords, and the January 2026
deprecation is POP, not IMAP. Google removed the enable/disable IMAP toggle in January
2025, so IMAP is simply always on for a personal account now — there is nothing to
switch.

### Forward, or redirect?

| Source | How | Why |
| --- | --- | --- |
| Gmail / Workspace | Settings → Forwarding | Auto-forward preserves the original `From:` |
| Outlook / `outlook.com` | rule → **Redirect to** | Redirect preserves `From:`; **Forward** wraps the body |

Use **Redirect** on Outlook, never Forward. Redirect leaves the headers alone, so
`resolve_sender` reads the real supplier off the envelope at confidence 1.0. Forward
wraps the message and drops it to the forwarded-header path at 0.9 — survivable, but
needlessly worse.

### The one setting that makes or breaks this

The tenant's `internal_domains` **must** list `tvdservices.com`, and
`internal_addresses` must list `tvdlogistics@outlook.com`, the collector itself, and any
other staff address. See `supabase/setup_tenant.sql`.

`resolve_sender` reads the envelope first and only looks for a forwarded `From:` block
once the envelope turns out to be internal. Leave the list empty and every forwarded
message is filed against the staff member who forwarded it. On the first load that put
**3,252 offers under `sales@tvdservices.com`**, as though a colleague were the client's
largest supplier. The collector mailbox relays *everything*, so leaving it off the list
makes it look like the busiest supplier of all.

## The privacy gate

The workflow decides whether an email is trade mail at all, and drops it if not. That
decision is in the **Is this trade mail?** node, and it has to be there rather than on
the backend.

The pipeline stores an email *before* it classifies it — `save_email` runs, then the
classifier concludes it is not an offer. So a bank statement "rejected" by the backend
is a bank statement stored in full, sitting in a review queue for someone to open.
Dropping it in n8n means it never reaches the database.

The test is deliberately loose. It keeps anything with a spreadsheet attached, trade
wording in the subject (`WTS`, `WTB`, `offer`, `stock`, `price`, `Angebot`, `oferta`…),
or prices quoted against product names in the body. Everything else is dropped, and the
false branch goes nowhere on purpose — the email stays in the collector, unread by
anything, and the execution log records what was dropped and why.

Loose in that direction because the two mistakes are not equal. A price list this drops
is invisible: nobody knows to look for an email that never arrived. Junk that gets
through costs one row and one model call, and the backend's classifier is the second
gate and much better at the judgement.

**This is not the only place to filter.** A rule on each staff mailbox, so private mail
never leaves it at all, is stronger — see `docs/collector-mailbox.md`. The two compose:
narrow at the source, precise here.

## What n8n does, and what it deliberately does not

n8n's job is **delivery**. It fetches a message and POSTs it to `/ingest`. Every decision
about what the message *means* — who sent it, whether they are buying or selling, what a
row says, what anything costs — happens in Python, where it is covered by 682 tests.

That line matters because n8n is a GUI. Anything living only inside it cannot be reviewed
in a diff, cannot be tested, and will one day be edited by someone in a hurry.

**Spreadsheets are no longer converted here.** The workflow sends the workbook as
`content_base64` and the backend reads it with `read_spreadsheet` — the same function the
sample corpus goes through. The old Extract-From-File approach meant a second,
untested implementation of that reader written in JavaScript, and it carried a real bug:
it pooled every sheet row in the execution, so two emails arriving in one poll each
received the other's rows. Only workbooks are sent whole; signature logos and PDFs are
recorded by name, because base64 inflates a file by a third and a 2MB logo on every email
is 2MB of nothing.

One rule does appear on both sides: stripping quoted reply history. n8n does it because
the payload should arrive clean, and the backend does it again because n8n might stop.
Both refuse to cut at a marker when a forwarded message sits below it — the whole design
is forwarding, so the payload is *under* `Begin forwarded message`, and a stripper tuned
for a normal mailbox would delete the contents of most of the inbox. The backend goes
further and refuses any cut that would remove more prices than it keeps.

## Setup

### 1. The collector mailbox

A dedicated Gmail nobody reads by hand:

1. **2-Step Verification on** — app passwords are not offered without it.
2. **App password**: Google Account → Security → 2-Step Verification → App passwords.
   Sixteen characters, shown once. This is the only mail credential the system holds.
3. **A filter so nothing is ever marked spam.** Forwarding breaks SPF, so forwarded
   supplier mail can land in Spam — and the IMAP trigger reads `INBOX` only, so anything
   filtered is silently lost. Filter: `from:(*)` → *Never send it to Spam*.

There is no step for enabling IMAP. Google removed that toggle in January 2025 and IMAP
is always on for personal accounts.

4. **Forward each staff mailbox into it**, and confirm the forward from this account —
   Gmail sends a confirmation to the collector for each one. Use Gmail's *auto-forward*
   and Outlook's **Redirect**, not Outlook's *Forward*: Redirect leaves the original
   `From:` and `Message-ID` untouched, while Forward wraps the message in a new one.
   The system copes with both, but Redirect is what lets it recognise the original
   sender at full confidence.

### 2. Somewhere to run n8n

```bash
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.io/n8nio/n8n
```

Fine for testing. For the client it has to be hosted, and on Render that means the
**$7/mo Starter plan — not the free tier, which sleeps after 15 minutes idle, and a
sleeping poller reads no mail.**

Two environment variables matter more than they look:

| Name | Why |
| --- | --- |
| `N8N_ENCRYPTION_KEY` | Set it **explicitly**. Credentials are encrypted with it; on an ephemeral filesystem a redeploy generates a new one and every stored credential becomes unrecoverable. |
| A **persistent disk**, 1 GB, mounted at `/home/node/.n8n` | Where n8n keeps its workflows, credentials and history. Without it, a redeploy starts from an empty instance. $0.25/month. |

**On storage: use the disk, not Postgres.** The obvious-looking option is to point n8n at
the Supabase database it already has. On Render that means going through Supabase's
connection pooler, because Supabase no longer offers direct IPv4 — which brings a
pooler hostname, a session-mode port, and a username that must be written
`n8n_user.<project-ref>` rather than `n8n_user`. Every one of those is a separate way to
spend an afternoon on a connection error.

n8n's default SQLite on a mounted disk avoids all of it, costs 25 cents a month, and is
a perfectly ordinary single-instance setup. The trade is that backups are a disk
snapshot rather than Supabase's, and n8n cannot be scaled to several replicas — neither
matters for one mailbox poller. `supabase/n8n_database.sql` is kept for the day one of
them does.

Prove it before trusting it: save a throwaway credential, force a redeploy, confirm it
still decrypts.

### 3. Three environment variables

**Settings → Variables**, or `-e` flags on the Docker command.

| Name | Value |
| --- | --- |
| `TVD_TENANT_ID` | printed by `supabase/setup_tenant.sql` when you run it |
| `TVD_BACKEND_URL` | the Render URL, e.g. `https://import-export-backend.onrender.com` |
| `TVD_INGEST_TOKEN` | the same string as `INGEST_TOKEN` in Render |

The token must match exactly. `/ingest` is a public URL: without it, anyone who finds it
can put fake stock on the client's board.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The payload builder throws if `TVD_TENANT_ID` is unset, rather than filing a morning's
mail against nothing and leaving a row of green ticks that moved no stock.

### 4. Import and connect

**Workflows → Import from File** → `n8n/imap-to-ingest.json`.

Then open **Watch the collector mailbox** → **Credential to connect with** → **Create
new**:

| Field | Value |
| --- | --- |
| User | the collector's Gmail address |
| Password | the 16-character app password, not the account password |
| Host | `imap.gmail.com` |
| Port | `993` |
| SSL/TLS | on |

Name it `Collector mailbox (IMAP)`.

### 5. Test before activating

Forward a real supplier email into one collector, then press **Execute workflow** — not
**Activate**. That runs it once over the unread mail with every node's input and output
visible.

Look at **POST /ingest**. Its reply is the backend saying what it did.

### 6. Activate

Toggle **Active**.

### 7. An error workflow

Set one under **Settings → Error Workflow** — anything that reaches you. A dead inbox
looks exactly like a quiet trading day, and the trade has plenty of quiet days.

With one trigger a bad credential is obvious — nothing arrives at all. The error
workflow matters for the quieter failures: a mailbox that stops forwarding, a Gmail
filter that starts eating mail, an `/ingest` returning 401 after a token rotation. All
of those look exactly like a slow trading day.

## Things that will come up

**"access to env vars denied" on the payload builder.** n8n 2.x blocks `$env` in Code
nodes by default. Set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and redeploy. The error names
the variable as though somebody set it; nobody did, it is the default.

**Nothing arrives.** The trigger only looks at *unread* mail, and opening the message
marks it read. Mark it unread again, or drop `customEmailConfig` in the trigger node.

**Nothing arrives, and the mailbox is full of mail.** Check Spam. Forwarding breaks SPF
and the trigger reads `INBOX` only. This is what the never-spam filter is for.

**It worked for an hour and then went quiet.** IMAP IDLE connections get dropped
silently. `forceReconnect: 60` is in the workflow for this; if it still happens, lower it.

**Every offer is filed against a colleague.** `internal_domains` is empty. This is the
3,252-offer bug — see above.

**401 from /ingest.** `TVD_INGEST_TOKEN` and Render's `INGEST_TOKEN` differ. They are
compared exactly; a trailing space counts.

**503 from /ingest.** `INGEST_TOKEN` is not set on Render at all. The endpoint refuses
rather than accepting everything, because an open ingest that shipped unnoticed is worse
than an outage.

**The spreadsheet arrived as the string `filesystem-v2:...`.** n8n was in filesystem
binary mode and something read `file.data` directly instead of
`getBinaryDataBuffer`. The shipped workflow uses the helper; a hand-edit may not.

**The same email processed twice.** It cannot double the board — `/ingest` deduplicates
on `message_id`, and a redelivery returns `duplicate` without touching anything. This
matters more here than in a single-mailbox setup: two staff forwarding the same supplier
email is routine, not rare.

**A workbook fails to parse.** `read_spreadsheet` reports an unreadable file as no sheets
rather than raising, and the attachment is kept without rows. The email still arrives
with its covering note, which often carries prices of its own.

## `gmail-to-ingest.json`

The previous workflow, kept for reference. It uses the Gmail OAuth trigger and converts
spreadsheets in n8n. Do not use it unless the collector-mailbox approach is abandoned —
and if it is, read the restricted-scope note at the top of this file first.
