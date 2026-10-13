# n8n on Render — the exact setup

Every value in one place, so none of it has to be remembered from a conversation.

---

## 1. The service

Render → **New → Web Service → Existing image**

| Field | Value |
|---|---|
| Image URL | `docker.n8n.io/n8nio/n8n` |
| Name | `n8n` |
| Region | the same one as the backend |
| Instance type | **Starter — $7/mo** |

**Not Free.** The free instance sleeps after 15 minutes idle, and a sleeping poller
reads no mail. The inbox would go quiet overnight and look like a quiet trading day.

## 2. The disk

**Disks → Add Disk**, before the first deploy.

| Field | Value |
|---|---|
| Name | `n8n-data` |
| Mount path | `/home/node/.n8n` |
| Size | 1 GB |

Render wipes the filesystem on every deploy and restart. n8n keeps its workflows, its
encrypted credentials and its execution history in a SQLite file at that exact path —
without the disk, every redeploy is an empty n8n and five app passwords to type again.

1 GB is Render's minimum, not n8n's requirement. The workflows and credentials are a
few megabytes; it is the execution history that grows, which is what the pruning
variables below are for.

## 3. Environment variables

### Identity

| Key | Value |
|---|---|
| `N8N_HOST` | the service's own hostname, e.g. `n8n-xxxx.onrender.com` |
| `N8N_PROTOCOL` | `https` |
| `N8N_PORT` | `5678` |
| `WEBHOOK_URL` | `https://<the same hostname>` |
| `GENERIC_TIMEZONE` | `Europe/Amsterdam` — the client's timezone, not yours; it is what "every morning" means in a schedule |

The hostname does not exist until the first deploy. Leave those three blank, deploy,
then come back and fill them in.

### The one that cannot be skipped

| Key | Value |
|---|---|
| `N8N_ENCRYPTION_KEY` | 64 hex characters, generated once |

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

n8n encrypts stored credentials with this. If it is not set, n8n generates one and
writes it to the disk — and the day that disk is replaced or recreated, every stored
credential becomes permanently unreadable with no error that says so. Setting it
explicitly costs one line now and saves re-entering five app passwords later.

Keep a copy somewhere other than Render.

### Where to send the mail

| Key | Value |
|---|---|
| `TVD_TENANT_ID` | `b9316981-b732-4891-854f-3335205dd582` |
| `TVD_BACKEND_URL` | `https://import-export-sno5.onrender.com` |
| `TVD_INGEST_TOKEN` | the same 43-character string as `INGEST_TOKEN` on the backend service |

The token is compared byte for byte. A trailing space counts, and the failure is a 401
that looks like a code problem.

### Keeping the disk from filling

| Key | Value |
|---|---|
| `EXECUTIONS_DATA_PRUNE` | `true` |
| `EXECUTIONS_DATA_MAX_AGE` | `168` |
| `EXECUTIONS_DATA_PRUNE_MAX_COUNT` | `1000` |
| `EXECUTIONS_DATA_SAVE_ON_PROGRESS` | `false` |

n8n saves the full data of every execution by default, and these payloads carry
base64-encoded spreadsheets — a 10 MB workbook is about 13 MB of base64 written to disk
on every run. Seven days and a thousand runs is enough to debug last week's problem and
not enough to fill a gigabyte.

### Keeping it inside its memory

| Key | Value |
|---|---|
| `NODE_OPTIONS` | `--max-old-space-size=400` |

Starter is 512 MB, which is n8n's documented minimum rather than its comfortable range.
Capping Node's heap below the container limit makes it garbage-collect instead of being
killed outright. If the logs show restarts anyway, Standard (2 GB) is the fix — but do
not pay for headroom before seeing whether it is needed.

## 4. First boot

Open the URL. n8n asks you to create an owner account — **do it immediately**. Until
somebody does, anyone who finds the URL can create it and inherit every credential
stored inside.

Then fill in `N8N_HOST` and `WEBHOOK_URL` with the real hostname and let it redeploy.

## 5. Prove the encryption key works

Before putting any real credential in: create a throwaway credential (any type, fake
values), then trigger a manual redeploy, then open it again.

If it still opens, the key is set correctly and the disk is mounted. If it errors, stop
— finding this out now with a fake credential is free; finding out later means five app
passwords and a conversation with the client.

## 6. Then

Import `n8n/imap-to-ingest.json` and follow `n8n/README.md` from "Import and connect".
