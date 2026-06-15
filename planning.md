# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Searches the 40-item mock listings dataset for pieces matching the user's described
keywords, optionally narrowed by size and a maximum price. Pure Python — no LLM call —
so it is fast, free, and deterministic.

**Input parameters:**
- `description` (str, required): free-text keywords describing the wanted item
  (e.g. `"vintage graphic tee"`). This is the leftover query text after the size and
  price have been parsed out.
- `size` (str | None, default None): a single size token to filter by (e.g. `"M"`, `"8"`,
  `"XXS"`). Matched case-insensitively as a *whole token*, so `"M"` matches `"S/M"` but
  `"8"` does NOT match `"W28"`. `None` skips size filtering.
- `max_price` (float | None, default None): inclusive upper price bound in USD. A listing
  priced exactly at `max_price` is kept. `None` skips price filtering.

**What it returns:**
A `list[dict]`, sorted by relevance (highest keyword-overlap score first, cheaper price as
the tiebreaker). Each dict is a full listing with these fields:
`id` (str), `title` (str), `description` (str), `category` (str: tops/bottoms/outerwear/
shoes/accessories), `style_tags` (list[str]), `size` (str), `condition` (str: excellent/
good/fair), `price` (float), `colors` (list[str]), `brand` (str | None), `platform`
(str: depop/thredUp/poshmark). Returns an empty list `[]` when nothing matches.

**What happens if it fails or returns nothing:**
It never raises — "no match" is represented by an empty list. When the exact search is empty
the agent does **not** give up immediately: it retries with loosened constraints (drop the size
filter, then the price limit, then both — only relaxations that actually change the query) and
uses the first retry whose results still match the query's item type (its head noun, e.g. "boots"
in "black combat boots") — so relaxing changes the size/price but never the *kind* of item —
recording what it adjusted in `session["relaxed"]`. Only
if every relaxation is still empty does it set `session["error"]` and return early **without**
calling `suggest_outfit` or `create_fit_card`. It never blindly re-runs the *same* constraints
— each retry drops a filter, so it can actually return something new. On a non-empty list (exact
or relaxed), the agent selects `results[0]` and continues.

---

### Tool 2: suggest_outfit

**What it does:**
Given the selected thrifted item and the user's wardrobe, asks the LLM to propose 1–2
complete outfits. Uses specific named wardrobe pieces when the wardrobe has items, and gives
general styling advice when it is empty.

**Input parameters:**
- `new_item` (dict, required): one listing dict produced by `search_listings` (the selected
  top result). The tool reads its `title`, `category`, `colors`, `style_tags`, `price`, and
  `platform` to describe the piece to the model.
- `wardrobe` (dict, required): a dict with an `items` key holding a list of wardrobe-item
  dicts. Each item has `id` (str), `name` (str), `category` (str), `colors` (list[str]),
  `style_tags` (list[str]), and optional `notes` (str). The `items` list may be empty.

**What it returns:**
A non-empty `str` of 1–2 outfit ideas in natural language. When the wardrobe has items, the
text references pieces by name (e.g. "pair it with your baggy dark-wash jeans"). Generated at
low temperature (~0.4) so the advice stays grounded and consistent.

**What happens if it fails or returns nothing:**
If `wardrobe["items"]` is empty, the tool switches to a general-styling prompt (what kinds of
pieces pair well, what vibe it suits) and still returns useful advice — it never returns an
empty string. If the Groq API call raises (missing key, rate limit, network error), the
exception is caught and the tool returns a safe fallback string so the planning loop can still
produce a (degraded) result instead of crashing.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion plus the item details into a short, casual, shareable OOTD-style
caption for social media.

**Input parameters:**
- `outfit` (str, required): the outfit-suggestion text returned by `suggest_outfit`.
- `new_item` (dict, required): the same listing dict, used to surface the item's `title`,
  `price`, and `platform` in the caption.

**What it returns:**
A `str` of 2–4 sentences written like a real OOTD post, mentioning the item name, price, and
platform once each and capturing the outfit's vibe. Generated at high temperature (~0.9) so
captions vary across runs and inputs.

**What happens if it fails or returns nothing:**
If `outfit` is empty or whitespace-only, the tool returns a descriptive error string (e.g.
"Can't make a fit card without an outfit suggestion") rather than raising. If the Groq API
call raises, the exception is caught and the tool returns a fallback caption assembled from the
item's `title`, `price`, and `platform`.

---

### Additional Tools (if any)

None for the core build. Possible stretch tools: a `parse_query` tool to extract
size/price/keywords via the LLM instead of regex, or a `rank_results` tool that re-ranks
search hits against the wardrobe.

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is **deterministic and linear** — the task is a fixed pipeline, so the next tool is
chosen by the success/failure of the previous step rather than by an LLM router. There is
exactly one branch point: whether the search returned anything.

