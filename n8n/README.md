# The inbox side

Everything so far has been fed by `scripts/load_samples.py`, run by hand. This is the
part that makes it live: a mailbox is watched, and every message that arrives goes
through the same pipeline the samples did.

`imap-to-ingest.json` is the workflow. Import it, connect one IMAP credential, set three
environment variables, activate.

## One collector mailbox, not five

The client has five staff on mixed providers — `info@`, `sales@` and `sales1@` on
`tvdservices.com`, plus `tvdlogistics@outlook.com`. They all **forward into a single
collector mailbox**, and n8n watches only that one.

This is not laziness, it is the only tractable option:

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

Five mailboxes, two providers, two app registrations, permanent token maintenance —
versus one app password that does not expire. Hence the collector.

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

A dedicated Gmail nobody reads by hand. On that account:

1. **2-Step Verification on** — app passwords are not offered without it.
2. **Settings → Forwarding and POP/IMAP → Enable IMAP.**
3. **App password**: Google Account → Security → 2-Step Verification → App passwords.
   Sixteen characters. This is the only mail credential the system ever holds.
4. **A filter so nothing is ever marked spam.** Forwarding breaks SPF, so forwarded
   supplier mail can land in Spam — and the IMAP trigger reads `INBOX` only, so anything
   filtered is silently lost. Filter: `from:(*)` → *Never send it to Spam*, or match the
   five forwarding addresses.

### 2. Somewhere to run n8n

```bash
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
```

Fine for testing. For the client it has to be hosted, and on Render that means the
**$7/mo Starter plan — not the free tier, which sleeps after 15 minutes idle, and a
sleeping poller reads no mail.**

Two environment variables matter more than they look:

| Name | Why |
| --- | --- |
| `N8N_ENCRYPTION_KEY` | Set it **explicitly**. Credentials are encrypted with it; on an ephemeral filesystem a redeploy generates a new one and every stored credential becomes unrecoverable. |
| `DB_TYPE=postgresdb` + `DB_POSTGRESDB_*` | Point n8n at its own schema in the existing Supabase Postgres, so no persistent disk is needed. Give it its own schema and role — not the application tables. |

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
| User | the collector address |
| Password | the 16-character app password, not the account password |
| Host | `imap.gmail.com` |
| Port | `993` |
| SSL/TLS | on |

### 5. Test before activating

Forward a real supplier email into the collector, then press **Execute workflow** — not
**Activate**. That runs it once over the unread mail with every node's input and output
visible.

Look at **POST /ingest**. Its reply is the backend saying what it did:

```
processed as sell: 219 rows, 219 inserted, 0 updated, 0 refreshed, 0 closed (status applied)
```

Check the sender it resolved. If a forwarded email comes back attributed to
`sales@tvdservices.com` rather than the real supplier, `internal_domains` is not set —
go back and run `setup_tenant.sql`.

`unclassified` or `needs_sender_review` is not a failure; the email is stored and waiting
in the dashboard's **Needs review** tab.

### 6. Activate

Toggle **Active**.

### 7. An error workflow

Set one under **Settings → Error Workflow** — anything that reaches you. A dead inbox
looks exactly like a quiet trading day, and the trade has plenty of quiet days.

## Things that will come up

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
