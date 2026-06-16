"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# Model used for the LLM-backed tools. llama-3.3-70b-versatile is a current,
# production-ready Groq model with good copy quality. Swap to
# "llama-3.1-8b-instant" for lower latency/cost.
MODEL = "llama-3.3-70b-versatile"


def _chat(messages: list[dict], temperature: float, max_tokens: int = 400) -> str:
    """Thin wrapper around a Groq chat completion. Returns the text content."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


# ── Tool 1: search_listings ───────────────────────────────────────────────────

# Words that carry no search signal; dropped before keyword matching.
_STOPWORDS = {
    "a", "an", "the", "for", "in", "of", "with", "and", "or", "to", "i", "im",
    "looking", "want", "wants", "need", "needs", "some", "something", "that",
    "this", "my", "me", "you", "what", "whats", "how", "out", "there", "mostly",
    "wear", "wears", "style", "styled", "it", "is", "are", "any", "find", "show",
    "size", "under", "below", "around", "about", "please", "would", "like",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and pure numbers."""
    raw = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in raw if t not in _STOPWORDS and not t.isdigit()]


def _score(listing: dict, keywords: list[str]) -> int:
    """
    Weighted keyword-overlap score. Matches in the title and style tags are
    worth more than matches buried in the free-text description.
    """
    title = listing.get("title", "").lower()
    desc = listing.get("description", "").lower()
    category = listing.get("category", "").lower()
    tags = " ".join(listing.get("style_tags", [])).lower()
    colors = " ".join(listing.get("colors", [])).lower()
    brand = (listing.get("brand") or "").lower()

    score = 0
    for kw in keywords:
        if kw in tags:
            score += 3
        if kw in title:
            score += 3
        if kw in category:
            score += 2
        if kw in colors or kw in brand:
            score += 1
        if kw in desc:
            score += 1
    return score


def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    keywords = _tokenize(description)

    results = []
    for listing in listings:
        # Hard filters first.
        if max_price is not None and listing.get("price", 0) > max_price:
            continue
        if size is not None:
            # Whole-token match (bounded by non-alphanumerics) so "M" matches
            # "S/M" but "8" does NOT match "W28". Case-insensitive.
            pattern = (r"(?<![A-Za-z0-9])"
                       + re.escape(size.strip())
                       + r"(?![A-Za-z0-9])")
            if not re.search(pattern, str(listing.get("size", "")), re.IGNORECASE):
                continue

        # Relevance scoring. If the user gave no usable keywords (e.g. just a
        # size/price filter), keep all listings that passed the hard filters.
        if keywords:
            s = _score(listing, keywords)
            if s == 0:
                continue
        else:
            s = 0
        results.append((s, listing))

    # Highest score first; cheaper price as the tiebreaker.
    results.sort(key=lambda pair: (-pair[0], pair[1].get("price", 0)))
    return [listing for _, listing in results]


# ── Tool 2: suggest_outfit ────────────────────────────────

def _format_item(item: dict) -> str:
    """One-line description of a listing, for use inside prompts."""
    return (
        f"{item.get('title', '(untitled)')} "
        f"(category: {item.get('category', '')}, "
        f"colors: {', '.join(item.get('colors', []))}, "
        f"style: {', '.join(item.get('style_tags', []))}, "
        f"${item.get('price', '?')}, on {item.get('platform', '')})"
    )


def _format_wardrobe(wardrobe: dict) -> str:
    """Bulleted list of the user's wardrobe items, for use inside prompts."""
    lines = []
    for item in wardrobe.get("items", []):
        tags = ", ".join(item.get("style_tags", []))
        colors = ", ".join(item.get("colors", []))
        note = f" (note: {item['notes']})" if item.get("notes") else ""
        lines.append(
            f"- {item.get('name', 'item')} [{item.get('category', '')}; "
            f"colors: {colors}; style: {tags}]{note}"
        )
    return "\n".join(lines)


def _build_outfit_messages(new_item: dict, wardrobe: dict) -> list[dict]:
    """
    Build the chat messages for suggest_outfit. Branches on whether the wardrobe
    has items: specific named-piece combinations vs. general styling advice.
    Kept separate from the API call so the branch logic is testable without a key.
    """
    item_desc = _format_item(new_item)
    items = wardrobe.get("items", []) if wardrobe else []

    if not items:
        user_prompt = (
            f"A shopper is considering this secondhand piece:\n{item_desc}\n\n"
            "They haven't told us what's in their wardrobe. Suggest how to style "
            "this piece: what kinds of items pair well, what vibe it suits, and "
            "one or two outfit directions. Keep it short and practical."
        )
    else:
        user_prompt = (
            f"A shopper is considering this secondhand piece:\n{item_desc}\n\n"
            f"Their wardrobe:\n{_format_wardrobe(wardrobe)}\n\n"
            "Suggest 1-2 complete outfits that combine the new piece with SPECIFIC "
            "named items from their wardrobe. Reference pieces by name. Keep it short."
        )

    return [
        {"role": "system", "content": "You are a sharp, friendly thrift and "
         "styling assistant. Be concrete and concise."},
        {"role": "user", "content": user_prompt},
    ]


