# The collector mailbox

One mailbox that every staff address forwards into, and the only mailbox the system
ever connects to.

---

## Why — the short version

**Because four of the five mailboxes cannot be read any other way, and the fifth would
cost weeks of paperwork.**

---

## Why — the long version

The obvious design is to connect all five staff mailboxes directly. We looked at that
properly before choosing. It does not work.

### Outlook is closed

Microsoft **has already disabled** Basic authentication for IMAP and POP in every
Exchange Online tenant. Not "is deprecating" — it is off, and
[Microsoft's own documentation](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)
states that nobody, including Microsoft support, can switch it back on. App passwords
stopped working at the same time. Consumer `@outlook.com` followed.

So `tvdlogistics@outlook.com` cannot be read with a username and password at all. It
would need a Microsoft Entra app registration, an OAuth consent flow, and someone
maintaining refresh tokens. And because it is a *personal* Outlook account rather than a
mailbox inside a company tenant, it does not even qualify for the clean administrative
routes that exist for business tenants.

### Gmail is expensive to automate

`gmail.readonly` is what Google calls a **restricted scope**. To use it in production,
an app must pass Google verification *and* an annual third-party CASA security
assessment. That is weeks of work and real money.

The alternative is leaving the app in "Testing" mode — where the refresh token expires
**every 7 days**. That means re-authorising every mailbox, every week, forever.

### What that adds up to

| Direct connection | Collector mailbox |
|---|---|
| 5 mailboxes | 1 mailbox |
| 2 providers | 1 provider |
| 2 app registrations | 0 |
| Google verification + CASA | none |
| Tokens to maintain forever | one app password that does not expire |
| Weeks | an afternoon |

### It also happens to be what the client already does

The staff already forward supplier emails around by hand. We are not introducing a new
habit — we are pointing an existing one at a mailbox that does something useful with it.

And the system already reads forwarded mail correctly. `resolve_sender` has a dedicated
path that opens the forwarded header block and pulls out the original supplier. On the
real corpus, **30 of 45 emails** resolve to the genuine supplier that way.

---

## What you are building

```
info@tvdservices.com      ─┐
sales@tvdservices.com     ─┤
sales1@tvdservices.com    ─┼──forward──▶  collector Gmail  ──IMAP──▶  n8n  ──▶  the board
tvdlogistics@outlook.com  ─┤
(the fifth address)       ─┘
```

Nobody reads the collector by hand. It exists so that software has exactly one door to
knock on.

---

## Setup

### Step 1 — Create the account

A **new** Gmail account, not a personal one. Something obvious:
`tvd.board.ingest@gmail.com`.

It will hold a copy of every supplier price list the client receives, so it is a
business asset. Put the password in a password manager, not in a chat.

### Step 2 — Turn on 2-Step Verification

<https://myaccount.google.com/signinoptions/twosv>

Google will not offer app passwords until this is on. There is no way round it.

### Step 3 — Turn on IMAP

Gmail → ⚙ → **See all settings** → **Forwarding and POP/IMAP** → **Enable IMAP** → Save.

### Step 4 — Create the app password

<https://myaccount.google.com/apppasswords>

Name it `n8n`. Google shows a **16-character** password once and never again. Copy it
straight into a password manager.

This is the only mail credential the system ever holds. It is not the account password,
it only grants mail access, and it can be revoked from that page without changing
anything else.

> **Send me the email address. Never send me the app password** — put it into n8n
> yourself when we get there.

### Step 5 — Stop anything being marked spam

This step is not optional and it is the one people skip.

Forwarding breaks SPF — the email now arrives from a server that is not authorised to
send for the supplier's domain — so forwarded supplier mail often lands in **Spam**. The
IMAP connection only reads **INBOX**. Anything in Spam is invisible, silently, forever.

Gmail → ⚙ → See all settings → **Filters and Blocked Addresses** → **Create a new
filter**:

- **From:** `info@tvdservices.com OR sales@tvdservices.com OR sales1@tvdservices.com OR tvdlogistics@outlook.com OR tvdservices@hotmail.com`
- Continue → tick **Never send it to Spam** → Create filter

### Step 6 — Forward the three `@tvdservices.com` mailboxes

Do this in each of `info@`, `sales@` and `sales1@`.

**If tvdservices.com is on Google Workspace:**

⚙ → See all settings → **Forwarding and POP/IMAP** → **Add a forwarding address** → the
collector address.

Google sends a confirmation code to the collector. Open the collector, click the link,
then return to the source mailbox and select **Forward a copy of incoming mail to…**,
leaving "keep Gmail's copy in the Inbox" selected so staff still see their own mail.

**If tvdservices.com is on Microsoft 365:**

Settings → Mail → **Rules** → Add new rule → condition **Apply to all messages** →
action **Redirect to** → the collector address.

Use **Redirect to**, not "Forward to" — see the box below.

> ⚠️ **An administrator can block forwarding to outside addresses**, on either platform.
> If it is blocked, this whole plan stops and we go back to per-mailbox OAuth. Check
> this before promising the client a date.

### Step 7 — Redirect `tvdlogistics@outlook.com`

Outlook → ⚙ → **Mail** → **Rules** → **Add new rule**

- Name: `Forward to board`
- Condition: **Apply to all messages**
- Action: **Redirect to** → the collector address
- Save

> **Redirect, not Forward.** Redirect passes the message through with the original
> `From:` header untouched, so the system reads the real supplier straight off the
> envelope at full confidence. "Forward" wraps the message in a new one from the staff
> member, which drops it to the forwarded-header path at 90% confidence. Both work. One
> is better and costs nothing.

### Step 8 — Test each path

Send a test email **from an outside address** (your personal Gmail) to each of the five
mailboxes in turn. For each, check the collector and confirm:

- [ ] it arrived in **INBOX**, not Spam
- [ ] the **From** shows the outside address, not the staff member
- [ ] it arrived within a minute or so

If the From shows the staff member instead, that mailbox is using Forward rather than
Redirect. Fix it now — it is much harder to spot later.

---

## When it is done, send me

1. The collector email address
2. Which of the five paths you tested and what the From showed
3. Whether `tvdservices.com` is Google Workspace or Microsoft 365
4. The fifth staff address, once Chandan gives it to you

Then I add the collector to the tenant config and we move to the n8n side
(`n8n/README.md`).

---

## One configuration detail that matters more than it looks

The collector relays **everything**. Unless it is listed in the tenant's
`internal_addresses`, the system will treat it as an outside sender and file every
single offer against it — the collector would appear as the client's largest supplier by
a wide margin.

This has already happened once. Before the fix, `internal_domains` was empty and
**4,539 of roughly 4,800 offers** were filed against the client's own staff.
`sales@tvdservices.com` alone held 4,161, as though a colleague were the biggest
supplier in Europe. One empty array.

So: the collector address goes into `supabase/setup_tenant.sql` the moment it exists,
and `setup_tenant.sql` gets run again. Not later.

---

## Things that will go wrong

**"Google won't let me create an app password."** 2-Step Verification is not on. Step 2.

**"The forwarding confirmation code never arrived."** Check the collector's Spam. This
is the one email that arrives before the never-spam filter can help, because it comes
from Google rather than from one of the five addresses.

**"Mail is arriving but n8n sees nothing."** Almost always Spam. Step 5.

**"Everything is attributed to the wrong person."** Either the tenant config is missing
an address, or an Outlook rule is using Forward instead of Redirect.

**"Staff say they stopped getting their own mail."** Redirect on Outlook moves the
message rather than copying it. If they want to keep a copy, use a rule with **Forward
to** plus keeping the original, and accept 90% confidence instead of 100%. Worth asking
them which they prefer before switching anything on.
