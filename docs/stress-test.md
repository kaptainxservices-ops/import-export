# Stress test — three files and three typed emails

Six emails. The first three carry the attachments and HTML tables that real suppliers
send, which is how roughly nine out of ten lists arrive; the last three are typed into
the body, which is how the rest arrive and where the awkward cases live.

Send them from an **outside** address (your personal Gmail) **to**
`kaptainx.services@gmail.com`, which forwards to the collector. Send them **one at a
time, a minute apart** — if something breaks you want to know which one did it.

---

## Before you send anything

Each email carries a forwarded header block naming a different supplier, so one sending
address stands in for six. That only works if the system treats your sending address as
one of **yours** — otherwise rule 1 of sender resolution fires, every email is filed
against your Gmail, and all six collapse into a single counterparty.

So add the test address to the tenant's own addresses first:

```sql
-- Check what is there now, and confirm the tenant id is the right one.
select id, name, internal_domains, internal_addresses from public.tenants;

-- Add the test sender. `array_append` is a no-op guard against running it twice
-- only if you check first, so this uses a filter instead.
update public.tenants
set internal_addresses = internal_addresses || array['kaptainx.services@gmail.com']
where id = 'b9316981-b732-4891-854f-3335205dd582'
  and not ('kaptainx.services@gmail.com' = any(internal_addresses));
```

The column is `internal_addresses` on `public.tenants`. `own_addresses` is a local
variable inside `supabase/setup_tenant.sql` and is not a column — a query against that
name fails with `column "own_addresses" does not exist`.

**Take it out again when the test is finished.** Leaving it in means a real email from
that address would be treated as internal mail rather than as a supplier.

```sql
update public.tenants
set internal_addresses = array_remove(internal_addresses, 'kaptainx.services@gmail.com')
where id = 'b9316981-b732-4891-854f-3335205dd582';
```

The three files are in `samples/stress/`. Attach them from there.

---

## 1 — Almanar · a seller's workbook

**Attach:** `almanar-stock-14-10.xlsx`
**Subject:** `WTS Stock list 14.10.2026`

**Body:**

```
---------- Forwarded message ---------
From: Almanar Trading FZE <sales@almanar.example>
Date: Tue, 14 Oct 2026 at 08:12
Subject: WTS Stock list 14.10.2026
To: <sales@tvdservices.com>

Dear partners,

Please find our stock list attached. EXW Dubai, prices in EUR, valid 24 hours.
Minimum order 10 pcs per line.

Best regards
Almanar Trading FZE
```

**What the file is built to break**

The sheet opens with **six rows of letterhead and trading terms** before the header, so
nothing can be assumed about where the columns are named — the header has to be found by
scoring every row. The price column is headed `Price EUR`, which is where the currency
for all 59 rows comes from.