1. **Initialize state.** `session = _new_session(query, wardrobe)` creates the session dict
   (see State Management) with every output field set to `None`/empty and `error = None`.

2. **Parse the query.** Extract `description`, `size`, and `max_price` from the raw query and
   store them in `session["parsed"]`. (Implementation: regex for the price phrase, e.g.
   `"under $30"` → `30.0`; regex for an explicit `"size X"` token, with a conservative fallback
   for standalone `XS/XL/XXL`; the remaining text becomes `description`.)

3. **Call search_listings.** `results = search_listings(parsed["description"], parsed["size"],
   parsed["max_price"])`.
   - **Branch A — `len(results) == 0` (retry, then maybe stop):** before giving up, retry with
     loosened constraints via `_fallback_attempts(parsed)` — drop the size filter, then the price
     limit, then both (only relaxations that change the query). Use the first retry whose results still match the
     query's item type (its head noun, e.g. "boots") — so relaxing never changes the *kind* of
     item — set `session["relaxed"]` to a note describing what was adjusted, and fall through to
     Branch B with those results. If every relaxation is still empty, set `session["error"]` and
     `return session` immediately — do **not** call `suggest_outfit` or `create_fit_card`.
   - **Branch B — results found (exact or relaxed):** store them in `session["search_results"]`,
     set `session["selected_item"] = results[0]` (the highest-scored listing), and proceed to step 4.

4. **Call suggest_outfit.** `session["outfit_suggestion"] =
   suggest_outfit(session["selected_item"], session["wardrobe"])`. This always returns a
   non-empty string (the empty-wardrobe and API-error cases are handled inside the tool), so no
   branch is needed here.

5. **Call create_fit_card.** `session["fit_card"] =
   create_fit_card(session["outfit_suggestion"], session["selected_item"])`.

6. **Return.** `return session`. The interaction is "done" when `fit_card` is set, or earlier
   if Branch A set `error`. The caller checks `session["error"]` first: if it is not `None`,
   the run ended early and the other output fields are still `None`.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict is the single source of truth for one user interaction, created by
`_new_session(query, wardrobe)` and threaded through every step. Each step reads the fields it
needs and writes its output back into the same dict:

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | `_new_session` (input) | parse step |
| `parsed` (description/size/max_price) | parse step | `search_listings` call |
| `search_results` | search step | selection step |
| `selected_item` | selection (`results[0]`) | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `_new_session` (input) | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | the UI |
| `error` | search step (only if all retries fail) | every later step / the UI (short-circuits output) |
| `relaxed` | search step (on a successful retry) | the UI (note prepended to the listing panel) |

There is no global state; one fresh `session` per call to `run_agent`. After the loop returns,
`app.handle_query` reads the finished session and maps `selected_item` / `outfit_suggestion` /
`fit_card` to the three UI panels — or, if `error` is set, puts the error in the first panel
and leaves the other two empty.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response. Each response names the actual message the user sees and what the agent offers instead.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No exact match | `run_agent` retries with loosened constraints (drop size, then price, then both) and uses the first retry whose results still match the query's item type (head noun) — so a "black combat boots" search relaxes into other boots, never black shorts. It sets `session["relaxed"]` to a note like *"No exact match for 'black combat boots', so I removed the $40 price limit and found these instead."* The UI prepends that note to the listing panel. |
| search_listings | No match even after loosening | `run_agent` sets `session["error"]` — e.g. *"No listings matched 'designer ballgown' (size XXS, under $5), even after loosening the filters. Try different keywords."* — and returns early, skipping both LLM tools. The UI shows it in panel 1; panels 2 and 3 stay empty. |
| suggest_outfit | Wardrobe is empty (`items == []`) | Instead of failing, the tool detects the empty wardrobe and asks the LLM for *general* styling guidance for the item, returning advice the user can act on — e.g. *"This tee leans casual-streetwear; pair it with high-waisted denim and chunky boots, and try an oversized flannel layered over it."* The loop continues normally to the fit card. |
| suggest_outfit | Groq API error (missing key / rate limit / network) | The exception is caught. The tool returns a non-empty fallback that still names the item and gives a usable pairing — e.g. *"Styling assistant is temporarily unavailable — for now, this piece works with neutral basics and one contrasting layer."* — so the loop still reaches `create_fit_card`. |
| create_fit_card | Outfit input is missing or incomplete (empty/whitespace) | The tool returns a clear, non-caption message rather than inventing one: *"Can't make a fit card without an outfit suggestion to base it on."* (Guards a bad hand-off; on the normal path `suggest_outfit` always returns text, so this should not fire.) |
| create_fit_card | Groq API error | The exception is caught and the tool returns a templated caption built from the item's own fields, so the user still gets something shareable — e.g. *"Just thrifted the 2003 Tour Bootleg Tee for $24 on depop and I'm obsessed — styled it up and it's giving exactly what I wanted."* |

