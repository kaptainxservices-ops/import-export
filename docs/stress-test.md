# Stress test — six emails

Send each from an **outside** address (your personal Gmail) **to**
`kaptainx.services@gmail.com`, which forwards to the collector.

Most carry a forwarded header block, so one sending address simulates six different
suppliers — and that is the production path anyway, since every real email arrives
through a forward.

Send them **one at a time**, a minute apart. If something breaks you want to know which
one did it.

---

## S1 — Vadimpex · the asterisk convention

**Subject:** `WTS Pricelist 14.10`

```
---------- Forwarded message ---------
From: Vadimpex Sales <sales@vadimpex.example>
Date: Tue, 14 Oct 2026 at 08:12
Subject: WTS Pricelist 14.10
To: <sales@tvdservices.com>

*WTS*  PRICES IN EUR

Samsung A27 5G DS 6/128GB A276 *67* Black *179€*
Samsung A37 5G DS 8/256GB A376 *409* Charcoal *229,50€*
Samsung S25 Ultra DS 12+512 S938 *12* Titanium Grey *1.045€*
Apple iPhone 16 128GB Black *50* *679€*
Apple iPhone 17 Pro Max 256 Cosmic Orange *8* *1.349€*
Xiaomi Redmi Note 14 8/256 *120* Blue *189€*

EXW Rotterdam. Prices valid today only.
```

**Exercises:** the `*288*` quantity / `*259€*` price convention · `6/128` and `12+512`
and `8/256` capacity forms · **bare `256`** with no unit · European decimal comma
(`229,50`) and thousands dot (`1.045`) · currency declared once at the top · EXW ·
four brands in one list.

**Expect:** 6 rows on **Sellers**, all with quantity and price, capacities 128/256/512,
currency EUR on every row, incoterm EXW.

---

## S2 — WESTech · the one designed to trip the category gate

**Subject:** `Fwd: WESTech stock 14/10`

```
---------- Forwarded message ---------
From: WESTech Slovakia <b2bint@westech.example>
Date: Tue, 14 Oct 2026 at 09:03
Subject: WESTech stock 14/10
To: <info@tvdservices.com>

Apple iPhone 15 128GB Blue A/A+ grade ZP/A    40pcs   519 USD
Apple iPhone 15 Pro 1TGB Natural Titanium     12pcs   899 USD
Samsung A56 5G Ent. Ed. 8+128 Graphite        200pcs  245 USD
Samsung A56 5G OM 8+128 Graphite              150pcs  229 USD
Google Pixel 10 6GB RAM 128GB Obsidian        30pcs   549 USD
Nothing Phone 3a 1000GB Black                 15pcs   399 USD
'8806095258799  Samsung S24 256GB Onyx        25pcs   519 USD

Accessories:
USB-C to Lightning cable 2m                   500pcs  3,50 USD
Silicone case for iPhone 16 Pro, clear        300pcs  2,20 USD
Screen protector film iPhone 15/16            800pcs  0,90 USD
Samsung S Pen replacement                     100pcs  12 USD

FOB Bratislava
```

**Exercises:** grade `A/A+` · region `ZP/A` · the `1TGB` typo (should read 1024) ·
`Ent. Ed.` vs `OM` **as separate identities at different prices** · `6GB RAM 128GB`
(the first number is memory, not storage) · `1000GB` → 1024 · an EAN with Excel's
leading apostrophe · FOB · and four accessory lines that **must not** be filed as
phones.

**Expect:** 7 phones + 4 accessories. The two A56 rows must be **two separate offers**,
not one overwriting the other. Filter Sellers by category `phone` — the cable, case,
film and S Pen must **not** appear.

---

## S3 — Erregame · retyped, no address anywhere

**Subject:** `Erregame Offer`

```
WTS

iPhone 16e 128gb black 30pcs 459
iPhone 16e 256gb white 18pcs 519
iPad Air 11 M3 128gb 10pcs 599

all eu spec, ready stock
```

**Exercises:** the pasted-from-WhatsApp case — no forwarded block, no address, company
name only in the subject. This is 1 email in 5 in the real corpus.

