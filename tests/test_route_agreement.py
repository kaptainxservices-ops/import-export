"""Every path the frontend calls must exist on the backend.

This test exists because the opposite was true for two months. `ImportQueue.tsx` called
five endpoints that were never built, so the Review tab returned a raw 404 in
production — and since `ReviewQueue` renders `ImportQueue` inside itself, the half that
did work went down with it.

Nothing caught it. The backend tests passed because they only tested routes that
existed; the frontend compiled because TypeScript cannot see across the wire. The gap
was exactly the shape neither side was looking at.

So this reads the paths out of `frontend/src/lib/api.ts` and asserts FastAPI can route
each one. It is deliberately crude — a scan of a source file — because the alternative
is a generated client, and that is a bigger change than this bug warrants. Crude in the
direction of *missing* a problem, never of inventing one: a test that cries wolf gets
deleted, and then the next missing endpoint ships too.
"""

from __future__ import annotations

import pathlib

import pytest

API_TS = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "api.ts"

_CALL = "authorised("
_ANY = "\x00"  # stands for an interpolated path segment


def _literals(source: str) -> list[str]:
    """Every string or template literal passed first to `authorised(...)`."""
    out = []
    start = 0
    while (found := source.find(_CALL, start)) != -1:
        cursor = found + len(_CALL)
        while cursor < len(source) and source[cursor] in " \n\t":
            cursor += 1
        if cursor < len(source) and source[cursor] in "`\"'":
            quote = source[cursor]
            end = source.find(quote, cursor + 1)
            if end != -1:
                out.append(source[cursor + 1 : end])
        start = cursor or found + 1
    return out


def _normalise(literal: str) -> str | None:
    """A template literal, reduced to a comparable path.

    An interpolation that forms a whole segment (`/rows/${id}`) becomes a wildcard. One
    glued to a literal (`/rows${query}`) is a query string being appended, so the path
    ends there. Mixing those up is what produced '/review/rowsx'.
    """
    if not literal.startswith("/"):
        return None

    out: list[str] = []
    index = 0
    while index < len(literal):
        if literal.startswith("${", index):
            close = literal.find("}", index)
            if close == -1:
                break
            if out and out[-1] == "/":
                out.append(_ANY)
                index = close + 1
                continue
            return "".join(out)  # a suffix, not a segment
        if literal[index] == "?":
            break
        out.append(literal[index])
        index += 1
    return "".join(out)


def _routes() -> list[list[str]]:
    from app.main import app

    return [
        route.path.split("/")
        for route in app.routes
        if getattr(route, "path", None)
    ]


def _matches(called: list[str], route: list[str]) -> bool:
    """Wildcards run both ways.

    The frontend interpolates where the backend has a literal (`/matches/${kind}` against
    `/matches/fill`), and the backend has a parameter where the frontend sends a literal.
    Checking only one direction reports working routes as missing.
    """
    if len(called) != len(route):
        return False
    return all(
        here == there or here == _ANY or (there.startswith("{") and there.endswith("}"))
        for here, there in zip(called, route, strict=True)
    )


def _frontend_paths() -> set[str]:
    source = API_TS.read_text(encoding="utf-8")
    return {path for literal in _literals(source) if (path := _normalise(literal))}


@pytest.mark.parametrize("path", sorted(_frontend_paths()))
def test_the_backend_can_route_it(path: str):
    called = path.split("/")
    assert any(_matches(called, route) for route in _routes()), (
        f"frontend/src/lib/api.ts calls {path.replace(_ANY, '{id}')!r} and no backend "
        "route matches it. This is the Review-tab-404 bug: add the endpoint, or stop "
        "calling it."
    )


def test_the_scan_found_something():
    """A scan that silently matched nothing would make every assertion above vacuous."""
    paths = _frontend_paths()
    assert len(paths) >= 8, f"only found {paths} — the scan is probably broken"


def test_the_scan_would_notice_a_missing_route():
    """Guards the guard. If this passes while the rest pass, the check has teeth."""
    assert not any(_matches("/review/nonsense".split("/"), route) for route in _routes())
