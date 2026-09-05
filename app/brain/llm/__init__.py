"""The model, used only where code cannot decide.

Four rules hold across this package. The first is the one that matters.

**The model never supplies data.** It picks a label from a closed set of three words,
or it picks a column index out of a header that already exists. Every quantity and
every price that reaches the board comes from the deterministic parser. A hallucinated
price gets quoted to a buyer and a hallucinated quantity gets promised to one; a
hallucinated *column index* is caught by checking whether that column actually contains
prices, which is arithmetic.

**The model proposes, code verifies.** Nothing the model returns is trusted on its own.
An index must be in range; a column it calls `price` must parse as money down the sample
rows, or the mapping is thrown away. See `columns._corroborate`.

**No key means no change.** With `ANTHROPIC_API_KEY` unset, every entry point here
returns `None` on its first line and the system behaves exactly as it did before this
package existed — deterministic answers, and the undecidable minority in a review queue.

**A model failure is never an email failure.** Rate limit, outage, revoked key, timeout,
malformed JSON: all of them return `None`. The email lands in the review queue, which is
where it would have landed a minute earlier anyway.

Cost is contained by where these are called from, not by prompt length. Classification
runs once per email and only for the ones the rules could not settle. Column mapping
runs once per *table* — never once per row — and is cached by header signature, because
a supplier sends the same header every morning for years.
"""

from app.brain.llm.client import Answer, ask, available
from app.brain.llm.columns import map_columns_llm, mapper_for
from app.brain.llm.side import SideVerdict, classify_side_llm
from app.brain.llm.usage import UsageLog

__all__ = [
    "Answer",
    "SideVerdict",
    "UsageLog",
    "ask",
    "available",
    "classify_side_llm",
    "map_columns_llm",
    "mapper_for",
]