Inside the rows: `Ent. Ed.` and `OM` on the same model at different prices, `1TGB` (one
supplier's consistent typo for 1TB), `1000GB`, `6GB RAM 128GB` where the first number is
memory and not storage, a bare `256` with no unit, an EAN carrying Excel's leading
apostrophe, and three rows with the price cell left empty. A second sheet,
**ACCESSORIES**, holds 10 rows.

**Expect**

| | |
|---|---|
| Sellers | **69 rows** — 59 from STOCK LIST, 10 from ACCESSORIES |
| Currency | EUR on every row, read from the header |
| Accessories | all 10 categorised `accessory`, none as a phone |
| Capacities | 128, 256, 512 and 1024 all present — the 1024s are the `1TGB` and `1000GB` rows |

The second sheet is the thing to actually look at. Taking only the first sheet of a
workbook would silently lose those 10 rows, and nothing anywhere would say so.

---

## 2 — Meridian · a buyer's workbook with capacity in its own column

**Attach:** `meridian-buying-14-10.xlsx`
**Subject:** `WTB Buying list 14.10.2026`

**Body:**

```
---------- Forwarded message ---------
From: Meridian Wireless <buying@meridianwireless.example>
Date: Tue, 14 Oct 2026 at 10:15
Subject: WTB Buying list 14.10.2026
To: <sales1@tvdservices.com>

Hello,

Our requirements are attached. Payment 30 days against documents, EU spec only.
Please quote your best on anything you can cover.

Meridian Wireless Ltd
```

**What the file is built to break**

The descriptions say `Apple iPhone 16` and nothing more. Capacity lives in a column
headed **`Memory`**, colour in one headed `Color`, and the target price in `Target
price` with the currency in its own column beside it. Nothing about the product is in
the product name.

That matters more than it sounds. Capacity is part of identity, so if the `Memory`
column is not read, the 128, 256 and 512 requirements for one model are the same
identity — they overwrite each other on reconciliation and two of the three never get
matched against anything. This is what the sheet exists to check.

The cells are deliberately inconsistent about how they write it: `128GB`, `8/256GB`,
`8/256`, `512GB`, and one row saying `Any`.

**Expect**

| | |
|---|---|
| Buyers | **20 rows** |
| Capacity | **18 of 20** — the two without are the Apple Watch row and the one that says `Any` |
| Currency | EUR on every row, from the currency column |
| Matches | several against email 1, most obviously the iPhone 16 128GB Black and the A37 |

---

## 3 — Landotech · an HTML table in the body

**Attach:** nothing. **Paste the contents of `landotech-table.html`** into the email as
formatted HTML — open the file in a browser, select all, copy, paste into Gmail's
compose window. The table must arrive as a table, not as text.

**Subject:** `Fwd: WTS stock list 14/10 - EXW Warsaw`

**What the file is built to break**

This is the shape most suppliers' lists actually arrive in, and it is where table
extraction has the most to get wrong:

- The real table sits inside a **layout table** — the wrapper every mail client adds.
  Reading the wrapper gives one giant cell and no rows at all.
- **The header repeats partway down**, as it does whenever two exports are pasted under
  one another. The repeat must be skipped, not read as stock.
- The last row is **`TOTAL`**. It is not stock either.
- The price column is headed only **`USD`** — read literally that is a currency column
  with no price anywhere, and the whole table becomes unparseable.
- Prices are European: `1.045,00` is a thousand and forty-five, not one.
- Brands appear **only as section headings** — `SAMSUNG`, `APPLE`, `OTHER BRANDS`. Every
  row under `SAMSUNG` reads `A17 5G DS SM-A176 128GB`, with no brand on the line.
- RAM has its own column, and the descriptions also contain the words `RAM` in them.
- One colour cell reads `Black / Teal / White` — one product in three finishes.
- There is a **mobile hotspot** in the list, and a **Galaxy Watch**, both carrying a
  handset brand and an LTE badge.

**Expect**

| | |
|---|---|
| Sellers | **22 rows**, and 2 skipped — the repeated header and the `TOTAL` line |
| Currency | USD on every row |
| Brand | Samsung on the first six rows, from the section heading alone |
| Categories | 13 phone, 4 accessory, 1 tablet, 1 audio, 1 wearable, 2 uncategorised |
| Uncategorised | the ZTE hotspot and the Honor Magic V6 — both correct, see below |

The ZTE row is a **mobile hotspot**, not a handset, and must not be filed as a phone
just because it says ZTE and 4G LTE. The Honor Magic V6 has nothing on its line that
distinguishes a phone from anything else the brand sells, so no category is the honest
answer; it still matches, because category never blocks a match.

---

## 4 — Erregame · retyped, no address anywhere

**Subject:** `Erregame Offer`

```
WTS

iPhone 16e 128gb black 30pcs 459
iPhone 16e 256gb white 18pcs 519
iPad Air 11 M3 128gb 10pcs 599

all eu spec, ready stock
```

**Exercises:** the pasted-from-WhatsApp case — no forwarded block, no address, the
company name only in the subject. About 1 email in 5 in the real corpus.

**Expect:** lands in **Review**, not on the board, with "Erregame" suggested as the
sender. Resolve it there by attributing it to `sales@erregame.example`.

**Then send it again**, changing the subject to `Erregame Offer 2`. The second one
should go **straight through** without review, attributed to Erregame by name. That is
the feature that turns a daily click into a one-time setup, and this is the only way to
watch it work.

---

## 5 — Kowloon · the classification trap

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
nothing, and the signature contains *"price list available on request"*, which matches a
selling rule perfectly. This email is a **request**, and reading it as an offer would
put a buyer's demand on the board as supply.

**Expect:** 2 rows on **Buyers**. If they land on Sellers, the rules and the model
disagreed and something regressed.

---

## 6 — Gulf Cell · the incomplete one

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

**Exercises:** rows with no quantity, no price and no capacity. These should be flagged
rather than put on the live board as though they were real requirements.

**Expect:** rows flagged **needs review**, visible under Review, and the import card
showing a verdict of *check* or *suspect* rather than *healthy*.

---

## What to check when all six are in

| Where | What |
|---|---|
| **Sellers** | 91 rows from emails 1 and 3 |
| **Sellers**, category `phone` | no cables, cases, glass or power adapters, and no hotspot |
| **Buyers** | 22 rows — 20 from email 2, 2 from email 5 |
| **Matches** | several, pairing email 2's requirements against emails 1 and 3 |
| **Review** | email 4's first send, and email 6's vague rows |
| **Counterparties** | 6 distinct suppliers, each with a real name, none of them your Gmail |

### The four checks worth doing by hand

**Did both A37 rows survive, in both lists?** `Ent. Ed.` and `OM` are different stock at
different prices, and emails 1 and 3 both list the pair. If only one of each is there,
identity is collapsing and one supplier's price is silently vanishing.

**Do the three iPhone 16 capacities on Buyers have three different capacities?** That is
the `Memory` column doing its job. If they all read the same, or none, the column is
being ignored and two of those three requirements can never match.

**Did the second Erregame email skip the review queue?** The one feature with no other
way to observe it.

**Is the ACCESSORIES sheet on the board?** 69 rows from email 1, not 59. A workbook whose
second sheet is quietly dropped looks exactly like a workbook that only had one.

---

## If something is wrong

Check **Review** before assuming a parse failure. An email that lands there is the
system refusing to guess, which is correct behaviour and not a bug.

The n8n execution log shows what each email actually became, and the `/ingest` reply says
what the backend did with it — `processed as sell: 69 rows, 69 inserted` or
`needs_sender_review`. Start there rather than from the board: it tells you whether the
email arrived, whether it was understood, and what was stored, in that order.
