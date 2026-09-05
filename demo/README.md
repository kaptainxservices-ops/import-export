# Six emails that prove the system works

Not fixtures — these go through the real pipeline, exactly as a live email would.

| File | Sender | Should do |
| --- | --- | --- |
| `01-almanar-offer.eml` | Al Manar, Dubai | **sell** — 3 rows, incl. 40 × 15 Pro Max 256GB Natural Ti @ 905 |
| `02-gulfcell-offer.eml` | Gulf Cell, Sharjah | **sell** — 3 rows, incl. 60 × the same handset @ 928 |
| `03-novatraders-offer.eml` | Nova Traders, Dubai | **sell** — 2 rows, incl. 70 × 14 Pro 128GB Deep Purple @ 545 |
| `04-meridian-request.eml` | Meridian Wireless, Brazil | **buy** — wants 100 × 15 Pro Max @ up to 960, and 300 × 16 Pro Desert Ti |
| `05-kowloon-request.eml` | Kowloon Digital, Hong Kong | **buy** — wants 100 × 14 Pro Deep Purple @ up to 610 |
| `06-noise.eml` | Building management | **ignored** — a lift maintenance notice, no products, no prices |

## What the Matches tab should then show

**A combination fill.** Meridian wants 100 and no single supplier has 100. Al Manar has
40 and Gulf Cell has 60, so the engine builds the lot from two suppliers: blended cost
918.80, margin **4,120 USD**.

**A partial fill.** Kowloon wants 100 and Nova only has 70. It is offered anyway, marked
**short by 30** — a shortfall is stated rather than hidden, because it is the trader's
next phone call.

**Unmet demand.** Nobody stocks the iPhone 16 Pro Desert Titanium, so that requirement
sits on the board as demand to go and source. It shows a dash, not a margin.

**A row that is not stock.** The lift notice is stored and left unclassified. It never
reaches the board, and no offer is invented from it.

## Running it

```powershell
python scripts\load_samples.py --tenant b9316981-b732-4891-854f-3335205dd582 --samples demo --wipe
```

Drop `--wipe` to add these on top of the 45 real samples instead of replacing them.
