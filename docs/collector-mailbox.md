# The collector mailboxes

**One collector per staff member.** Each person's mail forwards into a mailbox that is
theirs, and that mailbox is the only thing the system ever connects to.

> **Changed from the first version of this document.** That one described a single
> shared collector. It was wrong in a way worth recording: with one box, anybody who
> can open it reads everybody's forwarded mail. Sales sees what Logistics received. The
> whole point of letting staff see what is being collected is defeated by the thing
> that lets them see it.

---

## Why forward at all

**Because four of the five mailboxes cannot be read any other way.**

### Outlook is closed

Microsoft **has already disabled** Basic authentication for IMAP and POP in every
Exchange Online tenant. Not "is deprecating" — it is off, and
[Microsoft's own documentation](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)
says nobody, including Microsoft support, can switch it back on. App passwords stopped
working at the same time. Consumer `@outlook.com` followed.

So `tvdlogistics@outlook.com` cannot be read with a username and password at all. It
would need a Microsoft Entra app registration, an OAuth consent flow, and somebody
maintaining refresh tokens — and being a *personal* Outlook account rather than a
mailbox inside a company tenant, it does not even qualify for the clean administrative
routes that exist for business tenants.

### Gmail is expensive to automate

`gmail.readonly` is what Google calls a **restricted scope**. Using it in production
needs Google verification *and* an annual third-party CASA security assessment — weeks
of work and real money. The alternative is leaving the app in "Testing", where the
refresh token expires **every 7 days**: re-authorising every mailbox, every week,
forever.

### What that adds up to

| Reading the mailboxes directly | Forwarding to collectors |
|---|---|
| 2 providers, 2 app registrations | none |
| Google verification + CASA | none |
| Tokens to maintain forever | app passwords, which do not expire |
| Weeks | an afternoon |

### And it is already how they work

The staff forward supplier emails around by hand today. We are not introducing a habit,
we are pointing an existing one somewhere useful. The system already reads forwarded
mail correctly: on the real corpus, **30 of 45 emails** resolve to the genuine supplier
through the forwarded-header path.

---

## Why one each, rather than one shared

Three reasons, in order of weight.

**A shared box leaks between colleagues.** Give one person access and they can read
everyone's forwarded mail. That is a privacy problem *inside* the client's own team,
and it is the reason this document changed.

**Transparency only works if they can look.** Under GDPR the staff must be informed
about what is collected. A mailbox they own and can open at any time makes that
structural rather than a promise in a policy nobody reads.

**Revocation becomes clean.** One person objects, or leaves — their collector is
switched off without touching anybody else's.

It costs nothing in money. Five free Gmail accounts, and self-hosted n8n has no
execution limit, so five IMAP triggers run on the same server as one. The cost is
about two extra hours of setup.

---

## Why a collector can never be an Outlook account

The collector is **the one mailbox the system has to read**, and reading means IMAP —
which Microsoft has closed. So:

- `tvdlogistics@outlook.com` as a **source**: fine. It only forwards.
- An `@outlook.com` account as a **collector**: impossible. n8n cannot read it.

Every collector must be Gmail, Google Workspace, or another provider that still issues
app passwords (Zoho, Fastmail). Never Microsoft.

**This is why Chandan's answer matters.** If `tvdservices.com` is on Google Workspace,
the collectors can live on the client's own domain — company-owned, admin-controlled,
data never leaving their domain, at the cost of extra Workspace seats. If it is on
Microsoft 365, that option does not exist at all, because those mailboxes cannot be
read by IMAP either, and the collectors have to be Gmail accounts instead.

---

## What you are building

```
info@tvdservices.com      ──filter──▶  collector 1  ─┐
sales@tvdservices.com     ──filter──▶  collector 2  ─┤
sales1@tvdservices.com    ──filter──▶  collector 3  ─┼─IMAP─▶ n8n ─gate─▶ the board
tvdlogistics@outlook.com  ──filter──▶  collector 4  ─┤
tvdservices@hotmail.com   ──filter──▶  collector 5  ─┘
```

Each staff member has access to their own collector and to nobody else's.

Note the **two filters**. One at the source, so private mail never leaves the staff
mailbox. One in n8n, so anything that slips through never reaches the database. They
are not redundant — see below.

---

## Who owns the accounts

**The client, not you.** These will hold a copy of every supplier price list TVD
receives. Gmail accounts in your own name holding a client's commercial data is
awkward on day one and much worse the day the contract ends.

Have Samir or Chandan create them, and have them hand you the app passwords.

---

## Setup

Repeat steps 1–5 for each of the five staff members.

### Step 1 — Create the collector

A new Gmail account named after the person it serves, so nobody has to guess which is
which:

```
tvd.feed.info@gmail.com        for info@tvdservices.com
tvd.feed.sales@gmail.com       for sales@tvdservices.com
tvd.feed.sales1@gmail.com      for sales1@tvdservices.com
tvd.feed.logistics@gmail.com   for tvdlogistics@outlook.com
tvd.feed.hotmail@gmail.com     for tvdservices@hotmail.com
```

Give the staff member the password. It is their mailbox — that is the point.

### Step 2 — Turn on 2-Step Verification

<https://myaccount.google.com/signinoptions/twosv>

Google will not offer app passwords without it. There is no way round this.

### Step 3 — Turn on IMAP

Gmail → ⚙ → **See all settings** → **Forwarding and POP/IMAP** → **Enable IMAP** → Save.

### Step 4 — Create the app password

<https://myaccount.google.com/apppasswords>

Name it `n8n`. Google shows a **16-character** password once and never again.

This is the only mail credential the system holds for that person. It is not the
account password, it grants mail access only, and it can be revoked from that page
without changing anything else.

> **Send me the email addresses. Never send me the app passwords** — put them into n8n
> yourself when we get there.

### Step 5 — Stop anything being marked spam

Not optional, and the step people skip.

Forwarding breaks SPF — the mail now arrives from a server not authorised to send for
the supplier's domain — so forwarded supplier mail often lands in **Spam**. The IMAP
connection reads **INBOX** only. Anything in Spam is invisible, silently, forever.

Gmail → ⚙ → See all settings → **Filters and Blocked Addresses** → **Create a new
filter** → From: the one staff address that forwards here → Continue → tick **Never
send it to Spam** → Create filter.

---

## Step 6 — Forward, with a filter

This is where privacy is actually won, so it is worth doing properly.

**Do not forward everything.** Forward only what looks like trade mail, using a rule on
the *staff member's own* mailbox. Private, HR and banking mail then never leaves it.

Keep the rule deliberately loose:

> has an attachment
> **OR** subject contains `WTS` `WTB` `offer` `stock` `price` `list` `RFQ` `quote`
> **OR** the sender is a known supplier domain

Loose in that direction because the two mistakes are not equal. A price list the filter
drops is **invisible** — nobody knows to look for an email that never arrived. Junk that
gets through costs one database row, and n8n's gate catches most of it anyway.

**Gmail / Workspace source:** ⚙ → See all settings → **Filters and Blocked Addresses**
→ create a filter matching the rule above → **Forward it to** the collector. Add the
collector under **Forwarding and POP/IMAP** first; Google emails it a confirmation code
that you must click.

**Microsoft 365 source:** Settings → Mail → **Rules** → Add new rule, with the same
conditions, action **Redirect to**.

> ⚠️ **An administrator can block forwarding to outside addresses**, on either
> platform. If it is blocked this whole plan stops and we are back to per-mailbox
> OAuth. Check before promising the client a date.

### Step 7 — The Outlook mailboxes: forward or redirect?

For `tvdlogistics@outlook.com` and `tvdservices@hotmail.com`:

| | Keeps their own copy | Sender read at |
|---|---|---|
| **Redirect to** | likely **no** | 100% confidence |
| **Forward to** | yes | 90% confidence |

Redirect passes the message through with the original `From:` intact, which is better
for us. But it generally does **not** leave a copy in the sender's own mailbox — and
these are mailboxes somebody actually works in. A trader losing sight of their own
inbox is not a trade worth making for ten percentage points of confidence.

**So use Forward on these two**, unless the person says they do not mind.

Microsoft's behaviour differs between consumer Outlook and Exchange, so do not take my
word for it: set one rule, send a test, and look. That is what step 8 is for.

### Step 8 — Test every path

From an outside address (your personal Gmail), send a test to each of the five staff
mailboxes in turn. For each one, open that person's collector and check:

- [ ] it arrived in **INBOX**, not Spam
- [ ] the **From** shows the outside address, not the staff member
- [ ] the staff member still has their own copy
- [ ] it arrived within a minute or so

Then send one that should *not* pass the filter — "lunch tomorrow?" — and confirm it
does **not** appear.

---

## The second filter, in n8n

The workflow has an **Is this trade mail?** node between building the payload and
posting it. Anything that fails never reaches the database.

It has to be there rather than on the backend, and the reason is the order of
operations: **the pipeline stores an email before it classifies it.** `save_email` runs,
then the classifier concludes it is not an offer. So a bank statement "rejected" by the
backend is a bank statement stored in full, sitting in a review queue for somebody to
open. Rejected is not the same as never arrived.

The two filters compose: broad at the source so private mail never leaves, precise in
n8n so what does leave is still checked. Neither alone is enough.

---

## Retention

`supabase/migrations/0011_retention.sql` ages out what is stored — the message body
blanked after 90 days, the row deleted after 18 months once no offer or import still
points at it. It is **not scheduled automatically**; the `pg_cron` lines are in the file
ready to run once the client has agreed the windows.

TVD Services B.V. is in Rotterdam and TVD Services (UK) Ltd in Reading, so GDPR and UK
GDPR apply. Two consequences worth naming to the client: employee communications
monitoring is specifically regulated and the staff must be **informed**; and you would
be a processor to their controller, which normally means a written data processing
agreement. That document protects you as much as them. Neither of us is a lawyer — they
should get their own.

---

## The configuration detail that matters more than it looks

**All five collector addresses must go into the tenant's `internal_addresses`**, along
with `tvdservices.com` in `internal_domains`. See `supabase/setup_tenant.sql`.

A collector relays everything. Leave one off the list and the system reads it as an
outside sender and files every offer against it — that collector becomes the client's
largest supplier by a wide margin.

This has already happened once. `internal_domains` was empty and **4,539 of roughly
4,800 offers** were filed against the client's own staff, `sales@tvdservices.com` alone
holding 4,161, as though a colleague were the biggest supplier in Europe. One empty
array. Five collectors means five new ways to make the same mistake.

---

## When it is done, send me

1. The five collector addresses
2. Which paths you tested and what the From showed
3. Whether `tvdservices.com` is Google Workspace or Microsoft 365
4. Confirmation the staff were told

---

## Things that will go wrong

**"Google won't let me create an app password."** 2-Step Verification is not on. Step 2.

**"The forwarding confirmation code never arrived."** Check the collector's Spam. It is
the one email that arrives before the never-spam filter can help, because it comes from
Google rather than from the staff address.

**"Mail is arriving but n8n sees nothing."** Almost always Spam. Step 5.

**"A supplier's list never showed up."** The source filter was too narrow. Widen it —
this is the failure mode that is invisible, so check for it deliberately rather than
waiting to notice.

**"Everything is attributed to the wrong person."** A collector is missing from
`internal_addresses`, or an Outlook rule is wrapping the message.

**"Staff stopped getting their own mail."** Redirect moves rather than copies. Switch
that mailbox to Forward and accept 90% confidence. Step 7.
