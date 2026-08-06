"""What n8n POSTs to /ingest.

This is the seam between plumbing and brain, so it is written down rather than
implied. n8n's job is to deliver a message in this shape; every decision about what
the message *means* happens on this side of the line.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AttachmentKind = Literal["table", "text", "image", "other"]


class Attachment(BaseModel):
    """One attachment, already converted to text or rows by n8n where possible.

    `kind="table"` means rows were extracted cleanly and the deterministic parser can
    handle it — no per-row LLM call, which is where most of the cost saving lives.
    `kind="image"` means a screenshot that only the model can read.
    """

    model_config = ConfigDict(extra="ignore")

    filename: str
    mime_type: str = ""
    kind: AttachmentKind = "other"

    text: str | None = Field(default=None, description="Flat text, for prose attachments")
    rows: list[list[str]] | None = Field(
        default=None, description="Sheet rows including the header row, for kind='table'"
    )
    image_base64: str | None = Field(default=None, description="Set for kind='image'")


class InboundEmail(BaseModel):
    """A single message handed over for processing."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str = Field(description="Which client this mailbox belongs to")

    message_id: str = Field(description="RFC 5322 Message-ID, used for deduplication")
    in_reply_to: str | None = Field(default=None, description="Links a reply to what it answers")
    thread_id: str | None = None

    direction: Literal["inbound", "outbound"] = "inbound"

    from_email: str
    from_name: str = ""
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    received_at: datetime

    body_text: str = Field(
        description="Body with quoted reply history and signature already stripped. "
        "This is what gets parsed — if history leaks in, last week's prices are "
        "re-extracted as today's."
    )
    body_raw: str = Field(
        default="",
        description="The complete original message, kept only to render the source "
        "drawer in the dashboard. Never parsed.",
    )

    attachments: list[Attachment] = Field(default_factory=list)


class IngestAccepted(BaseModel):
    """Acknowledgement returned to n8n. 202, not 200: work continues after the reply."""

    message_id: str
    accepted: bool
    detail: str = ""