---

## Architecture

The agent is a linear planning loop. Its one branch point is the search result: on an empty
exact search it retries with loosened constraints (drop size, then price, then both) and only
errors out if every retry is still empty. Every step reads from and writes to a single `session`
dict. The error path short-circuits the loop and returns early on the right-hand return rail.

```
User query + wardrobe choice
    │
    ▼
app.handle_query          (guards empty query; picks example vs. empty wardrobe)
    │  passes: query, wardrobe
    ▼
                         ┌───────────────────────────────────────────┐
  reads / writes  ◄───► │ SESSION STATE  (one dict per interaction)        │
  on every step          │   query · parsed · search_results ·             │
    │                    │   selected_item · wardrobe · outfit_suggestion ·│
    │                    │   fit_card · error · relaxed                      │
    │                    └───────────────────────────────────────────┘
    ▼
Planning Loop (run_agent) ─────────────────────────────────────┐
    │                                                                        │
    │  (1) parse query → parsed = {description, size, max_price}             │
    │                                                                        │
    ├─► (2) search_listings(description, size, max_price)                    │
    │           │ exact results == []                                       │
    │           ├──► RETRY ladder: drop size, then price, then both         │
    │           │        ├─ all retries still [] → session["error"]          │
    │           │        │     "...even after loosening" ─► return session ──┤  early
    │           │        └─ a retry has hits → session["relaxed"] = note     │  exit
    │           │                                                           │
    │           ▼ results found (exact or relaxed)                          │
    │       session["selected_item"] = results[0]                            │
    │           │ passes: selected_item, wardrobe                           │
    ├─► (3) suggest_outfit(selected_item, wardrobe)                         │
    │           │ returns: outfit text                                      │
    │           │   (empty wardrobe → general advice; API error → fallback) │
    │           ▼                                                           │
    │       session["outfit_suggestion"] = "..."                             │
    │           │ passes: outfit_suggestion, selected_item                  │
    └─► (4) create_fit_card(outfit_suggestion, selected_item)               │
                │ returns: caption                                          │
                │   (empty outfit → error string; API error → fallback)     │
                ▼                                                           │
            session["fit_card"] = "..."                                     │
                │                                early-exit returns here ───┘
                ▼
            return session
                │
                ▼
  UI panels:  [1] listing details (+ relaxed note)   [2] outfit idea   [3] fit card
              (on early exit: error message in [1]; [2] and [3] stay empty)
```

## AI Tool Plan

For each part below I name the AI tool, the exact `planning.md` sections I'll paste in as the prompt, what I expect back, and the checks I'll run *before* trusting the output.

**Milestone 3 — Individual tool implementations:**

- **search_listings (no LLM):** Give Claude the **Tool 1** block (inputs, the full list-of-dicts return spec, and the empty-list failure mode) plus the signature of `load_listings()` from `utils/data_loader.py`. I expect a function that loads the listings, applies the `max_price` and `size` filters, scores the remainder by keyword overlap with `description`, drops zero-score items, and returns them sorted best-first. **Before running it** I'll read the code to confirm it (a) filters by all three parameters, (b) returns `[]` rather than raising on no match, and (c) matches `size` as a whole token so `"8"` does not match `"W28"`. **Then** I'll test 5 queries: keyword+price, keyword+size, keyword-only, a numeric size, and a deliberate no-match that must return `[]`.
- **suggest_outfit (LLM):** Give Claude the **Tool 2** block plus its **Error Handling** rows and the Groq client setup already in `tools.py`. I expect a function that branches on whether `wardrobe["items"]` is empty (general advice vs. named-piece combos), calls the model at low temperature (~0.4), and wraps the call in try/except returning a fallback string. **Before running** I'll confirm both branches exist and the API error is caught. **Then** I'll test it once with the example wardrobe (output must name real wardrobe pieces) and once with the empty wardrobe (output must still be non-empty general advice).
- **create_fit_card (LLM):** Give Claude the **Tool 3** block plus its **Error Handling** rows. I expect a function that guards an empty `outfit` string, otherwise prompts the model at high temperature (~0.9) for a 2–4 sentence caption naming the item, price, and platform. **Before running** I'll confirm the empty-input guard exists and that item/price/platform are passed into the prompt. **Then** I'll test with a real outfit string (caption mentions all three details and reads casually) and with `""` (returns the guard message, not a caption).

**Milestone 4 — Planning loop and state management:**

