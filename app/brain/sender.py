"""Who actually sent this offer?

Offer identity is keyed on counterparty, and the disappearance rule compares today's
list against *that supplier's* previous one. Attribute an email to the wrong supplier
and reconciliation corrupts two suppliers' boards at once — one gets rows it never
sent, the other has live stock closed as sold. So this is resolved explicitly, with a
recorded method and confidence, rather than by reading the From header and hoping.

Three shapes occur in practice:

1. **Direct** — the supplier emails the client's inbox. The envelope is the truth, and
   this is the overwhelming majority.

2. **Forwarded** — an internal address forwards a supplier's message. The envelope is
   the client's own staff; the real sender is in the 'Begin forwarded message' block.

3. **Relayed by hand** — offers that arrive on WhatsApp, which a staff member copies
   into a fresh email. There is no forwarded block and no supplier address anywhere in
   the headers. The company name usually survives in the subject ('Reline Offer') or a
   sign-off in the body.

Shape 3 cannot be resolved with certainty by any parser, so it is never resolved
silently: it comes back with needs_review set, for a human to confirm in one click.
"""

import re
from dataclasses import dataclass
from typing import Literal

ResolutionMethod = Literal[
    "envelope",
    "forwarded_header",
    "body_signature",
    "subject_company",
    "unresolved",
]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# 'Begin forwarded message:' (Apple Mail), '-----Original Message-----' (Outlook),
# 'From: ... Sent: ...' (Outlook inline). All three appear in the samples.
_FORWARD_MARKER = re.compile(
    r"(?:begin\s+forwarded\s+message|-{2,}\s*original\s+message|-{2,}\s*forwarded\s+message)",
    re.IGNORECASE,
)
_FORWARD_FROM = re.compile(
    r"From:\s*\"?([^\"<\n\r]*?)\"?\s*<?\s*(?P<email>[\w.+-]+@[\w-]+\.[\w.-]+)",
    re.IGNORECASE,
)

# Free mailboxes are people, not suppliers — never treat one as a company identity
# without review.
_FREEMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "gmx.de",
    "web.de", "proton.me", "protonmail.com", "mail.ru", "yandex.ru",
}

# Subject lines in the relayed emails: 'Reline Offer', 'VNN International - Offer',
# 'New Way International  - Request'.
# A trailing date is optional: 'Smalltronic Offer 27.07.2026' is as common as
# 'Reline Offer'.
#
# The colon is required when a reply prefix is present. With it optional, 'Reline
# Offer' parsed as Re: + 'line Offer' and the supplier came out as "line".
_SUBJECT_COMPANY = re.compile(
    r"^\s*(?:(?:fw|fwd|re)\s*:\s*)?(?P<company>.+?)\s*[-–—]?\s*"
    r"(?:offers?|requests?|wtb|wts|stock\s*(?:list|sheet)|price\s*list)"
    r"\s*[\d./_\s-]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolvedSender:
    email: str | None
    name: str | None
    method: ResolutionMethod
    confidence: float
    needs_review: bool

    @property
    def domain(self) -> str | None:
        return self.email.split("@", 1)[1].lower() if self.email and "@" in self.email else None


def resolve_sender(
    envelope_from: str | None,
    subject: str | None,
    body_text: str | None,
    internal_domains: set[str] | None = None,
    internal_addresses: set[str] | None = None,
) -> ResolvedSender:
    """Work out which counterparty an email came from.

    `internal_domains` and `internal_addresses` describe the client's own identities —
    their domain, plus any personal mailboxes staff send from. Configured per tenant,
    because 'internal' is not something that can be detected.
    """
    internal_domains = {d.lower().lstrip("@") for d in (internal_domains or set())}
    internal_addresses = {a.lower() for a in (internal_addresses or set())}

    env_email, env_name = _split_address(envelope_from)

    # 1. Direct from the supplier — the common case in production.
    if env_email and not _is_internal(env_email, internal_domains, internal_addresses):
        return ResolvedSender(env_email, env_name, "envelope", 1.0, needs_review=False)

    # 2. Forwarded by staff: read the forwarded header block.
    forwarded = _from_forward_block(body_text)
    if forwarded:
        email, name = forwarded
        if not _is_internal(email, internal_domains, internal_addresses):
            # High but not total: a chain of forwards can nest several From: lines, and
            # the outermost is not always the originator.
            return ResolvedSender(email, name, "forwarded_header", 0.9, needs_review=False)

    # 3. Relayed by hand. Look for an external address anywhere in the body — often a
    # sign-off. Deliberately low confidence: an address in a body might be a colleague
    # copied in, an unsubscribe link, or a customer mentioned in passing.
    signature = _first_external_address(body_text, internal_domains, internal_addresses)
    if signature:
        return ResolvedSender(signature, None, "body_signature", 0.5, needs_review=True)

    # 4. Nothing but a company name in the subject. Enough to suggest a match against
    # known counterparties, never enough to file an offer against one.
    company = _company_from_subject(subject)
    if company:
        return ResolvedSender(None, company, "subject_company", 0.3, needs_review=True)

    return ResolvedSender(None, None, "unresolved", 0.0, needs_review=True)


def _split_address(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    match = _EMAIL.search(raw)
    email = match.group(0).lower() if match else None
    name = raw.split("<")[0].strip().strip('"') if "<" in raw else None
    return email, (name or None)


def _is_internal(email: str, domains: set[str], addresses: set[str]) -> bool:
    if email.lower() in addresses:
        return True
    domain = email.split("@", 1)[1].lower() if "@" in email else ""
    return domain in domains


def _from_forward_block(body: str | None) -> tuple[str, str | None] | None:
    """Read the first From: line after a forward marker.

    Only the region just after the marker is searched. Scanning the whole body would
    happily pick up a 'From:' inside a quoted chain three replies deep.
    """
    if not body:
        return None

    marker = _FORWARD_MARKER.search(body)
    start = marker.end() if marker else 0
    window = body[start : start + 800]

    match = _FORWARD_FROM.search(window)
    if not match:
        return None

    name = (match.group(1) or "").strip()
    return match.group("email").lower(), (name or None)


def _first_external_address(
    body: str | None, domains: set[str], addresses: set[str]
) -> str | None:
    if not body:
        return None

    for candidate in _EMAIL.findall(body):
        email = candidate.lower()
        if _is_internal(email, domains, addresses):
            continue
        domain = email.split("@", 1)[1]
        if domain in _FREEMAIL:
            continue
        # Tracking and unsubscribe machinery, not a person.
        if re.search(r"(?:no-?reply|do-?not-?reply|unsubscribe|bounce|mailer|postmaster)", email):
            continue
        return email

    return None


def _company_from_subject(subject: str | None) -> str | None:
    if not subject:
        return None

    cleaned = re.sub(r"\s+", " ", subject).strip()
    cleaned = re.sub(r"^(?:fw|fwd|re)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()

    match = _SUBJECT_COMPANY.match(cleaned)
    if not match:
        return None

    company = match.group("company").strip(" -–—")
    # Strip a trailing date, as in 'Smalltronic Offer 27.07.2026'.
    company = re.sub(r"[\d./_-]{6,}$", "", company).strip(" -–—")

    if len(company) < 3 or not re.search(r"[a-z]", company, re.IGNORECASE):
        return None
    return company
