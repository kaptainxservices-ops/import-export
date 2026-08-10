# Deploying to Render

The backend is a single web service. `render.yaml` describes it, so Render can read the
configuration from the repo rather than having it typed into a form and forgotten.

## First deploy

1. **Push to GitHub** — Render deploys from the repo, so anything uncommitted does not
   exist as far as it is concerned.

2. **Render → New → Blueprint** → connect the `import-export` repo. It reads
   `render.yaml` and proposes one service, `import-export-backend`.

3. **Set the secrets.** Everything marked `sync: false` in the blueprint has to be
   entered in the dashboard — deliberately, so no secret is ever in the repo:

   | Key | Value |
   |---|---|
   | `INGEST_TOKEN` | the same value as your local `.env` |
   | `SUPABASE_URL` | `https://<project>.supabase.co` |
   | `SUPABASE_SECRET_KEY` | the `sb_secret_…` key |
   | `ANTHROPIC_API_KEY` | once it exists; the service runs without it |
   | `SENTRY_DSN` | optional, phase 7 |

4. **Deploy**, then check:

   ```
   https://<service>.onrender.com/health        -> {"status":"ok", ...}
   https://<service>.onrender.com/health/ready  -> {"status":"ok","database":"reachable"}
   ```

   `/health` only says the process is alive. `/health/ready` also proves it can reach
   Supabase, which is the check that actually tells you the deploy is usable.

5. **Prove `/ingest` from outside**, using the real token:

   ```powershell
   curl.exe -X POST https://<service>.onrender.com/ingest `
     -H "Content-Type: application/json" `
     -H "X-Ingest-Token: <your token>" `
     -d '{\"tenant_id\":\"<a real tenant uuid>\",\"message_id\":\"<probe@test>\",
          \"from_email\":\"probe@example.test\",\"subject\":\"WTS test\",
          \"received_at\":\"2026-08-07T09:00:00Z\",\"body_text\":\"iPhone 15 128GB Black 579 EUR\"}'
   ```

   Expect `202`. Without the header, expect `401` — worth checking, because that
   endpoint is a public URL and the token is the only thing in front of it.

## Two things that bite

**Liveness and readiness are separate on purpose.** Render restarts a service whose
health check fails. If `/health` depended on Supabase, a brief Supabase outage would
restart the backend repeatedly while emails piled up, turning a blip into an incident.
`/health` answers only for the process; `/health/ready` is what you and UptimeRobot
watch.

**The free plan spins down when idle.** A cold start takes tens of seconds, and n8n
will time out and mark the delivery failed. `render.yaml` therefore specifies `starter`
at $7/month. If you drop to free for a demo, expect the first email of the morning to
fail and n8n to retry it.

## Costs

| | |
|---|---|
| Render starter | $7/mo |
| Supabase | free tier is sufficient at this volume |
| Anthropic | usage-based; set a spend limit before creating the key |
