# Sample email analysis

**45 emails · 31 offers, 14 buyer requests · 7 spreadsheets, 2 PDFs, 1 photo**
Analysed 7 August 2026, against the samples in `samples/`.

The purpose of this pass was to check the build specification against reality before
writing the extraction engine. It does not match in three material ways. This document
sets out what the data actually contains, what has to change, and what needs confirming
with the client.

---

## Summary

| | Finding | Consequence |
|---|---|---|
| 1 | The business is not iPhone-only, or even phone-only | Schema and normalisation need reworking |
| 2 | The sender of an email is usually not the supplier | Counterparty attribution needs a different mechanism |
| 3 | Prices use European formatting | Current parser is wrong by up to 1000x |
| 4 | EAN barcodes are present in most structured lists | A better identity key is available for free |
| 5 | Condition grading barely appears | The hardest problem in the spec is largely absent |

---

## 1. The product range is far wider than specified

The specification describes a secondary iPhone trade. The sample describes a general
consumer electronics wholesale brokerage.

Brands appearing across the 45 emails:

| Brand | Emails | Brand | Emails |
|---|---|---|---|
| Samsung | 27 | Google / Pixel | 15 |
| Apple | 21 | Oppo | 12 |
| Motorola | 17 | Sony | 11 |
| Xiaomi / Redmi | 17 | Honor | 10 |
| | | OnePlus | 9 |

Categories are broader still. Beyond phones and tablets: Apple Watch and Amazfit
watches, AirPods and JBL audio, Nintendo Switch and PlayStation 5 consoles, a 43-inch
Sinudyne television, power banks, cases, chargers, cables and screen protectors. One
list from AB Business opens with Covid-19 antigen tests, FFP2 masks and hand sanitiser
before reaching the Samsung section.

**What this breaks.** The `offers` table has columns shaped for a phone:
`model`, `storage_gb`, `colour`, `grade`, `region_code`. A 20W car charger has none of
those. A PlayStation 5 has none of those. The current model parser is deliberately
strict — it returns nothing for anything that is not an iPhone — so on this data the
majority of rows would be silently discarded.

**What it should be instead.** A generic core that every product has — brand, category,
description, EAN, quantity, price, currency — with the phone-specific attributes
(capacity, colour, network spec, condition) as optional fields that are populated when
they apply. Matching then compares on whatever attributes both sides actually specify.

---

## 2. The email sender is usually not the supplier

This is the most operationally significant finding, because offer identity is keyed on
counterparty and the disappearance rule compares today's list against *that sender's*
previous one. Attribute an email to the wrong supplier and reconciliation silently
corrupts two suppliers' boards at once.

The 45 emails fall into three tiers:

**Tier 1 — forwarded, sender recoverable (30 emails).**
The envelope reads `sales@tvdservices.com`, but the body opens with a forwarded header
block naming the real supplier:

```
Begin forwarded message:
From: "Philip AB BUSINESS" <info@abbusiness.fr>
Subject: WANT TO SELL 27.07.2026
Date: 27 July 2026 at 1:23:27 am GMT+5:30
```

21 distinct suppliers are identifiable this way, including Metropolitan Trends
(`team@metro-trends.de`), Masterfone (`sales@master-fone.com`), Thaysen telecom,
Smalltronic, Landotech, WESTech and World Comm.

**Tier 2 — genuine external sender (3 emails).**
Belsimpel (`buy@belsimpel.nl`) and Select Tech Group arrive directly. The envelope is
the truth.

**Tier 3 — no machine-readable sender at all (12 emails).**
Emails such as `All in Srl Offer.eml`, `Reline Offer.eml`, `Vadimpex Offer.eml` and
`OT Distribution Offer.eml` were composed fresh by the client's own staff, with the
supplier's content pasted into the body. The envelope is the client's own address and
there is no forwarded header. **The supplier's name exists only in the filename**,
which does not survive as data.

For tier 3 there is no reliable automatic answer. The options are to infer the supplier
from the body text where the company signs off, or to accept that these land in a
review queue for a human to attribute in one click. Inference is worth attempting but
should never be silent.

