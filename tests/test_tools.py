# tests/test_tools.py
#
# Unit tests for the three FitFindr tools, with at least one test per failure
# mode described in planning.md.
#
# Design note: the LLM-backed tools (suggest_outfit, create_fit_card) are tested
# with tools._chat monkeypatched, so the suite is deterministic, fast, free, and
# runs without a GROQ_API_KEY or any network. We test the real branch logic and
# the real fallback/guard paths; live model output is verified separately.

import pytest

import tools
from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    _build_outfit_messages,
    _build_fitcard_messages,
)
from utils.data_loader import (
    load_listings,
    get_example_wardrobe,
    get_empty_wardrobe,
)

SAMPLE_ITEM = load_listings()[0]


# --------------- Tool 1: search_listings (deterministic, no LLM) ---------------

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0
    assert all("title" in r and "price" in r for r in results)


def test_search_empty_results():
    # Failure mode: no match -> empty list, never raises.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_whole_token_not_substring():
    # Failure mode guard: size "8" must NOT match a size string like "W28".
    results = search_listings("jeans", size="8", max_price=None)
    assert all(item["size"] != "W28" for item in results)


def test_search_size_letter_matches_compound():
    # "M" should still match a compound size like "S/M" (whole-token, not substring).
    results = search_listings("", size="M", max_price=None)
    assert any("M" in item["size"] for item in results)


def test_search_sorted_by_relevance():
    results = search_listings("graphic tee", size=None, max_price=None)
    if len(results) >= 1:
        top = results[0]
        blob = (top["title"] + " " + " ".join(top["style_tags"])).lower()
        assert "tee" in blob or "graphic" in blob


# --------------- Tool 2: suggest_outfit (LLM mocked) ---------------

def test_suggest_outfit_with_wardrobe_uses_named_pieces(monkeypatch):
    # Happy path: with a wardrobe, the prompt asks for specific named pieces.
    example = get_example_wardrobe()
    prompt = _build_outfit_messages(SAMPLE_ITEM, example)[1]["content"]
    names = [it["name"] for it in example["items"]]
    assert "SPECIFIC named items" in prompt
    assert any(n in prompt for n in names)

    monkeypatch.setattr(tools, "_chat",
                        lambda messages, temperature, **k: "MOCK OUTFIT")
    assert suggest_outfit(SAMPLE_ITEM, example) == "MOCK OUTFIT"


def test_suggest_outfit_empty_wardrobe_general_advice(monkeypatch):
    # Failure mode: empty wardrobe -> general-advice prompt, no named pieces.
    empty = get_empty_wardrobe()
    prompt = _build_outfit_messages(SAMPLE_ITEM, empty)[1]["content"]
    assert "haven't told us" in prompt

    monkeypatch.setattr(tools, "_chat",
                        lambda messages, temperature, **k: "GENERAL ADVICE")
    out = suggest_outfit(SAMPLE_ITEM, empty)
    assert isinstance(out, str) and len(out) > 0


def test_suggest_outfit_api_error_returns_fallback(monkeypatch):
    # Failure mode: API/LLM error -> caught, non-empty fallback (no exception).
    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(tools, "_chat", boom)
    out = suggest_outfit(SAMPLE_ITEM, get_example_wardrobe())
    assert isinstance(out, str) and len(out) > 0
    assert "unavailable" in out.lower()


# --------------- Tool 3: create_fit_card (LLM mocked) ---------------

def test_create_fit_card_empty_outfit_guard():
    # Failure mode: empty/whitespace outfit -> guard message, not a caption.
    expected = "Can't make a fit card without an outfit suggestion to base it on."
    assert create_fit_card("", SAMPLE_ITEM) == expected
    assert create_fit_card("   ", SAMPLE_ITEM) == expected


def test_create_fit_card_prompt_has_item_details():
    outfit = "jeans + white tank + boots"
    prompt = _build_fitcard_messages(outfit, SAMPLE_ITEM)[1]["content"]
    assert SAMPLE_ITEM["title"] in prompt
    assert str(SAMPLE_ITEM["price"]) in prompt
    assert SAMPLE_ITEM["platform"] in prompt
    assert outfit in prompt


def test_create_fit_card_happy_path(monkeypatch):
    monkeypatch.setattr(tools, "_chat",
                        lambda messages, temperature, **k: "MOCK CAPTION")
    assert create_fit_card("a real outfit", SAMPLE_ITEM) == "MOCK CAPTION"


def test_create_fit_card_api_error_returns_fallback(monkeypatch):
    # Failure mode: API error -> caught, fallback caption built from item fields.
    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(tools, "_chat", boom)
    out = create_fit_card("a real outfit", SAMPLE_ITEM)
    assert isinstance(out, str) and len(out) > 0
    assert SAMPLE_ITEM["title"] in out


# ─────────────── Tool 4: find_similar_listings (deterministic) ───────────────

from tools import find_similar_listings


def test_find_similar_returns_list_excluding_self():
    sim = find_similar_listings(SAMPLE_ITEM)
    assert isinstance(sim, list)
    assert all(s["id"] != SAMPLE_ITEM["id"] for s in sim)


def test_find_similar_respects_limit():
    assert len(find_similar_listings(SAMPLE_ITEM, limit=2)) <= 2


def test_find_similar_shares_a_signal():
    seed_cat = SAMPLE_ITEM["category"]
    seed_tags = {t.lower() for t in SAMPLE_ITEM["style_tags"]}
    seed_colors = {c.lower() for c in SAMPLE_ITEM["colors"]}
    for s in find_similar_listings(SAMPLE_ITEM):
        shares = (
            s["category"] == seed_cat
            or bool(seed_tags & {t.lower() for t in s["style_tags"]})
            or bool(seed_colors & {c.lower() for c in s["colors"]})
        )
        assert shares


def test_find_similar_empty_when_nothing_matches():
    alien = {"id": "zzz-none", "category": "spacesuit",
             "style_tags": ["martian"], "colors": ["ultraviolet"]}
    assert find_similar_listings(alien) == []
