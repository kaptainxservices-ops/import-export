"""Post one hand-made email to /ingest, to prove the target before wiring n8n to it.

When something fails on the first live run, the fault is in one of three places: the URL,
the token, or the shape of the payload. n8n can only be blamed for the third, and it is
much easier to debug once the first two are known good.

    python scripts/ping_ingest.py https://your-service.onrender.com
    python scripts/ping_ingest.py http://127.0.0.1:8000 --token abc123
    python scripts/ping_ingest.py https://your-service.onrender.com --attachment

The token comes from INGEST_TOKEN in .env unless --token is given. Nothing is written to
the board that cannot be found again: the message id is stamped with the time, so this
can be run repeatedly, and every row it creates belongs to a counterparty called
'ping@ingest-test.local' that can be deleted in one statement.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

TENANT = "b9316981-b732-4891-854f-3335205dd582"

# A small but genuinely representative body: a marked price, a quantity, a capacity and
# a colour. If this comes back with 3 rows, the whole chain works.
BODY = """Hello,

Our stock list today:

Apple iPhone 15 Pro Max 256GB Natural Titanium *40* 905 USD
Apple iPhone 16 128GB Teal *45* 679 USD
Samsung Galaxy A16 128GB Black *120* 105 USD

Best regards
Ping Test
"""

SHEET = [
    ["Description", "EAN", "Qty", "Price"],
    ["Apple iPhone 15 128GB Black", "0195949035999", "50", "579"],
    ["Apple iPhone 16 128GB Teal", "0195949036002", "45", "679"],
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="backend base URL, no trailing slash")
    parser.add_argument("--token", help="defaults to INGEST_TOKEN from .env")
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument(
        "--attachment", action="store_true", help="also send a spreadsheet, as n8n will"
    )
    args = parser.parse_args()

    token = args.token or get_settings().ingest_token
    if not token:
        print("No token. Put INGEST_TOKEN in .env or pass --token.")
        return 1

    stamp = datetime.now(timezone.utc)  # noqa: UP017
    payload = {
        "tenant_id": args.tenant,
        "message_id": f"<ping-{stamp:%Y%m%d-%H%M%S}@ingest-test.local>",
        "from_email": "ping@ingest-test.local",
        "from_name": "Ping Test",
        "to": ["desk@tvdservices.example"],
        "subject": f"WTS stock list {stamp:%d.%m.%Y %H:%M}",
        "received_at": stamp.isoformat(),
        "body_text": BODY,
        "body_raw": BODY,
        "attachments": (
            [{"filename": "stock.xlsx", "kind": "table", "rows": SHEET}]
            if args.attachment
            else []
        ),
    }

    url = f"{args.url.rstrip('/')}/ingest"
    print(f"POST {url}")
    print(f"  token   ...{token[-4:]}  ({len(token)} chars)")
    print(f"  tenant  {args.tenant}")
    print(f"  sending {'body + spreadsheet' if args.attachment else 'body only'}\n")

    request = urllib.request.Request(  # noqa: S310 — the URL is given on the command line
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Token": token,
            "User-Agent": "import-export-ping/0.1",
        },
        method="POST",
    )

    try:
        # Generous, because Render's free tier sleeps and the first request after a
        # quiet night pays for the whole container starting up.
        with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
            body = json.loads(response.read().decode())
            print(f"  {response.status} {response.reason}")
            print(f"  {body.get('detail')}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:400]
        print(f"  {error.code} {error.reason}\n  {detail}\n")
        print(f"  {_explain(error.code)}")
        return 1
    except Exception as error:
        print(f"  could not reach it: {error}\n")
        print("  A timeout on Render's free tier usually means the service is asleep.")
        print("  Open the URL in a browser once, wait for it to load, then try again.")
        return 1

    print("\nWorks. n8n now only has to produce this same JSON.")
    return 0


def _explain(code: int) -> str:
    if code == 401:
        return (
            "The token does not match. Compare INGEST_TOKEN in Render's Environment tab\n"
            "  against the one in your local .env — they are compared exactly, so a\n"
            "  trailing space counts."
        )
    if code == 503:
        return (
            "INGEST_TOKEN is not set on the server at all. The endpoint refuses rather\n"
            "  than accepting everything, because an open ingest that shipped unnoticed\n"
            "  is worse than an outage. Set it in Render's Environment tab."
        )
    if code == 422:
        return "The payload shape is wrong — the body above names the field."
    if code == 404:
        return "Wrong URL. It should be the service root, with no path after it."
    if code == 500:
        return "The email reached the pipeline and the pipeline failed. Check Render's logs."
    return "See the body above."


if __name__ == "__main__":
    raise SystemExit(main())
