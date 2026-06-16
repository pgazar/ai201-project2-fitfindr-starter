# tests/test_agent.py
#
# Tests for the planning loop, including the no-results retry/fallback (stretch).
# The LLM tools are stubbed so the suite stays deterministic and offline.

import agent
from agent import run_agent, _fallback_attempts
from utils.data_loader import get_example_wardrobe


def _stub_llm(monkeypatch):
    """Replace the two LLM tools with deterministic stubs; count their calls."""
    calls = {"suggest": 0, "fitcard": 0}

    def fake_suggest(item, wardrobe):
        calls["suggest"] += 1
        return "OUTFIT"

    def fake_fitcard(outfit, item):
        calls["fitcard"] += 1
        return "CARD"

    monkeypatch.setattr(agent, "suggest_outfit", fake_suggest)
    monkeypatch.setattr(agent, "create_fit_card", fake_fitcard)
    return calls


# ─────────────── _fallback_attempts (pure logic) ───────────────

def test_fallback_attempts_order_with_both_filters():
    attempts = _fallback_attempts({"description": "x", "size": "XS", "max_price": 30.0})
    # drop size, then price, then both
    assert len(attempts) == 3
    assert attempts[0][1] is None and attempts[0][2] == 30.0      # size dropped
    assert attempts[1][1] == "XS" and attempts[1][2] is None      # price dropped
    assert attempts[2][1] is None and attempts[2][2] is None      # both dropped


def test_fallback_attempts_only_size():
    attempts = _fallback_attempts({"description": "x", "size": "XS", "max_price": None})
    assert len(attempts) == 1
    assert attempts[0][1] is None and attempts[0][2] is None


def test_fallback_attempts_none_when_no_filters():
    attempts = _fallback_attempts({"description": "x", "size": None, "max_price": None})
    assert attempts == []


# ─────────────── run_agent paths ───────────────

def test_happy_path_no_relaxation(monkeypatch):
    _stub_llm(monkeypatch)
    s = run_agent("vintage graphic tee under $30", get_example_wardrobe())
    assert s["error"] is None
    assert s["relaxed"] is None              # exact match, nothing loosened
    assert s["selected_item"] is not None
    assert s["fit_card"] == "CARD"


def test_relaxation_drops_size(monkeypatch):
    # No graphic tee exists in size XS -> agent should drop the size filter.
    _stub_llm(monkeypatch)
    s = run_agent("vintage graphic tee size XS", get_example_wardrobe())
    assert s["error"] is None
    assert s["relaxed"] is not None
    assert "size" in s["relaxed"].lower()
    assert len(s["search_results"]) > 0
    assert s["fit_card"] == "CARD"


def test_no_match_even_after_loosening(monkeypatch):
    calls = _stub_llm(monkeypatch)
    s = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())
    assert s["error"] is not None
    assert "even after loosening" in s["error"]
    assert s["relaxed"] is None
    assert s["selected_item"] is None
    assert s["outfit_suggestion"] is None and s["fit_card"] is None
    # LLM tools must NOT be called when nothing is found
    assert calls["suggest"] == 0 and calls["fitcard"] == 0


# ─────────────── retry/fallback — extended coverage ───────────────

def test_relaxation_drops_price_only(monkeypatch):
    # "leather bomber under $10": the bomber exists but costs more -> drop price.
    _stub_llm(monkeypatch)
    s = run_agent("leather bomber under $10", get_example_wardrobe())
    assert s["error"] is None
    assert s["relaxed"] is not None
    assert "price" in s["relaxed"].lower()
    assert "size" not in s["relaxed"].lower()      # only the price limit was dropped
    assert len(s["search_results"]) > 0
    assert s["fit_card"] == "CARD"


def test_relaxation_drops_both(monkeypatch):
    # Wrong size AND over budget -> only dropping both filters yields results.
    _stub_llm(monkeypatch)
    s = run_agent("leather bomber size XS under $10", get_example_wardrobe())
    assert s["error"] is None
    assert s["relaxed"] is not None
    assert "both" in s["relaxed"].lower()
    assert len(s["search_results"]) > 0


def test_exact_match_with_filters_does_not_relax(monkeypatch):
    # A track jacket in size M exists -> exact match, the ladder never runs.
    _stub_llm(monkeypatch)
    s = run_agent("track jacket size M", get_example_wardrobe())
    assert s["error"] is None
    assert s["relaxed"] is None


def test_relaxed_results_are_stored_and_selected(monkeypatch):
    # The relaxed results are what gets stored, and the top one is selected.
    _stub_llm(monkeypatch)
    s = run_agent("vintage graphic tee size XS", get_example_wardrobe())
    assert len(s["search_results"]) > 0
    assert s["selected_item"] is s["search_results"][0]


def test_relaxation_still_runs_llm_tools(monkeypatch):
    # After a successful relaxation the loop must continue to BOTH LLM tools.
    calls = _stub_llm(monkeypatch)
    run_agent("vintage graphic tee size XS", get_example_wardrobe())
    assert calls["suggest"] == 1
    assert calls["fitcard"] == 1


def test_size_relaxation_note_wording(monkeypatch):
    # The note should name exactly which filter was dropped.
    _stub_llm(monkeypatch)
    s = run_agent("vintage graphic tee size XS", get_example_wardrobe())
    assert "removed the size filter" in s["relaxed"]


# ─────────────── relaxation preserves item type (regression) ───────────────

from agent import _head_noun, _is_on_type


def test_head_noun_is_last_content_token():
    assert _head_noun("black combat boots") == "boots"
    assert _head_noun("vintage graphic tee") == "tee"
    assert _head_noun("") is None


def test_is_on_type_matches_type_fields_only():
    boots = {"title": "Suede Chelsea Boots — Tan", "style_tags": ["boots"], "category": "shoes"}
    shorts = {"title": "Biker Shorts — Black, Shiny", "style_tags": ["y2k"], "category": "bottoms"}
    assert _is_on_type(boots, "boots") is True
    assert _is_on_type(shorts, "boots") is False


def test_relaxation_preserves_item_type_boots(monkeypatch):
    # Regression: "black combat boots" must not relax into black shorts/tops.
    # The only boots are over $40, so the agent should drop the PRICE (not size)
    # and return a shoes item — never a bottoms/tops item.
    _stub_llm(monkeypatch)
    s = run_agent("black combat boots size 8, UNDER $40", get_example_wardrobe())
    assert s["error"] is None
    assert s["relaxed"] is not None
    assert "price" in s["relaxed"].lower()
    assert s["selected_item"]["category"] == "shoes"
    assert "boot" in s["selected_item"]["title"].lower()
    # no off-type items leaked into the results
    assert all(it["category"] == "shoes" for it in s["search_results"])


# ─────────────── Tool 4 integration: similar_listings ───────────────

def test_similar_listings_populated_on_happy_path(monkeypatch):
    _stub_llm(monkeypatch)
    s = run_agent("vintage graphic tee under $30", get_example_wardrobe())
    assert isinstance(s["similar_listings"], list)
    assert len(s["similar_listings"]) > 0
    assert s["selected_item"]["id"] not in [x["id"] for x in s["similar_listings"]]


def test_similar_listings_empty_on_no_results(monkeypatch):
    _stub_llm(monkeypatch)
    s = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())
    assert s["similar_listings"] == []