Give Claude the **Planning Loop**, **State Management**, and **Architecture** (diagram) sections together, plus the three finished tool signatures. I expect an implementation of `run_agent(query, wardrobe)` that builds the `session` dict, parses the query into `description`/`size`/`max_price`, calls `search_listings`, enforces the no-results gate (set `session["error"]` and return *before* any LLM call), selects `results[0]`, then calls `suggest_outfit` and `create_fit_card`, threading state through `session`. **Before running** I'll check that the empty-results branch returns before the LLM tools and that each tool's output is written to the session field named in State Management. **Then** I'll run the happy-path query (expect all three output fields populated and `error is None`) and the deliberate no-results query (expect `error` set, with `outfit_suggestion` and `fit_card` still `None`). Once that passes I'll hand Claude the **Error Handling** and **Complete Interaction** sections to wire `app.handle_query` and confirm the three panels map correctly.

---

## A Complete Interaction (Step by Step)

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**What FitFindr needs to do:** FitFindr takes a shopper's plain-language request for a secondhand piece and runs it through a fixed pipeline — it parses the request and calls `search_listings` to find matching items, then (once a listing is picked) calls `suggest_outfit` to style that item against the user's wardrobe, and finally calls `create_fit_card` to turn the outfit into a shareable caption. Each tool is triggered by the successful output of the step before it: search runs on the parsed query, the outfit suggestion runs on the top-scored listing, and the fit card runs on the outfit text. When something fails the agent degrades instead of crashing: if the exact search finds nothing it first retries with loosened filters and only stops with a helpful 'no matches' message (never calling the LLM tools) when even those come up empty, if the wardrobe is empty `suggest_outfit` gives general styling advice, and if a suggestion or caption can't be generated the tool returns a safe fallback string.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — Initialize + parse.**
`run_agent(query, example_wardrobe)` builds a fresh `session` and parses the query into
`session["parsed"] = {"description": "looking for a vintage graphic tee . I mostly wear baggy jeans and chunky sneakers", "size": None, "max_price": 30.0}`. "$30" becomes the price ceiling, no explicit size is given, and the leftover text is the description.

**Step 2 — search_listings (first tool call).**
Input: `search_listings("looking for a vintage graphic tee ...", None, 30.0)`.
It drops every listing over $30, tokenizes the description (dropping stopwords like *looking/for/I*), scores the rest by weighted keyword overlap (matches in `style_tags` and `title` count most), drops zero-score items, and sorts best-first.
Returns: a non-empty `list[dict]` whose top element is the best-matching graphic tee under $30 — e.g. `{"title": "Graphic Tee — 2003 Tour Bootleg Style", "price": 24.0, "platform": "depop", "size": "L", "style_tags": ["graphic tee", "vintage", "grunge", "streetwear", "band tee"], ...}`.
Because the list is non-empty, the agent sets `session["selected_item"] = results[0]` and continues. (If it were empty, the agent would set `session["error"]` and return here instead.)

**Step 3 — suggest_outfit (second tool call).**
Input: `suggest_outfit(selected_item, example_wardrobe)`, where the wardrobe contains pieces like *baggy straight-leg dark-wash jeans* and an *olive canvas shacket*.
Since the wardrobe is non-empty, the tool prompts the LLM (temp 0.4) to combine the tee with specific named pieces.
Returns: an outfit string such as *"Pair the bootleg tee with your baggy dark-wash jeans and chunky sneakers, then layer the olive shacket over it for a relaxed streetwear look."*
Stored in `session["outfit_suggestion"]`.

**Step 4 — create_fit_card (third tool call).**
Input: `create_fit_card(outfit_suggestion, selected_item)`.
The tool prompts the LLM (temp 0.9) for a casual 2–4 sentence caption that names the item, its $24 price, and depop.
Returns: a caption such as *"Found this 2003 bootleg tour tee on depop for $24 and had to grab it. Styled it with baggy jeans, chunky sneakers, and an olive shacket — peak thrifted streetwear. Obsessed."*
Stored in `session["fit_card"]`, and `run_agent` returns the session.

**Final output to user:**
The Gradio UI shows three panels — (1) the listing details for the $24 bootleg graphic tee on depop, (2) the outfit idea pairing it with the user's baggy jeans, chunky sneakers, and shacket, and (3) the shareable fit-card caption. If only the size or price has no match (e.g. a graphic tee in an unavailable size), the agent retries without that filter and prepends a note to panel 1 — *"No exact match for 'vintage graphic tee', so I removed the size filter (size XS) and found these instead."* On a total no-results query (e.g. "designer ballgown size XXS under $5"), where even the relaxed retries are empty, panel 1 instead shows *"No listings matched 'designer ballgown' (size XXS, under $5), even after loosening the filters. Try different keywords."* and panels 2 and 3 stay empty.
