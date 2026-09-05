"""Set a dashboard login's password directly, without sending any email.

Supabase's built-in mailer is throttled to a handful of messages an hour — it is a
convenience for development, not a mail service — so "email rate limit exceeded" is the
normal outcome of trying twice. Magic links and password resets both go through it, which
means both are unavailable at exactly the moment you need one.

The service key does not need the mailer. It talks to the admin API, which sets the
password server-side in one request.

    python scripts/set_password.py shahardik1165@gmail.com

The password is prompted for, never echoed, never taken as an argument, and never
printed. Keeping it out of argv is the point of the prompt: a password on the command
line is written to your shell history, and from there into any backup of it.

Existing sessions are revoked afterwards, so a token that leaked — pasted into a chat, a
screenshot, an issue — stops working immediately.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

# Supabase's own default is six. Eight is asked for here because this login opens a board
# holding a client's entire order book, and the difference in typing effort is two
# characters.
MINIMUM = 8


def call(method: str, path: str, key: str, url: str, body: dict | None = None):
    """One admin request. Returns parsed JSON, or None for an empty 2xx body."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def find_user(email: str, key: str, url: str) -> dict | None:
    """Page through the user list looking for one address.

    Paged rather than filtered because the admin list endpoint's filter syntax has
    changed between GoTrue versions and a wrong filter returns everybody rather than an
    error — which would silently reset the wrong account.
    """
    wanted = email.strip().lower()

    for page in range(1, 21):
        payload = call("GET", f"/auth/v1/admin/users?page={page}&per_page=200", key, url)
        users = (payload or {}).get("users", [])
        if not users:
            return None
        for user in users:
            if (user.get("email") or "").lower() == wanted:
                return user

    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        print("usage: python scripts/set_password.py <email>")
        return 2

    email = sys.argv[1]
    settings = get_settings()

    if not settings.supabase_url or not settings.supabase_secret_key:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY must both be set in .env.")
        return 1

    url, key = settings.supabase_url, settings.supabase_secret_key

    try:
        user = find_user(email, key, url)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        print(f"Admin API refused the request ({error.code}).")
        if error.code in (401, 403):
            print(
                "  That is the key. SUPABASE_SECRET_KEY must be the secret/service_role\n"
                "  key from Settings > API Keys — not the publishable or anon one."
            )
        else:
            print(f"  {detail}")
        return 1
    except urllib.error.URLError as error:
        print(f"Could not reach {url}: {error.reason}")
        return 1

    if user is None:
        print(f"No auth user with the address {email!r}.")
        print("  Create it in Supabase > Authentication > Users, then run this again.")
        return 1

    print(f"Found {user['email']}  id {user['id']}")
    if not user.get("email_confirmed_at"):
        print("  (address not confirmed — this will confirm it too)")

    # Prompted twice because a typo here locks the account rather than reporting an
    # error: the password would be set to something nobody knows.
    first = getpass("New password: ")
    if len(first) < MINIMUM:
        print(f"Too short — {MINIMUM} characters minimum. Nothing was changed.")
        return 1
    if first != getpass("Again: "):
        print("They do not match. Nothing was changed.")
        return 1

    try:
        call(
            "PUT",
            f"/auth/v1/admin/users/{user['id']}",
            key,
            url,
            {"password": first, "email_confirm": True},
        )
    except urllib.error.HTTPError as error:
        print(f"Rejected ({error.code}): {error.read().decode(errors='replace')[:300]}")
        return 1

    print(f"\nPassword set for {user['email']}.")

    # Best-effort: not every GoTrue version exposes this, and failing to revoke old
    # sessions is not a reason to report that the password change failed. It did not.
    try:
        call("DELETE", f"/auth/v1/admin/users/{user['id']}/sessions", key, url)
        print("Existing sessions revoked — any previously issued token is now dead.")
    except (urllib.error.HTTPError, urllib.error.URLError):
        print(
            "Could not revoke existing sessions on this Supabase version.\n"
            "  Sign the user out from Authentication > Users if a token has leaked."
        )

    print("\nSign in at http://localhost:5173")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
