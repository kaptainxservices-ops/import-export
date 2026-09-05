# The inbox side

Everything so far has been fed by `scripts/load_samples.py`, run by hand. This is the
part that makes it live: a mailbox is watched, and every message that arrives goes
through the same pipeline the samples did.

`gmail-to-ingest.json` is the workflow. Import it, connect a Gmail account, set three
environment variables, activate.

## What it does, and what it deliberately does not

n8n's job is **delivery**. It fetches a message, converts any spreadsheet to rows, and
POSTs the result to `/ingest`. Every decision about what the message *means* — who sent
it, whether they are buying or selling, what a row says, what anything costs — happens
in Python, where it is covered by 500-odd tests.

That line matters because n8n is a GUI. Anything living only inside it cannot be
reviewed in a diff, cannot be tested, and will one day be edited by someone in a hurry.

One rule does appear on both sides: stripping quoted reply history. n8n does it because
the payload should arrive clean, and the backend does it again because n8n might stop.
Both implementations refuse to cut at a marker when a forwarded message sits below it —
the client's entire workflow is forwarding a supplier's email to himself, so the payload
is under `Begin forwarded message`, and a stripper tuned for a normal mailbox would
delete the contents of two thirds of his inbox. The backend goes further and refuses any
cut that would remove more prices than it keeps.

## Setup

### 1. Somewhere to run n8n

Either works:

- **n8n Cloud** — sign up, free trial, nothing to maintain. Paid after the trial.
- **Self-hosted** — free, and one command:

  ```bash
  docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
  ```

  Then open `http://localhost:5678`. Note that a locally-running n8n only polls while
  your machine is on, which is fine for testing and not for the client.

### 2. Three environment variables

In n8n: **Settings → Variables** (Cloud) or the `-e` flags on the Docker command.

| Name | Value |
| --- | --- |
| `TVD_TENANT_ID` | `b9316981-b732-4891-854f-3335205dd582` |
| `TVD_BACKEND_URL` | your Render URL, e.g. `https://import-export-backend.onrender.com` |
| `TVD_INGEST_TOKEN` | the same string as `INGEST_TOKEN` in Render |

The token has to match on both sides exactly. `/ingest` is a public URL: without it,
anyone who finds it can put fake stock on the client's board.

If you have not set `INGEST_TOKEN` on Render yet, generate one:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put it in Render under **Environment**, and in n8n as `TVD_INGEST_TOKEN`.

### 3. Import the workflow

**Workflows → Import from File** → `n8n/gmail-to-ingest.json`.

### 4. Connect Gmail

Open the **Every 5 minutes** node → **Credential to connect with** → **Create new**.
n8n walks you through Google's OAuth screen.

Sign in as **hardikshah1165@gmail.com** — the test inbox, not the client's. Swapping to
his official mailbox at go-live is changing this one credential.

### 5. Test before activating

Send yourself an email with a price list, then press **Execute workflow** — not
**Activate**. That runs it once, over the unread mail, with every node's input and
output visible.

Look at **POST /ingest**. Its reply is the backend telling you what it did:

```
processed as sell: 219 rows, 219 inserted, 0 updated, 0 refreshed, 0 closed (status applied)
```

If it says `unclassified` or `needs_sender_review`, that is not a failure — the email is
stored and waiting in the dashboard's **Needs review** tab.

### 6. Activate

Toggle **Active**. It will poll every five minutes from then on.

## Things that will come up

**Nothing arrives.** The trigger only looks at *unread* mail, and opening the message in
Gmail marks it read. Mark it unread again, or drop the `readStatus` filter in the trigger
node.

**401 from /ingest.** `TVD_INGEST_TOKEN` and Render's `INGEST_TOKEN` are different
strings. They are compared exactly — a trailing space counts.

**503 from /ingest.** `INGEST_TOKEN` is not set on Render at all. The endpoint refuses
rather than accepting everything, because an open ingest that shipped unnoticed is worse
than an outage.

**The first request after a quiet night is slow.** Render's free tier sleeps. The node
allows 120 seconds and retries; the cold start fits inside that.

**The same email processed twice.** It cannot double the board — `/ingest` deduplicates
on `message_id`, and a redelivery returns `duplicate` without touching anything. That
guard matters more than it sounds: reconciling a second time would see the offers it had
just created as already live, and close everything missing from the second copy.

**A spreadsheet fails to parse.** The extract node is set to continue on failure, so the
email still arrives with its covering note. Fewer rows is visible in the review queue; an
email that never arrives is not visible anywhere.
