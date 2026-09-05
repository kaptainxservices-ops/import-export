"""Cut last week's prices out of this week's email — without cutting the email.

`InboundEmail.body_text` is documented as arriving with quoted history already removed,
and the reason is stated there: if history leaks in, last week's prices are re-extracted
as today's. But that stripping is n8n's job, n8n is a GUI the client can edit, and a
correctness-critical transformation living only in a place nobody can test is a
transformation that will one day stop happening quietly. So it is done again here, where
it is deterministic and covered.

**The danger runs the other way in this inbox.** Of 43 sample emails with a plain-text
body, 28 contain 'Begin forwarded message' — because forwarding a supplier's email to
himself is exactly how the client works. The payload is *below* that marker. Only 3
emails contain reply quoting at all.

So the aggressive stripper that would be right for a normal mailbox is catastrophic
here: it would delete the contents of two thirds of the corpus to tidy up three emails.
Everything below is built to fail towards keeping too much.
"""

from __future__ import annotations

import re

from app.brain.normalise import find_price

# Unambiguous starts of a quoted reply. Each has to be anchored to a line start, because
# 'From:' inside a signature block is not the top of a quote.
_REPLY_MARKERS = [
    # 'On Tue, 5 Aug 2026 at 09:14, Someone <a@b.com> wrote:' and its translations.
    re.compile(r"^\s*(?:On|Am|Le|Il|El)\b[^\n]{0,160}\b(?:wrote|schrieb|a écrit|ha scritto):\s*$",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*_{10,}\s*$", re.MULTILINE),
    # Three or more consecutive quoted lines. One '>' is a stray character; a block of
    # them is a conversation.
    re.compile(r"^>[^\n]*\n>[^\n]*\n>", re.MULTILINE),
]

# The client's own workflow. Anything at or after this point is the message he is
# forwarding, which is to say the entire reason the email was sent.
_FORWARD = re.compile(
    r"-{2,}\s*Forwarded message\s*-{2,}|Begin forwarded message|"
    r"^\s*(?:Weitergeleitete Nachricht|Messaggio inoltrato|Mensaje reenviado)",
    re.IGNORECASE | re.MULTILINE,
)


def strip_quoted_history(body: str | None) -> tuple[str, str]:
    """Remove a quoted reply tail. Returns the body to parse and why it looks that way.

    The reason string is not decoration — it goes into the import notes, so that an
    email which came out short can be explained without re-deriving this by hand.
    """
    if not body or not body.strip():
        return body or "", "empty body"

    cut = _first_reply_marker(body)
    if cut is None:
        return body, "no quoted reply found"

    # A forward below the quote means the quote was wrapping the thing we actually want.
    if _FORWARD.search(body, cut):
        return body, "a forwarded message sits inside the quoted part; kept whole"

    kept, dropped = body[:cut], body[cut:]

    # The check that makes the rest safe to be wrong about. Rather than trying to
    # classify every marker correctly, look at what the cut would throw away: if the
    # prices are all on the far side of it, the marker was not the top of a quote,
    # whatever it looked like.
    kept_prices, dropped_prices = _price_lines(kept), _price_lines(dropped)
    if dropped_prices > kept_prices:
        return body, (
            f"the quoted part holds more prices ({dropped_prices} lines "
            f"against {kept_prices}); kept whole"
        )

    return kept.rstrip(), f"dropped {len(dropped)} characters of quoted reply"


def _first_reply_marker(body: str) -> int | None:
    starts = [match.start() for pattern in _REPLY_MARKERS if (match := pattern.search(body))]
    return min(starts) if starts else None


def _price_lines(text: str) -> int:
    """Lines carrying something marked as money.

    The same test the prose extractor applies, so this measures what would actually be
    lost rather than how much text moved.
    """
    return sum(1 for line in text.splitlines() if find_price(line) is not None)
