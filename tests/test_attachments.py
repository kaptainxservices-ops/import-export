"""Workbooks that arrive as raw bytes rather than rows.

n8n can hand over a spreadsheet either way. Sending `rows` means n8n converted it;
sending `content_base64` means this side converts it, using the same `read_spreadsheet`
the sample loader and the HTML path already use.

The second shape is the one to prefer, so these tests exist to make it as trustworthy
as the first. They care about two things in particular: that every sheet survives, and
that the base64 is gone by the time anything is stored.
"""

import base64
import io
from datetime import datetime

import pytest

from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig
from app.pipeline import MAX_ATTACHMENT_BYTES, materialise_attachments, process_email
from app.schemas.email import Attachment, InboundEmail

TENANT = TenantConfig(
    id="tenant-1",
    name="TVD",
    internal_domains={"tvdservices.com"},
    internal_addresses={"tvdlogistics@outlook.com"},
)

HEADERS = ["Description", "EAN", "Qty", "Price"]
STOCK = [
    HEADERS,
    ["Apple iPhone 15 128GB Black", "0195949035999", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "0195949036002", "45", "679"],
    ["Samsung Galaxy S24 256GB Onyx", "8806095258799", "30", "519"],
]


@pytest.fixture
def repo():
    return InMemoryRepository([TENANT])


def workbook(sheets):
    """A real .xlsx in memory, so the test exercises openpyxl rather than a stub."""
    openpyxl = pytest.importorskip("openpyxl")

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(title=name)
        for row in rows:
            sheet.append(row)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def encoded(sheets):
    return base64.b64encode(workbook(sheets)).decode("ascii")


def email(attachments, *, message_id="<m1@x>"):
    return InboundEmail(
        tenant_id="tenant-1",
        message_id=message_id,
        from_email="sales@supplier.example",
        subject="WTS Price List 07.08.2026",
        received_at=datetime(2026, 8, 7, 9, 0),
        body_text="Please find our price list.",
        attachments=attachments,
    )


# ---------------------------------------------------------------- conversion


def test_a_workbook_sent_as_bytes_becomes_rows():
    out = materialise_attachments(
        [Attachment(filename="stock.xlsx", content_base64=encoded({"Sheet1": STOCK}))]
    )

    assert len(out) == 1
    assert out[0].kind == "table"
    assert out[0].rows == STOCK


def test_every_sheet_survives():
    """Taking only the first sheet discards most of some suppliers' lists."""
    second = [HEADERS, ["Xiaomi Redmi Note 13 256GB Blue", "6941812757147", "88", "189"]]

    out = materialise_attachments(
        [
            Attachment(
                filename="stock.xlsx",
                content_base64=encoded({"Phones": STOCK, "Tablets": second}),
            )
        ]
    )

    assert len(out) == 2
    assert [a.rows for a in out] == [STOCK, second]
    # The sheet name travels with the rows, so a row's source_ref names the sheet it
    # came from rather than only the file.
    assert [a.filename for a in out] == ["stock.xlsx!Phones", "stock.xlsx!Tablets"]


def test_the_payload_is_always_stripped():
    """Whatever happens, no attachment leaves here still carrying the file.

    This is the invariant that keeps megabytes of base64 out of the email row.
    """
    cases = [
        Attachment(filename="stock.xlsx", content_base64=encoded({"S": STOCK})),
        Attachment(filename="stock.xlsx", content_base64="not base64 at all!!"),
        Attachment(filename="stock.xlsx", content_base64=base64.b64encode(b"junk").decode()),
        Attachment(filename="photo.jpg", content_base64=base64.b64encode(b"\xff\xd8").decode()),
        Attachment(filename="stock.xlsx", rows=STOCK, content_base64=encoded({"S": STOCK})),
    ]

    for attachment in cases:
        for result in materialise_attachments([attachment]):
            assert result.content_base64 is None, attachment.filename


# ---------------------------------------------------------------- declining to read


