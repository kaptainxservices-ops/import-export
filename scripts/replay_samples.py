"""Replay the sample emails through the real pipeline, offline, and print each decision.

Runs on the in-memory store with every model and database setting blanked, so it never
touches Supabase or calls a model -- safe to run against a .env that points at the live
project.

    python scripts/replay_samples.py samples              # in the order they arrived
    python scripts/replay_samples.py samples --shuffle 5  # also 5 random orders

With --shuffle, each random order must end with the same board as the time order: what
a list does may not depend on which email n8n happened to deliver first.
"""

from __future__ import annotations

import logging
import os
import random
import sys
from collections import Counter
from pathlib import Path

for key in ("ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "SUPABASE_URL",
            "SUPABASE_SECRET_KEY"):
    os.environ[key] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
# A Windows console or a redirect to a file may not take '·' or an emoji subject.
sys.stdout.reconfigure(errors="replace")

from app.brain.list_decision import parse_note  # noqa: E402
from app.db.memory import InMemoryRepository  # noqa: E402
from app.db.models import TenantConfig  # noqa: E402
from app.pipeline import process_email  # noqa: E402
from scripts.load_samples import read_email  # noqa: E402

TENANT = TenantConfig(
    id="t1", name="TVD", internal_domains={"tvdservices.com"},
    internal_addresses={"tvdlogistics@outlook.com", "tvdservices@hotmail.com"},
)


def run(emails, *, show: bool) -> set[tuple]:
    repo = InMemoryRepository([TENANT])
    tally: Counter = Counter()
    for path, message in emails:
        before = len(repo.imports)
        result = process_email(message, repo)
        record = repo.imports[-1].record if len(repo.imports) > before else None
        stored = parse_note(record.notes) if record else None
        if stored:
            action = stored.action
        elif record:
            action = record.status
        else:
            action = result.outcome
        if record and record.status == "flagged_low_row_count":
            action = "review (short whole list)"
        tally[action] += 1
        if show:
            rec = result.reconcile
            counts = (
                f"+{rec.inserted:<4} ~{rec.updated:<4} -{rec.closed:<4}" if rec else " " * 18
            )
            print(f"{message.received_at:%d %b %H:%M}  {path.name[:46]:46} "
                  f"{result.row_count:5} lines  {counts}  {action}")
            if stored and stored.reasons:
                print(f"{'':15}{' · '.join(stored.reasons)[:150]}")
    if show:
        print("\nDecisions:")
        for action, count in tally.most_common():
            print(f"  {count:3}  {action}")
    # By address, not id: ids are handed out in arrival order, so they differ per run.
    who = {cp.id: cp.primary_email for cp in repo.counterparties.values()}
    return {
        (o.side, who.get(o.counterparty_id), o.description, o.quantity, o.unit_price)
        for o in repo.offers.values() if o.status == "live"
    }


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    shuffles = int(sys.argv[sys.argv.index("--shuffle") + 1]) if "--shuffle" in sys.argv else 0

    emails = [(p, read_email(p, "t1")) for p in root.rglob("*.eml")]
    emails.sort(key=lambda pair: pair[1].received_at)
    print(f"{len(emails)} emails from {root}, in the order they arrived\n")
    board = run(emails, show=True)
    print(f"\nLive lines on the board: {len(board)}")

    failed = 0
    for seed in range(1, shuffles + 1):
        shuffled = emails[:]
        random.Random(seed).shuffle(shuffled)
        other = run(shuffled, show=False)
        differs = len(board ^ other)
        failed += bool(differs)
        print(f"random order {seed}: {'same board' if not differs else f'{differs} lines differ'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
