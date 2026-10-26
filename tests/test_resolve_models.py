"""The model resolver: asked only what code allows, every answer kept, people win."""

import pytest

from app.brain import resolve_models
from app.brain.llm.client import Answer
from app.brain.resolve_models import (
    ask_about,
    effective_aliases,
    pending_questions,
    resolve_new_models,
)
from app.db.memory import InMemoryRepository
from app.db.models import TenantConfig

BOARD = [
    ("ONEPLUS NORD CE6 256GB 8GB (CPH2807) NEW", "OnePlus", "Black"),
    ("JBL TUNE 720BT", "JBL", "Black"),
    ("Samsung Galaxy S23 128GB", "Samsung", None),
    ("Samsung Galaxy S23 Ultra 256GB", "Samsung", None),
]


def test_only_descriptive_differences_become_questions():
    questions = pending_questions(
        [
            ("OnePlus Nord CE6 Smooth 256GB", "OnePlus", None),
            ("JBL Tune 720BT Deluxe", "JBL", "Black"),
            # Restates the product type only: the same key as the board's line, no question.
            ("JBL Tune 720BT Wireless Over-Ear Headphones", "JBL", "Black"),
            ("Samsung S23 Ultra", "Samsung", None),   # already exactly on the board
            ("Samsung S23 FE", "Samsung", None),      # a variant word: never asked
        ],
        BOARD,
        {},
    )
    asked = {q.key: [c[0] for c in q.candidates] for q in questions}
    assert asked == {
        "oneplus:nordce6smooth": ["oneplus:nordce6"],
        "jbl:tune720btdeluxe": ["jbl:tune720bt"],
    }


def test_a_settled_wording_is_not_asked_again():
    questions = pending_questions(
        [("OnePlus Nord CE6 Smooth", "OnePlus", None)], BOARD, {"oneplus:nordce6smooth": None}
    )
    assert questions == []


@pytest.fixture
def model_says(monkeypatch):
    def install(text):
        calls = []

        def fake(system, user, **kwargs):
            calls.append(user)
            return Answer(text=text, model="test-model")

        monkeypatch.setattr(resolve_models, "ask", fake)
        monkeypatch.setattr(resolve_models, "available", lambda: True)
        return calls

    return install


def test_a_yes_maps_the_longer_wording_onto_the_shorter(model_says):
    model_says('{"answers": [{"id": 1, "match": 1}]}')
    questions = pending_questions([("OnePlus Nord CE6 Smooth", "OnePlus", None)], BOARD, {})
    rows = ask_about(questions)
    assert rows == [{
        "from_key": "oneplus:nordce6smooth", "to_key": "oneplus:nordce6", "source": "model",
        "model": "test-model", "note": "OnePlus Nord CE6 Smooth",
    }]


def test_a_no_is_stored_too(model_says):
    model_says('Sure. {"answers": [{"id": 1, "match": 0}]}')
    questions = pending_questions([("OnePlus Nord CE6 Smooth", "OnePlus", None)], BOARD, {})
    assert ask_about(questions)[0]["to_key"] is None


def test_a_reply_out_of_range_or_unreadable_stores_nothing(model_says):
    questions = pending_questions([("OnePlus Nord CE6 Smooth", "OnePlus", None)], BOARD, {})
    model_says('{"answers": [{"id": 1, "match": 7}]}')
    assert ask_about(questions) == []
    model_says("I think they are the same")
    assert ask_about(questions) == []


def test_without_a_model_configured_nothing_is_asked():
    questions = pending_questions([("OnePlus Nord CE6 Smooth", "OnePlus", None)], BOARD, {})
    assert ask_about(questions) == []


def test_a_person_is_never_overruled(model_says):
    repo = InMemoryRepository([TenantConfig(id="t1")])
    repo.save_model_aliases("t1", [{"from_key": "oneplus:nordce6smooth", "to_key": None,
                                    "source": "human"}])
    repo.save_model_aliases("t1", [{"from_key": "oneplus:nordce6smooth",
                                    "to_key": "oneplus:nordce6", "source": "model"}])
    assert repo.load_model_aliases("t1") == {"oneplus:nordce6smooth": None}


def test_resolution_never_fails_an_email(model_says):
    class Broken(InMemoryRepository):
        def model_catalogue(self, tenant_id):
            raise RuntimeError("database down")

    model_says('{"answers": []}')
    from app.brain.llm import UsageLog

    assert resolve_new_models(Broken(), "t1", [("x", None, None)], UsageLog()) == 0


def test_chains_resolve_to_the_final_model_and_differences_drop_out():
    raw = {"a:x1long": "a:x1mid", "a:x1mid": "a:x1", "a:y1": None}
    assert effective_aliases(raw) == {"a:x1long": "a:x1", "a:x1mid": "a:x1"}
