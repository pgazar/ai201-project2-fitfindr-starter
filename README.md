# FitFindr 🛍️

FitFindr is an agentic LLM app for secondhand shopping. You describe what you want in
plain language ("vintage graphic tee under $30, size M"); the agent finds a matching
listing, styles it against your wardrobe, and writes a shareable outfit caption. It runs
on Groq for inference and Gradio for the UI.

The agent chains three tools through a deterministic planning loop, passing state between
them in a single session dict. The full design — spec, agent diagram, and decision rationale
— lives in [`planning.md`](planning.md).

---

## Setup

```bash
pip install -r requirements.txt   # plus `pip install pytest` to run the test suite
```

Add a Groq API key (free at [console.groq.com](https://console.groq.com)) to a `.env` file
in the project root:

```
GROQ_API_KEY=your_key_here
```

## Running it

```bash
python app.py        # launches the Gradio UI (open the URL it prints, usually http://localhost:7860)
python agent.py      # CLI: runs the happy path, the no-results path, and a relaxation path
python manual_test.py  # end-to-end eyeball test of all three tools
pytest tests/        # 25 unit tests (LLM calls stubbed, so no key/network needed)
```

---

## Tool Inventory

### 1. `search_listings(description, size, max_price) -> list[dict]`
Deterministic keyword search over the 40-item mock dataset — **no LLM call**, so it is fast,
free, and can't hallucinate listings that don't exist.

- `description` (`str`): free-text keywords, e.g. `"vintage graphic tee"`.
- `size` (`str | None`): size to filter by, matched case-insensitively as a *whole token*
  (so `"M"` matches `"S/M"` but `"8"` does **not** match `"W28"`). `None` skips the filter.
- `max_price` (`float | None`): inclusive price ceiling in USD. `None` skips the filter.

**Returns:** a list of full listing dicts (`id`, `title`, `description`, `category`,
`style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted by
weighted keyword overlap (matches in `title`/`style_tags` count most), cheapest first as a
tiebreaker. Returns `[]` when nothing matches.

### 2. `suggest_outfit(new_item, wardrobe) -> str`
LLM-backed styling (temperature 0.4 for grounded, consistent advice).

- `new_item` (`dict`): the selected listing dict.
- `wardrobe` (`dict`): `{"items": [ ... ]}`, where each item has `name`, `category`,
  `colors`, `style_tags`, and optional `notes`. May be empty.

**Returns:** a non-empty `str` with 1–2 outfit ideas. With a wardrobe it references specific
named pieces; with an empty wardrobe it gives general styling advice.

### 3. `create_fit_card(outfit, new_item) -> str`
LLM-backed caption writing (temperature 0.9 for variety across runs).

- `outfit` (`str`): the suggestion text from `suggest_outfit`.
- `new_item` (`dict`): the listing dict (for item name, price, platform).

**Returns:** a 2–4 sentence casual OOTD caption naming the item, price, and platform once
each. If `outfit` is empty/whitespace, returns a descriptive guard message instead.

---

## How the Planning Loop Works (the decisions the agent makes)

`run_agent(query, wardrobe)` runs a **linear, deterministic** loop — not an LLM-routed agent.
The pipeline is fixed (search → style → caption), so hard-coding the sequence is more reliable
and far easier to debug than asking a model to pick the next tool. The agent makes real
decisions at two points:

1. **Parse the query** into `description` / `size` / `max_price`. Sizes and prices are
   structured tokens, so the parser uses regex (deterministic, free) rather than an LLM:
   `"under $30"` → `max_price=30.0`, `"size M"` → `size="M"`, and the leftover text becomes the
   `description`.

2. **Branch on the search result — the key decision.** After `search_listings`:
   - If it returns matches, the agent selects `results[0]` (highest-scored) and proceeds.
   - If it returns nothing, the agent does **not** immediately give up and does **not** blindly
     re-run the same query. It retries with **loosened constraints** in a fixed order — drop the
     size filter, then the price limit, then both (only relaxations that actually change the
     query) — and uses the first retry that returns hits, recording what it changed in
     `session["relaxed"]`. Only if *every* relaxation is still empty does it set
     `session["error"]` and stop. The LLM tools are **never** called on a true no-match, so the
     agent never feeds empty input to the model.

After a listing is selected, `suggest_outfit` and `create_fit_card` run unconditionally on that
state. Those two tools always return a non-empty string (their empty-input and API-error cases
are handled internally), so they need no branching in the loop.

---

## State Management

A single `session` dict is the source of truth for one interaction, created by `_new_session()`
and threaded through every step. Each step reads the fields it needs and writes its output back:

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | init (input) | parse step |
| `parsed` | parse step | `search_listings` |
| `search_results` | search step | selection |
| `selected_item` | selection (`results[0]`) | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | init (input) | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | the UI |
| `error` | search step (only if all retries fail) | every later step / the UI |
| `relaxed` | search step (on a successful retry) | the UI (note shown above the listing) |

There is no global state — one fresh session per call. State flows **by reference**: the exact
`selected_item` dict produced by the search step is the same object passed into both LLM tools
(verified with object-identity checks), so nothing is re-derived or re-prompted between steps.
`app.handle_query` reads the finished session and maps `selected_item` / `outfit_suggestion` /
`fit_card` to the three UI panels, or puts `error` in the first panel and leaves the rest empty.

---

## Error Handling (per tool, with examples from testing)

Every failure mode was triggered deliberately (Milestone 5). Concrete results:

- **`search_listings` — no match.** Returns `[]`, never raises.
  `search_listings('designer ballgown', size='XXS', max_price=5)` → `[]`. The agent then retries
  with loosened filters; when even those are empty it returns a specific message:
  *"No listings matched 'designer ballgown' (size XXS, under $5), even after loosening the
  filters. Try different keywords."* — and `outfit_suggestion`/`fit_card` stay `None`.

- **`search_listings` — partial match (relaxation).** `"vintage graphic tee size XS"` has no tee
  in XS, so the agent drops the size filter and prepends a note:
  *"No exact match for 'vintage graphic tee', so I removed the size filter (size XS) and found
  these instead."*

- **`suggest_outfit` — empty wardrobe.** Switches to a general-advice prompt and still returns
  usable guidance (e.g. *"…pair it with high-waisted denim and chunky boots… layer under a denim
  jacket…"*) instead of failing.

- **`suggest_outfit` / `create_fit_card` — Groq API error.** Both wrap the call in try/except and
  return a safe fallback string (a generic pairing / a templated caption built from the item's
  own fields), so a missing key or network blip degrades gracefully instead of crashing the loop.

- **`create_fit_card` — empty outfit.** `create_fit_card('', item)` →
  *"Can't make a fit card without an outfit suggestion to base it on."* (a descriptive string, not
  an exception).

---

## Spec Reflection

Most of the build matched `planning.md`. The signatures, the deterministic loop, the
single-session state model, and the empty-wardrobe / empty-outfit handling all landed as
designed. A few things changed as I implemented and tested:

- **No-results behavior evolved.** My first instinct was to auto-retry the search on no results.
  That would loop forever on identical constraints, so I changed the design to *stop and advise the
  user*. Later I added a smarter version as a stretch feature: retry with constraints relaxed one
  at a time, and only error out if all relaxations fail. I updated `planning.md` (loop, error
  table, diagram) to match before shipping it.
- **A real search bug surfaced in testing.** Substring size matching let `"8"` match `"W28"`. I
  changed the size filter to whole-token matching, which keeps the legitimate `"M"`→`"S/M"` case
  while fixing the false positive — a precision/recall tradeoff I'd have missed without testing.

Known limitations: keyword-overlap search has broad recall (a common tag like "vintage" matches
many items), but ranking puts the best matches first and the agent only uses the top result; and
the regex query parser handles common phrasings ("under $30", "size M") but isn't a general NLU.

---

## AI Usage

I used Claude as a coding collaborator, driving it with the specific sections of `planning.md`
and reviewing every output before running it.

1. **Implementing `search_listings`.** I gave Claude the Tool 1 block from `planning.md` (inputs,
   the full list-of-dicts return spec, the empty-list failure mode) and the `load_listings()`
   signature. It produced the keyword-overlap scorer with size/price filters. On review and testing
   I caught that `"8"` matched `"W28"`, so I had it switch from substring to whole-token size
   matching and re-ran the five verification queries before accepting the code.

2. **Implementing the planning loop.** I gave Claude the Planning Loop, State Management, and
   Architecture (diagram) sections and asked for `run_agent`. It produced the loop; I verified it
   against my spec by checking that (a) it branches on the `search_listings` result, (b) it writes
   each tool's output to the documented session field, and (c) it does **not** call the LLM tools
   when search is empty. I confirmed state flows by reference using object-identity checks (the
   selected item is the same object passed into both LLM tools).

3. **Overriding a design decision.** When I sketched Tool 1, I wrote that a failed search should
   automatically re-search. Claude pushed back that re-running identical constraints just fails
   again, and we replaced it with the stop-and-advise design — then I added the constraint-loosening
   retry deliberately as a stretch feature, which is the version in the code today.

---

## Project Structure

```
ai201-project2-fitfindr-starter/
├── data/                 # 40 mock listings + wardrobe schema (provided)
├── utils/data_loader.py  # data loaders (provided)
├── planning.md           # design doc: spec, agent diagram, decisions
├── tools.py              # search_listings, suggest_outfit, create_fit_card
├── agent.py              # run_agent planning loop + query parser + retry ladder
├── app.py                # Gradio UI + handle_query
├── tests/                # test_tools.py + test_agent.py (25 tests)
├── conftest.py           # makes the project root importable for tests
└── manual_test.py        # end-to-end eyeball script
```
