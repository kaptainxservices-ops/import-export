"""Somewhere to put what the model calls cost, without the brain knowing about storage.

Anthropic is the only cost that scales linearly with clients, so it is the only one
worth measuring per tenant. But the calls happen deep inside parsing, where neither the
tenant nor the email id is known yet — the email has not been saved at the point its own
classification is decided.

So calls accumulate here as they happen, and the pipeline stamps them with the tenant and
the email once both exist. The brain still imports nothing from `app.db`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.brain.llm.client import Answer


@dataclass
class UsageLog:
    """Model calls made while processing one email."""

    calls: list[tuple[str, Answer]] = field(default_factory=list)

    def add(self, operation: str, answer: Answer | None) -> None:
        """Record a call. `None` answers are dropped: a call that never happened, or one
        that failed before reaching the API, has no tokens to account for."""
        if answer is not None:
            self.calls.append((operation, answer))

    @property
    def cost_usd(self) -> float:
        return round(sum(answer.cost_usd for _, answer in self.calls), 6)

    def __bool__(self) -> bool:
        return bool(self.calls)

    def __len__(self) -> int:
        return len(self.calls)