def test_rows_already_present_are_not_second_guessed():
    out = materialise_attachments(
        [
            Attachment(
                filename="stock.xlsx",
                kind="table",
                rows=STOCK,
                content_base64=encoded({"S": [HEADERS, ["something", "else", "1", "2"]]}),
            )
        ]
    )

    assert len(out) == 1
    assert out[0].rows == STOCK


def test_a_non_workbook_is_kept_without_rows():
    out = materialise_attachments(
        [Attachment(filename="terms.pdf", content_base64=base64.b64encode(b"%PDF-1.4").decode())]
    )

    assert len(out) == 1
    assert out[0].rows is None
    assert out[0].kind == "other"


def test_invalid_base64_does_not_raise():
    out = materialise_attachments(
        [Attachment(filename="stock.xlsx", content_base64="????not base64????")]
    )

    assert len(out) == 1
    assert out[0].rows is None


def test_an_unreadable_workbook_is_kept_as_a_record():
    """One corrupt file must not abandon the email it arrived on."""
    out = materialise_attachments(
        [
            Attachment(
                filename="stock.xlsx", content_base64=base64.b64encode(b"nonsense").decode()
            ),
            Attachment(filename="good.xlsx", content_base64=encoded({"S": STOCK})),
        ]
    )

    assert len(out) == 2
    assert out[0].rows is None
    assert out[1].rows == STOCK


def test_an_oversized_file_is_refused_before_it_is_parsed():
    oversized = base64.b64encode(b"\x00" * (MAX_ATTACHMENT_BYTES + 1)).decode()

    out = materialise_attachments([Attachment(filename="stock.xlsx", content_base64=oversized)])

    assert len(out) == 1
    assert out[0].rows is None


def test_attachments_without_bytes_pass_through_untouched():
    original = [
        Attachment(filename="note.txt", kind="text", text="see attached"),
        Attachment(filename="stock.xlsx", kind="table", rows=STOCK),
    ]

    assert materialise_attachments(original) == original


# ---------------------------------------------------------------- through the pipeline


def test_the_pipeline_reaches_the_board_from_bytes_alone(repo):
    result = process_email(
        email([Attachment(filename="stock.xlsx", content_base64=encoded({"S": STOCK}))]), repo
    )

    assert result.processed
    assert result.side == "sell"
    assert result.row_count == 3
    assert len(repo.live_offers()) == 3


def test_bytes_and_rows_produce_the_same_board(repo):
    from_bytes = process_email(
        email([Attachment(filename="stock.xlsx", content_base64=encoded({"S": STOCK}))]), repo
    )

    other = InMemoryRepository([TENANT])
    from_rows = process_email(
        email([Attachment(filename="stock.xlsx", kind="table", rows=STOCK)]), other
    )

    assert from_bytes.row_count == from_rows.row_count
    assert sorted(o.identity_key for o in repo.live_offers()) == sorted(
        o.identity_key for o in other.live_offers()
    )


def test_nothing_base64_is_stored_on_the_email_row(repo):
    """The reason conversion happens at the top of the pipeline rather than at use."""
    process_email(
        email([Attachment(filename="stock.xlsx", content_base64=encoded({"S": STOCK}))]), repo
    )

    stored = next(iter(repo.emails.values()))
    assert stored.attachments
    for attachment in stored.attachments:
        assert attachment.get("content_base64") is None
        assert attachment["rows"] == STOCK


def test_a_reprocess_works_from_the_stored_rows(repo):
    """A human naming the sender must not need the original file kept anywhere."""
    from app.pipeline import reprocess_email

    forwarded = email(
        [Attachment(filename="stock.xlsx", content_base64=encoded({"S": STOCK}))]
    ).model_copy(update={"from_email": "sales@tvdservices.com", "body_text": "fwd"})

    first = process_email(forwarded, repo)
    assert first.outcome == "needs_sender_review"

    counterparty = repo.create_counterparty("tenant-1", "sales@supplier.example", "Supplier")
    repo.attribute_email("tenant-1", first.email_id, counterparty.id, "sell")

    again = reprocess_email("tenant-1", first.email_id, repo)
    assert again.processed
    assert again.row_count == 3