def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Uses specific named wardrobe pieces when the wardrobe is non-empty; gives
    general styling advice when it's empty. Always returns a non-empty string —
    on an API error it returns a safe fallback instead of raising, so the agent
    loop can continue.
    """
    messages = _build_outfit_messages(new_item, wardrobe)
    try:
        return _chat(messages, temperature=0.4)
    except Exception as e:
        return (
            f"(Styling assistant temporarily unavailable: {e}) "
            f"For now, {new_item.get('title', 'this piece')} works with neutral "
            "basics and one contrasting layer."
        )


# ── Tool 3: create_fit_card ────────────────────────────────

def _build_fitcard_messages(outfit: str, new_item: dict) -> list[dict]:
    """
    Build the chat messages for create_fit_card. Separated from the API call so
    the prompt can be inspected without a key. Assumes `outfit` is non-empty
    (the empty-input guard lives in create_fit_card itself).
    """
    title = new_item.get("title", "this find")
    price = new_item.get("price", "?")
    platform = new_item.get("platform", "")

    user_prompt = (
        f"Write a 2-4 sentence caption for a thrifted-outfit post.\n\n"
        f"Item: {title} (${price}, found on {platform})\n"
        f"Outfit: {outfit}\n\n"
        "Style: casual and authentic like a real OOTD post (not a product "
        "description). Mention the item name, price, and platform naturally, once "
        "each. Capture the vibe in specific terms. No hashtag walls."
    )
    return [
        {"role": "system", "content": "You write fun, authentic outfit captions "
         "for thrift finds."},
        {"role": "user", "content": user_prompt},
    ]


def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, casual, shareable OOTD caption for the thrifted find.

    Returns a descriptive error string (never raises) if `outfit` is empty or
    whitespace-only. On an API error it returns a fallback caption assembled
    from the item's own fields, so the user still gets something shareable.
    Uses a high temperature (0.9) so captions vary across runs and inputs.
    """
    if not outfit or not outfit.strip():
        return "Can't make a fit card without an outfit suggestion to base it on."

    title = new_item.get("title", "this find")
    price = new_item.get("price", "?")
    platform = new_item.get("platform", "")

    messages = _build_fitcard_messages(outfit, new_item)
    try:
        return _chat(messages, temperature=0.9)
    except Exception as e:
        return (
            f"Just thrifted {title} for ${price} on {platform} and I'm obsessed. "
            f"Styled it up and it's giving exactly what I wanted. "
            f"(caption generator offline: {e})"
        )


# ── Tool 4: find_similar_listings (extended) ──────────────────────────────────

def find_similar_listings(new_item: dict, limit: int = 3) -> list[dict]:
    """
    Find other listings similar to `new_item` — a "you might also like" tool.
    Deterministic (no LLM): scores every other listing by shared category, shared
    style tags, and shared colors, excludes the item itself, and returns the top
    matches.

    Args:
        new_item: the selected listing dict.
        limit:    max number of similar listings to return (default 3).

    Returns:
        A list of up to `limit` listing dicts, most similar first (cheaper price as
        the tiebreaker). Returns [] if nothing shares any signal with new_item.
    """
    listings = load_listings()
    item_id = new_item.get("id")
    item_cat = new_item.get("category", "")
    item_tags = {t.lower() for t in new_item.get("style_tags", [])}
    item_colors = {c.lower() for c in new_item.get("colors", [])}

    scored = []
    for other in listings:
        if other.get("id") == item_id:
            continue  # never suggest the item itself
        score = 0
        if item_cat and other.get("category") == item_cat:
            score += 3
        score += 2 * len(item_tags & {t.lower() for t in other.get("style_tags", [])})
        score += len(item_colors & {c.lower() for c in other.get("colors", [])})
        if score > 0:
            scored.append((score, other))

    scored.sort(key=lambda pair: (-pair[0], pair[1].get("price", 0)))
    return [listing for _, listing in scored[:limit]]