**The question this raises:** is tier 3 how the business genuinely operates, or an
artifact of how these samples were gathered? If production mail arrives directly from
suppliers into a watched inbox, the problem largely disappears. If staff really do
re-compose offers by hand, that is a workflow change worth proposing, because no
software can reliably attribute an email that contains no sender.

---

## 3. European number formats break the current price parser

11 of the 45 emails use comma as the decimal separator, and 5 use dot as the thousands
separator. Metropolitan Trends alone has 709 comma-decimal prices in one email.

| Written | Means | Current parser reads | Error |
|---|---|---|---|
| `109,50` | €109.50 | `109` | price understated |
| `2,50` | €2.50 | `2` | price understated |
| `€1.079` | €1,079 | `1.07` | **1000x understated** |
| `1,234.50` | €1,234.50 | `1234.50` | correct |

The third row is the dangerous one. A €1,079 phone read as €1.07 does not look like a
parsing failure; it looks like an extraordinary margin, which is exactly the kind of
number a broker acts on quickly.

Both conventions appear in the same corpus, so the parser cannot simply be switched to
European mode. It has to decide per value, using the position and length of the
separator groups, and fall back to a per-supplier setting when a value is genuinely
ambiguous — `1,079` is either 1079 or 1.079 with no way to tell from the string alone.

---

## 4. EAN barcodes are present and should anchor identity

Most structured lists carry an EAN column:

| Supplier | Format | EAN present |
|---|---|---|
| Metropolitan Trends | xlsx, 710 rows | yes |
| SELTE | xlsx, 1000 rows | yes |
| World Comm | xlsx, 1396 rows | yes |
| All in Srl | xlsx, 177 rows | yes |
| Erregame | prose | yes, per line |

An EAN identifies one exact variant globally — capacity, colour and market included.
Where both sides carry one, "is this the same product?" needs no parsing, no
normalisation and no judgement.

**Recommendation: EAN when present, spec fallback when not.** Prose offers such as
`A17 5G DS SM-A176 4+128 — Black — €135` carry no EAN and still need
brand + model + capacity + colour matching. But roughly half the volume in this sample
could be matched exactly and for free.

A second benefit: EAN makes cross-supplier matching trivially reliable. Today, deciding
that Masterfone's row and Yukatel's row are the same product requires normalising two
different descriptions. With EANs it is an equality test — which is the foundation the
combination-fill feature needs.

---

## 5. Condition grading is largely absent; region coding uses different vocabulary

The specification treats grade normalisation as the hardest sub-problem — A/A+, 14-day,
CPO, AS-IS, BNIB. In this sample almost every list is **brand new stock**:
"Brand New Stock", "Neu", "Ent. Ed.". Exactly one email concerns used hardware
(`WTB - Hardware USED`), and one references "Ins" grading.

Region coding is similar. There is no `LL/A`, `ZP/A` or `CH/A` anywhere in the sample.
The distinctions actually drawn are:

- `EU` versus `NON EU` (Google Pixel rows, Thaysen's list)
- `INDIA SPEC` (Thaysen requests it explicitly and separately)
- `DS` for dual-SIM, `Ent. Ed.` for enterprise edition

**This is good news for the timeline** — two features budgeted as hard are lighter than
expected — but the vocabulary is wrong in the current code and needs replacing with what
these suppliers actually write. It is also worth confirming with the client, since a
used-goods trade may simply be under-represented in a sample gathered in one week.

---

## 6. Format breakdown, and what it costs

| Format | Emails | Notes |
|---|---|---|
| HTML table in the body | 31 | 13,543 table rows in total across the sample |
| Prose or bullet lists | 9 | Multi-value lines are common |
| Attachment only, empty body | 3 | xlsx or xls; the body carries nothing |
| Document attachments | 9 files | 6 xlsx, 1 xls (legacy), 2 PDF |
| Photograph of a list | 1 | WhatsApp screenshot; needs vision, not text extraction |

Total body text across 45 emails is **652,000 characters — roughly 163,000 tokens**.
Six emails account for 473,000 of those characters. The largest single email, Masterfone,
is 144,669 characters and contains a 2,235-row table.

**This validates the deterministic-parsing decision emphatically.** Sending those six
emails to a language model as raw text, and asking it to emit every row as JSON, would
cost far more in output tokens than input and take minutes per email. Parsing the tables
in code — with one small model call to identify which column means what — reduces those
same six emails to a few hundred tokens each. It is also more accurate, because code
cannot invent a quantity.

### Complications in the structured files

Spreadsheets are not clean tables:

- **SMTR** opens with seven rows of prose greeting and terms before the header row.
- **SELTE** has a blank first row, then headers, then brand separator rows with a name
  and no data.
- **World Comm** carries float artifacts — a price stored as `15.470519999999999`.
- **Parktel** is legacy `.xls`, not `.xlsx`, and needs a different reader.

Header-row detection is therefore mandatory rather than a refinement.

### One line is often several offers

```
A37 SM-A376B 5G 6+128 — Awesome Lavender €200 · Charcoal €195     → 2 offers
A17 LTE DS SM-A175 4+128 — Black / Blue / Grey / Light Blue — €125 → 4 offers
IPHONE 15 128GB black / blue / pink                    (qty 100)   → 3 requests
```

Expansion has to happen during extraction, and it materially changes row counts — which
matters, because the safety check that prevents a bad parse from wiping a supplier's
stock compares today's row count against that supplier's usual count.

### Other quirks worth recording

- **Languages:** German, Italian, Polish, Dutch, Romanian, French, Spanish. Product
  descriptions are mostly English; the surrounding prose is not.
- **`8+256` means 8GB RAM and 256GB storage** — Samsung convention throughout.
- **Erregame's offer is a SharePoint link**, with the actual list behind a login. It
  cannot be extracted at all and should be flagged rather than silently ignored.
- **Newsletter furniture** — unsubscribe links, privacy notices, "DO NOT REPLY",
  embedded logos — appears in most emails and must not reach the extractor.
- **Action.pl sent five near-identical WTB emails** in the sample. Deduplication needs
  to be more than a Message-ID check.

---

## 7. What this changes in the build

**Needs rework:**

| Item | Change |
|---|---|
| `offers` schema | Generic core plus optional phone attributes; add EAN, brand, category |
| Model normalisation | Currently iPhone-only; must handle every brand, or defer to description matching |
| Price parsing | European formats, per-value decision with per-supplier fallback |
| Region normalisation | Replace LL/A vocabulary with EU / NON EU / INDIA SPEC |
| Counterparty attribution | Parse forwarded headers; review queue for tier 3 |

**Unaffected, and still correct:**

- Tenancy and row-level security — verified and passing
- The imports table and the row-count safety check — more important than before, since
  multi-value expansion makes row counts volatile
- Offer change history
- The insertions-are-harmless-closures-are-destructive rule
- The backend, deployment and test scaffolding

**Newly easier:** grade and region normalisation are lighter than budgeted, and EAN
matching removes much of the hardest cross-supplier comparison work.

---

## 8. Questions for the client

1. **Do suppliers email a shared inbox directly, or do staff forward and re-compose?**
   Twelve emails in this sample have no recoverable sender. If that is normal, some
   offers cannot be attributed automatically.
2. **Confirm the product scope.** Should the system carry accessories, consoles,
   televisions and sundries, or only phones and tablets?
3. **How much of the trade is used or graded stock?** This sample is almost entirely
   brand new, which contradicts the earlier emphasis on grading vocabulary.
4. **Is EAN reliable enough to trust as an identity key** where suppliers provide it?
5. **Which currency does each regular supplier quote in?** Needed to resolve ambiguous
   values such as `1,079`.
6. **What should happen to an unparseable offer** — the SharePoint link, the WhatsApp
   photo? Flag for manual entry, or ignore?

---

## Appendix — per-email detail

`chars` is body text length. `comma` and `dot` count European-format numbers found.

| Kind | File | Real sender | Chars | Format | comma | dot |
|---|---|---|---|---|---|---|
| offer | All in Srl Offer | *(none)* | 0 | attachment only | 0 | 0 |
| offer | Automic Offers | *(none)* | 2,250 | prose | 0 | 1 |
| offer | Erregame Offer | *(none)* | 858 | prose + SharePoint link | 0 | 0 |
| offer | CELLULAR IBERIA OFFER | *(none)* | 8,155 | html table, 73 rows | 9 | 0 |
| offer | Angebote (Offers) | newsletter@amd-gmbh.com | 13,603 | html table, 138 rows | 0 | 0 |
| offer | Apple iPhone 16e 128GB | Adrian.Kulczynski@action.pl | 570 | prose | 0 | 0 |
| offer | Landotech Mobile Stock | lukasz@landotech.eu | 3,474 | html table, 285 rows | 0 | 0 |
| offer | Metropolitan Trends | team@metro-trends.de | 86,556 | html 711 rows + xlsx | 709 | 15 |
| offer | Moto Update | Adrian.Kulczynski@action.pl | 3,164 | html table, 30 rows | 53 | 0 |
| offer | Offer (ATI Trading) | info@atitradingproject.com | 1,599 | prose | 0 | 0 |
| offer | PRICE LIST 24_07 | massimiliano.tesi@selte.eu | 321 | xlsx + pdf | 0 | 0 |
| offer | PRICELIST FROM 27.07 | sales@gotel.de | 24,502 | html table, 245 rows | 220 | 9 |
| offer | Price List Masterfone | sales@master-fone.com | 144,669 | html table, 2,235 rows | 0 | 0 |
| offer | Samsung Update | Adrian.Kulczynski@action.pl | 12,725 | html table, 110 rows | 109 | 0 |
| offer | Smalltronic Offer | inesa.zuber@smalltronic.pl | 16,959 | html 226 rows + xlsx | 0 | 0 |
| offer | Special Offer | mhanke@cytec-gmbh.de | 9,003 | html table, 101 rows | 0 | 0 |
| offer | TechPunt Xiaomi stock | zakelijk@techpunt.nl | 9,551 | html table, 3,074 rows | 1 | 0 |
| offer | Apple Accessories offer | b2bint@westech.eu | 58,609 | html table, 1,222 rows | 278 | 0 |
| offer | Apple Watch offer | b2bint@westech.eu | 32,859 | html table, 331 rows | 0 | 0 |
| offer | WANT TO SELL 27.07 | info@abbusiness.fr | 4,481 | html table, 106 rows | 80 | 0 |
| offer | WTS Pricelist HITISY | r@gsm-b2b.com | 108,409 | html table, 224 rows | 178 | 12 |
| offer | WTS today's stock list | mhanke@cytec-gmbh.de | 5,140 | pdf attachment | 0 | 0 |
| offer | World Comm Accessories | sorin.campeanu@worldcomm.ro | 5,735 | html 56 rows + xlsx | 0 | 0 |
| offer | Yukatel today offer | r@gsm-b2b.com | 41,584 | html table, 3,075 rows | 259 | 18 |
| offer | [wts] Instant 24.07 | s@personalelectronics.eu | 3,788 | html table, 37 rows | 0 | 0 |
| offer | OT Distribution Offer | *(none)* | 1,377 | prose | 16 | 0 |
| offer | Patktal Offer | *(none)* | 0 | legacy .xls only | 0 | 0 |
| offer | Reline Offer | *(none)* | 6,679 | prose | 146 | 11 |
| offer | Stock Sheet | *(none)* | 2,765 | html 90 rows + xlsx | 0 | 0 |
| offer | VNN International | *(none)* | 646 | prose + WhatsApp photo | 0 | 0 |
| offer | Vadimpex Offer | *(none)* | 2,543 | prose | 0 | 0 |
| request | Bauer Request | *(none)* | 319 | prose | 0 | 0 |
| request | Cooperation & WTB | info@trinitye.de | 3,076 | html table, 12 rows | 0 | 0 |
| request | Thaysen telecom WTB | newsletter@thaysen.com | 5,041 | html table, 149 rows | 0 | 0 |
| request | WTB Hardware USED | olga@atlastradingworld.com | 2,558 | html table, 7 rows | 0 | 0 |
| request | WTB Action ×5 | global@mailing.action.pl | 1.4–2.5k | html table, 141 rows each | 0 | 0 |
| request | WTB Phoneport | alp@phoneport24.de | 4,040 | html table, 47 rows | 0 | 0 |
| request | New Way International | *(none)* | 1,500 | prose | 0 | 0 |
| request | WTB Mobielwerkt ×3 | *(none)* | 2.9–6.3k | html tables, 38–134 rows | 0 | 0 |
