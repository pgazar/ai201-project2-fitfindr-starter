#!/usr/bin/env python
"""
manual_test.py — eyeball-test every FitFindr tool end to end.

Run from the project root with the venv active:
    python manual_test.py

Makes real Groq calls if GROQ_API_KEY is set in .env; otherwise the LLM tools
fall back to safe placeholder text (the script still runs).
"""

import os

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import (
    load_listings,
    get_example_wardrobe,
    get_empty_wardrobe,
)


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def show_listing(item):
    print(f"  • {item['title']}  —  ${item['price']:g}  ·  size {item['size']}"
          f"  ·  {item['platform']}")


def main():
    key = bool(os.environ.get("GROQ_API_KEY"))
    print(f"GROQ_API_KEY detected: {key}  "
          f"({'real LLM output' if key else 'fallback text only'})")

    # ---------------------------------------------------------------- TOOL 1
    hr("TOOL 1 · search_listings — a few queries")

    queries = [
        ("vintage graphic tee", None, 30.0),   # keyword + price
        ("track jacket",        "M",  None),   # keyword + size
        ("flowy midi skirt",    None, None),   # keyword only
        ("black combat boots",  "8",  None),   # numeric size (8 must not match W28)
        ("designer ballgown",   "XXS", 5.0),   # deliberate no-match
    ]
    for desc, size, price in queries:
        results = search_listings(desc, size=size, max_price=price)
        label = f'"{desc}"' + (f"  size={size}" if size else "") + \
                (f"  <= ${price:g}" if price else "")
        print(f"\n{label}  ->  {len(results)} hit(s)")
        for item in results[:3]:
            show_listing(item)
        if not results:
            print("  (empty list — agent would set session['error'] and stop here)")

    # ---------------------------------------------- END-TO-END (happy path)
    hr("END TO END · search -> pick top -> suggest_outfit -> create_fit_card")

    user_query_desc = "vintage graphic tee"
    results = search_listings(user_query_desc, size=None, max_price=30.0)
    selected = results[0]
    print(f"\nUser wants: '{user_query_desc}' under $30")
    print("Top match selected as session['selected_item']:")
    show_listing(selected)

    example = get_example_wardrobe()
    print(f"\n[suggest_outfit] with example wardrobe ({len(example['items'])} items):\n")
    outfit = suggest_outfit(selected, example)
    print(outfit)

    print("\n[create_fit_card] from that outfit:\n")
    print(create_fit_card(outfit, selected))

    # ------------------------------------------------ TOOL 2 · empty wardrobe
    hr("TOOL 2 · suggest_outfit — EMPTY wardrobe (general advice branch)")
    empty = get_empty_wardrobe()
    print(suggest_outfit(selected, empty))

    # ------------------------------------------------- TOOL 3 · failure modes
    hr("TOOL 3 · create_fit_card — failure modes")
    print("empty outfit string  -> ", create_fit_card("", selected))
    print("whitespace outfit    -> ", create_fit_card("   ", selected))

    hr("DONE")
    print("If the outfit names real wardrobe pieces, the empty-wardrobe answer is")
    print("generic, captions vary on re-run, and the no-match query returned [],")
    print("then all three tools are behaving per planning.md.")


if __name__ == "__main__":
    main()
