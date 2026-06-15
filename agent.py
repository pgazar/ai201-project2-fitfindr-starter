"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        "relaxed": None,             # note if constraints were loosened on retry
    }


# ── query parsing (deterministic, regex-first) ───────────────────

# Unambiguous standalone letter sizes we accept WITHOUT the word "size".
_STANDALONE_SIZES = {"xs", "xl", "xxl", "xxxl"}


def _parse_query(query: str) -> dict:
    """
    Extract description / size / max_price from a free-text query.

    Prices and sizes are structured tokens that regex handles deterministically;
    whatever is left over becomes the description keywords. Matches planning.md >
    Query Parsing.
    """
    q = query
    spans_to_strip = []

    # --- max_price ---
    max_price = None
    price_patterns = [
        r"under\s*\$?\s*(\d+(?:\.\d+)?)",
        r"below\s*\$?\s*(\d+(?:\.\d+)?)",
        r"\$\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*dollars?",
    ]
    for pat in price_patterns:
        m = re.search(pat, q, re.IGNORECASE)
        if m:
            max_price = float(m.group(1))
            spans_to_strip.append(m.span())
            break

    # --- size ---
    size = None
    m = re.search(r"\bsize\s+([A-Za-z0-9/.]+)", q, re.IGNORECASE)
    if m:
        size = m.group(1).upper()
        spans_to_strip.append(m.span())
    else:
        for tok_m in re.finditer(r"\b([A-Za-z]{1,4})\b", q):
            if tok_m.group(1).lower() in _STANDALONE_SIZES:
                size = tok_m.group(1).upper()
                spans_to_strip.append(tok_m.span())
                break

    # --- description = query minus the matched size/price spans ---
    chars = list(q)
    for start, end in spans_to_strip:
        for idx in range(start, end):
            chars[idx] = " "
    description = re.sub(r"\s+", " ", "".join(chars)).strip(" ,.")

    return {"description": description, "size": size, "max_price": max_price}


# ── no-results fallback (stretch) ───────────────────────────────

def _fallback_attempts(parsed: dict) -> list[tuple]:
    """
    Ordered relaxations to try when the exact search returns nothing.

    Each entry is (note, size, max_price). Only relaxations that actually change
    the query are included (no point "dropping" a filter that was never set).
    Order: drop size first, then the price limit, then both.
    """
    size = parsed["size"]
    price = parsed["max_price"]
    attempts = []
    if size is not None:
        attempts.append((f"removed the size filter (size {size})", None, price))
    if price is not None:
        attempts.append((f"removed the ${price:g} price limit", size, None))
    if size is not None and price is not None:
        attempts.append(("removed both the size and price filters", None, None))
    return attempts


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1: initialize state
    session = _new_session(query, wardrobe)

    # Step 2: parse the query into description / size / max_price
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]

    # Step 3: search (the gate before any LLM call)
    results = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )

    # Stretch: if the exact search is empty, retry with loosened constraints
    # before giving up, and record what was adjusted.
    if not results:
        for note, size, price in _fallback_attempts(parsed):
            retry = search_listings(parsed["description"], size, price)
            if retry:
                results = retry
                session["relaxed"] = (
                    f"No exact match for '{parsed['description']}', so I {note} "
                    "and found these instead."
                )
                break

    session["search_results"] = results

    if not results:
        # Every relaxation still failed -> stop with advice, skip the LLM tools.
        bits = []
        if parsed["size"]:
            bits.append(f"size {parsed['size']}")
        if parsed["max_price"] is not None:
            bits.append(f"under ${parsed['max_price']:g}")
        filt = f" ({', '.join(bits)})" if bits else ""
        session["error"] = (
            f"No listings matched '{parsed['description']}'{filt}, even after "
            "loosening the filters. Try different keywords."
        )
        return session  # STOP — do NOT call suggest_outfit / create_fit_card

    # Step 4: select the top-scored listing
    session["selected_item"] = results[0]

    # Step 5: suggest an outfit from the selected item + wardrobe
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6: turn the outfit into a shareable fit card
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: done
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        if session["relaxed"]:
            print(f"Note: {session['relaxed']}\n")
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")

    print("\n\n=== Relaxation path: impossible size, loosened on retry ===\n")
    session3 = run_agent(
        query="vintage graphic tee size XS",
        wardrobe=get_example_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Relaxed: {session3['relaxed']}")
        print(f"Found: {session3['selected_item']['title']}")
