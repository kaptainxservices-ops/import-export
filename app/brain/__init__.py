"""The brain: everything that decides what an email *means*.

Phase 2 fills this in:

    classify.py    seller offer / buyer request / both / negotiation / irrelevant  (Haiku)
    extract.py     prose -> line items, strict JSON schema                         (Sonnet)
    tables.py      structured attachments: one LLM call maps columns, code applies
                   the mapping to every row
    normalise/     models, grades, colours, region codes, currency, price basis

Two rules hold across all of it.

**No trading logic outside this package.** Not in n8n, not in the API layer, not in the
frontend. One place to test, one place to fix.

**Insertions are harmless, closures are destructive.** A wrong new row is noise. Wrongly
marking a live 120-unit lot as sold loses a deal. When uncertain, insert and flag —
never close.
"""