**Expect:** lands in **Review**, not on the board, with "Erregame" suggested as the
sender. Resolve it there by attributing it to `sales@erregame.example`.

**Then send it again** (change the subject to `Erregame Offer 2`). The second one should
go **straight through** without review, attributed to Erregame by name. That is the
feature that turns a daily click into a one-time setup — and this is the only way to
see it work.

---

## B1 — the deliberate match

**Subject:** `WTB iPhone 16 / A37`

```
---------- Forwarded message ---------
From: Meridian Wireless <buying@meridianwireless.example>
Date: Tue, 14 Oct 2026 at 10:15
Subject: WTB iPhone 16 / A37
To: <sales1@tvdservices.com>

WTB

Apple iPhone 16 128GB Black   40pcs   up to 690 EUR
Samsung A37 5G 8/256 Charcoal 300pcs  up to 235 EUR
Apple iPhone 15 128GB Blue    25pcs   up to 530 USD

Payment 30 days. EU spec only.
```

**Exercises:** the whole point of the product. Each line is deliberately the same
product as one in S1 or S2, at a price a little above the asking price.

**Expect:** 3 rows on **Buyers**, and — the thing to actually check — **three matches on
the Matches tab**, pairing these with the S1 and S2 offers.

---

## B2 — the classification trap

**Subject:** `RE: stock`

```
---------- Forwarded message ---------
From: Kowloon Digital <purchase@kowloondigital.example>
Date: Tue, 14 Oct 2026 at 11:40
Subject: RE: stock
To: <sales@tvdservices.com>

Hi,

Looking for the below, please quote your best:

Google Pixel 10 128GB  — 200pcs
Xiaomi Redmi Note 14 256GB — 500pcs

Our own price list is available on request if you have buying interest.

Best regards
Kowloon Digital Trading
```

**Exercises:** the trap written into the classifier's own comments. The subject says
nothing (`RE: stock`), and the signature contains *"price list available on request"* —
which matches a selling rule perfectly. This email is a **request**, and reading it as an
offer would put a buyer's demand on the board as supply.

**Expect:** 2 rows on **Buyers**. If they land on Sellers, the rules and the model
disagreed and something regressed.

---

## B3 — the incomplete one

**Subject:** `WTB urgent`

```
---------- Forwarded message ---------
From: Gulf Cell Trading <trading@gulfcell.example>
Date: Tue, 14 Oct 2026 at 12:05
Subject: WTB urgent
To: <tvdlogistics@outlook.com>

WTB urgently:

iPhone 17 Pro Max — any capacity, any colour
Samsung flagship, good quantity
Pixel — need pricing

Call me.
```

**Exercises:** rows with no quantity, no price and no capacity. The confidence scorer
should flag these rather than put half-empty rows on the live board.

**Expect:** rows flagged **needs review**, visible under Review, and the import card
showing a verdict of *check* or *suspect* rather than *healthy*.

---

## What to check when all six are in

| Where | What |
|---|---|
| **Sellers** | ~13 phone rows from S1 and S2, with quantities, prices and capacities |
| **Sellers**, category = phone | cable, case, film and S Pen **absent** |
| **Buyers** | 5 rows — 3 from B1, 2 from B2 |
| **Matches** | at least 3, pairing B1's lines with S1 and S2 |
| **Review** | S3 first send, and B3's vague rows |
| **Counterparties** | 5 distinct suppliers, each with a real name |

The two single most informative checks:

**Did the two Samsung A56 rows both survive?** `Ent. Ed.` and `OM` are different stock at
different prices. If only one is there, identity is collapsing and one supplier's price
is silently vanishing — the exact bug the identity work was for.

**Did the second Erregame email skip the review queue?** That is the one feature with no
other way to observe it.

---

## If something is wrong

Check **Review** before assuming a parse failure. An email that lands there is the
system refusing to guess, which is correct behaviour and not a bug.

The n8n execution log shows what each email actually became, and the `/ingest` reply
says what the backend did with it — `processed as sell: 6 rows, 6 inserted` or
`needs_sender_review`. Start there rather than from the board.
