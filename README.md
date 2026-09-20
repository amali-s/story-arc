# Story Arc

Name a story, book, film, or folk tale. The app finds it on Wikipedia, pulls the plot
summary, works out who the three main characters are, and draws each one's journey on a
single chart with a character toggle.

- **Height and shape** of the line show action intensity.
- **Color** shows the feeling TypeSafe reads in the narration: joy (yellow), rage (red), despair (purple), relief (blue).
- **Brightness** shows how sure it is. **Dashed** means uncertain intensity. **Faded** means the character is offstage.

It shows TypeSafe System One's "code in control" pattern, and leans on it harder than the
usual demo does. **System One does not search and does not count.** The docs are explicit:
*"Do not rely on knowledge stored in model weights when current information can come from
your own knowledge base."* So code does the retrieval, the name counting, the segmentation,
the ranking arithmetic and the drawing. System One only answers narrow, typed questions
about text that code put in front of it.

## Run it

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or plain `pip`).

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
echo 'TYPESAFE_API_KEY=your_key_here' > .env   # skip if .env already exists
.venv/bin/uvicorn app:app --app-dir backend --port 8000
```

Open http://localhost:8000 and search for a story. Or switch to **Paste text** and click
**Load sample story**.

Set `WIKI_CONTACT` in `.env` to your own URL or email before using this beyond a local
demo — see [User-Agent](#user-agent) below.

The API key is read from `.env` by the Python backend only. The browser talks to `/api/*`
and never sees the key. `.env` is git-ignored.

## How it works

```
backend/
  wiki.py       1-2. Wikipedia search and plot-section retrieval (no AI)
  story.py      3.   segmentation + narration features (deterministic, no AI)
  characters.py 4.   candidate names and mention counts (no AI); 5. code-side ranking
  questions.py  5,6. the two state shapes and every question definition
  app.py        staged endpoints, caching, concurrency
  cache.py      SQLite cache of raw answers and retrieved summaries
  compose.py    7.   intensity, color, flags: ALL WEIGHTS AND THRESHOLDS LIVE HERE
frontend/
  index.html, style.css, app.js   SVG chart, toggle, tooltip, legend (rendering only)
  sample.js                       original sample story for the paste path
```

The frontend calls four endpoints in order so each stage can report progress.
Every stage is cached, so later stages re-derive earlier ones for free.

1. **Title search** (`wiki.py`, `GET /api/search`). Wikipedia's search endpoint, plus one
   more call for the hits' short descriptions. Each result is shown as a title and a media
   type with its year — "Novel · 1925", "Film · 2013", "Musical · 2023" — which is what
   makes an ambiguous title usable. `classify()` reads the type and year out of the short
   description and the title's parenthetical, because neither alone is enough: *Jurassic
   Park* is described as "1993 film by Steven Spielberg" (both), while *The Little Mermaid
   (1989 film)* is described as "American animated film" and carries its year only in the
   title. Articles that are not works fall back to the raw description, so "Jay Gatsby"
   reads "Character in the novel The Great Gatsby" rather than being mislabeled a novel.
   This costs no extra API call: it is all text already in hand.

2. **Plot retrieval** (`wiki.py`, `POST /api/plot`). The article's section list is fetched,
   the first heading matching `PLOT_SECTION_TITLES` wins, and that section's HTML is
   fetched and stripped to plain text (references, tables, figures and infoboxes dropped).
   If there is no such section the request fails with a message naming the sections the
   article *does* have. **There is no silent fallback to another section** — a sonnet with
   only "Structure" and "Analysis" says so rather than charting literary criticism.

3. **Segmentation** (`story.py`). Plot summaries are a few dense paragraphs, so they are
   split into groups of `SENTENCES_PER_SEGMENT` (3) sentences rather than by paragraph.
   Pasted text keeps the original paragraph mode, because there the paragraphs are the
   author's own beats. The sentence splitter knows about abbreviations and initialisms, so
   "Dr. Grant" and "J. R. R. Tolkien" do not split a sentence in half.

4. **Candidate characters** (`characters.py`, "code counts names"). Capitalized name-like
   runs are extracted, honorifics and possessives trimmed, sentence-initial stopwords
   dropped, aliases merged, and the top `MAX_CANDIDATES` (8) kept by mention count.
   This is a heuristic, not an NER model: it adds no dependency, and its failure mode is
   over-generation, which is exactly what the next step's gate cleans up.

5. **Character confirmation** (`questions.py`, `app.py`, "jev confirms"). **One request.**
   State is `{"story": {"title", "plot"}, "candidates": [{"name", "mentions"}, ...]}`, and
   for each candidate `i` two Nouls are asked in parallel:
   - `is_character_i` — is `candidates[i].name` a character (person, animal, being) rather
     than a place, group, object, or title?
   - `is_main_character_i` — do their actions or fate drive the events of `story.plot`?

   Code then ranks: `is_character` is a hard gate at `IS_CHARACTER_THRESHOLD`, and the
   survivors are scored by `RANK_WEIGHTS` over `is_main_character` and normalized mention
   count. Top 3 win. `candidates[i].mentions` sits in the state as context but **no question
   ever refers to it** — jev-1.13 is documented as poor at counting and numeric comparison,
   so all the arithmetic stays in code. If fewer than 3 pass the gate, the chart shows
   however many there are and says so.

6. **Journey analysis** (`questions.py`, `app.py`). **One request per segment, covering all
   three characters**, so every question in it runs in parallel. State is
   `{"characters": [{"name"}, ...], "segment": {"index", "text"}, "context": {"previous_segment"}}`.
   - Asked once per segment, because they are about the narration and not about a person:
     `sets_the_stage`, `physical_action`, `active_conflict`, `urgent_pacing` (Noul) and
     `action_intensity` (Score, 5 levels).
   - Asked per character, suffixed `_0`/`_1`/`_2`: `character_is_focal`, `stakes_raised`
     (Noul) and `narrated_feeling` (Choice, 4 options).

   That is 14 questions in one request for three characters, against 24 if each character
   were asked separately. Segments run concurrently, up to `MAX_CONCURRENT_REQUESTS` (8).

7. **Composition** (`compose.py`) turns answers into chart values, per character:
   - `intensity` is a weighted sum of the shared signals (`action_intensity`,
     `physical_action`, `active_conflict`, `urgent_pacing`, pacing features) plus that
     character's own `character_is_focal` and `stakes_raised`.
   - If `sets_the_stage >= 0.7` and intensity is below `0.35`, intensity is clamped to
     `0.03`, which draws a flat line.
   - `color` blends the four feeling colors by that character's Choice `probabilities`.
     Its saturation is then scaled by `narrated_feeling.confidence`.
   - If that character's `character_is_focal < 0.5`, the segment is offstage: opacity 0.3.
     Focal status also carries an intensity weight, so an absent character both dips and
     fades rather than tracking someone else's scene at full height.
   - If `action_intensity.confidence < 0.6`, the segment's intensity is uncertain and drawn
     dashed. This is shared, so it is the same on all three lines.

8. **Chart** (`app.js`): monotone cubic (Fritsch–Carlson) interpolation. It's smooth but
   never overshoots, so flat stretches stay flat and sudden jumps stay sharp. Each point's
   half of the line takes that point's dash and opacity; color is a gradient between
   neighboring points. The character buttons swap which `composed[k]` is drawn — no refetch.
   Hovering (or tabbing to) a point shows its excerpt, any flags on it (stage-setting,
   uncertain intensity, offstage), that character's four feeling probabilities with
   confidence, and the deterministic pacing features. The raw Noul and Score numbers behind
   the line height are still returned by `/api/journeys` on every point, so they are one
   `console.log` away if you want to inspect them.

   The UI does not caption what the chart is reading. That distinction has not gone away —
   see the first entry under Known limits, which is the thing to know before reading any of
   these charts.

### Tuning

Every weight and threshold is a constant at the top of its module. Restart the server after
editing.

| File | Constants |
| --- | --- |
| [`backend/compose.py`](backend/compose.py) | `INTENSITY_WEIGHTS`, `PACING_FEATURE_WEIGHTS`, `BREVITY_SHORT_WORDS`, `BREVITY_LONG_WORDS`, `STAGE_SETTING_THRESHOLD`, `STAGE_LOW_INTENSITY`, `STAGE_CLAMPED_INTENSITY`, `FOCAL_THRESHOLD`, `OFFSTAGE_OPACITY`, `INTENSITY_CONFIDENCE_THRESHOLD`, `FEELING_COLORS`, `MIN_SATURATION_FACTOR`, `BLEND_BY_PROBABILITIES` |
| [`backend/characters.py`](backend/characters.py) | `MAX_CANDIDATES`, `TOP_CHARACTERS`, `IS_CHARACTER_THRESHOLD`, `RANK_WEIGHTS`, `_HONORIFICS`, `_STOPWORDS`, `_NON_NAMES` |
| [`backend/story.py`](backend/story.py) | `SENTENCES_PER_SEGMENT`, `MIN_TRAILING_SENTENCES`, `MIN_SEGMENT_WORDS`, `SHORT_SENTENCE_WORDS`, `MAX_STORY_CHARS`, `MAX_SEGMENTS` |
| [`backend/wiki.py`](backend/wiki.py) | `PLOT_SECTION_TITLES`, `SEARCH_LIMIT`, `MAX_PLOT_CHARS`, `REQUEST_TIMEOUT`, `CONTACT` |
| [`backend/app.py`](backend/app.py) | `MODEL`, `MAX_CONCURRENT_REQUESTS` |

Changing a composition or ranking constant **does not** re-call the API. The cache key
hashes the exact state, the question definitions and the model name. Only edits to a
segment (or the segment before it), the character set, or the questions cause new calls.
Delete `.cache/` to force a fresh run — including to re-fetch a Wikipedia article that has
since been edited.

## Known limits

**The chart reads the summary, not the book.** This is the big one. A Wikipedia plot
summary is an encyclopedia's compressed retelling: past tense, even pacing, no dialogue, no
imagery. When the chart says segment 9 of *Hamlet* is rage at confidence 1.00, that is a
true reading of *the summary's* narration, not of Shakespeare's verse. Summaries flatten
exactly the things this app measures. The paste-text path exists partly so you can feed it
real prose and see the difference. **The UI does not say this anywhere** — it is on you to
remember it, or to put the caption back.

**Alias merging is shallow.** It folds a shorter name into a longer one when the shorter is
a word-prefix or word-suffix of exactly one longer candidate: "Gatsby" into "Jay Gatsby",
"Riding Hood" into "Little Red Riding Hood". It does **not**:
- catch nicknames ("Lizzy" never merges into "Elizabeth Bennet");
- resolve pronouns — a character referred to as "he" for a whole paragraph is undercounted,
  though `character_is_focal` is asked to count pronouns and so partly compensates;
- merge ambiguous names — "Buchanan" matches both "Tom Buchanan" and "Daisy Buchanan", so
  it stays a separate candidate rather than being guessed at;
- keep "of"-style epithets. `_PARTICLE` deliberately excludes `of` and `the`, because
  "Prince Hamlet of Denmark" would otherwise become a character named "Hamlet of Denmark".
  The cost is that "Joan of Arc" is read as "Joan".

**Sentence-initial role nouns.** "Industrialist John Hammond has created..." opens a
sentence, so capitalization alone cannot separate the role noun from the name. The fix is
to prefer a suffix of the run that also appears mid-sentence, which turns that into
"Hammond". A character who *only* ever appears sentence-initially behind a role noun will
still come out wrong.

**Poems, sonnets and many short works have no plot section** and are rejected by design
with a message listing the sections they do have. Wikipedia's poetry articles are usually
"Structure / Context / Analysis".

**Only capitalized names are visible to the name finder.** A character referred to by a
common noun is structurally invisible to it. In *Little Red Riding Hood* the wolf — arguably
the second lead — is never found, because the summary calls it "the wolf" in lower case.
The chart offers the huntsman ("Jäger") instead. An NER model would not fix this either;
resolving "the wolf" as a character needs coreference, not name detection.

**The `is_character` gate does real work but is not perfect.** On the same article it
correctly rejects "Grimms" (0.10), "Brothers Grimm" (0.14), "German" (0.06) and "Sanitized"
(0.07) — all publication-history noise from a thin plot section — but lets **Charles
Perrault** through at exactly 0.50, the threshold. The author of the tale is not a character
in it. Raising `IS_CHARACTER_THRESHOLD` above 0.5 would drop him; it would also drop
genuinely borderline minor characters. Places that act like characters, and characters named
after places, sit on the same knife edge. The tooltip on each character chip shows the gate
and ranking numbers so you can see why someone made the cut.

**Ranking is a judgment call, not a fact.** On *Jurassic Park* (the 1993 film) the model
gives Dennis Nedry `is_main_character` 0.93 and Alan Grant 0.52 — defensible, since Nedry's
sabotage is what actually drives the plot, but it means the protagonist places fourth and
drops off the chart. Raise the `mentions` weight in `RANK_WEIGHTS` if you disagree. This is
the point of keeping the weights in code.

**The cache never expires.** Articles get edited; delete `.cache/` to re-fetch.

## API details: verified vs. assumed

### TypeSafe

Verified against https://docs.typesafe.ai (llms.txt index, System One, State, How to Build,
Primitives, Confidence, Models, Jev 1.13 jaggedness, Python SDK) and by inspecting the
installed `typesafe-sdk` 0.7.0:

| Detail | Source | What the app relies on |
| --- | --- | --- |
| System One does not retrieve | How to Build: *"Do not rely on knowledge stored in model weights when current information can come from your own knowledge base."* | All retrieval is in `wiki.py`; the model only judges fetched text |
| Questions run in parallel | How to Build: *"Questions are evaluated independently and in parallel. One primitive's result does not become hidden context that changes another primitive's result."* | 14 questions in one per-segment request; 2×N in one candidate request |
| Array paths in instructions | How to Build shows backticked dot-and-index notation, e.g. `` `support.tickets[0].message` `` | `` `characters[0].name` ``, `` `candidates[0].name` `` |
| Noul answers carry no confidence | SDK: `NoulAnswer` fields are exactly `type`, `noul` (`ScoreAnswer`/`ChoiceAnswer` do have `confidence`) | Ranking uses the Noul probability directly; no confidence is read or displayed for Nouls |
| Choice answers include the full distribution | API reference: Choice answer `probabilities` (sum to 1); SDK `ChoiceAnswer` fields `choice`, `confidence`, `probabilities` | Color blends by probability |
| Score range | Score page: "from 0 to the top level number"; divide by `len(criteria) - 1` | 5 levels → 0–4, normalized by 4 |
| Counting is a weak spot | Jev 1.13 jaggedness: poor at counting, math and numeric comparison; accuracy drops with large irrelevant state | Mentions are counted and compared only in code |
| Auth | SDK reads `TYPESAFE_API_KEY` from the environment (`constants.API_KEY_ENV`); HTTP uses `Authorization: Bearer` | Key loaded from `.env` in the backend |
| Limits | Models page: 64k tokens per request, 32k for state + longest question, 1,200 req/min, 250k tok/s, and these "can change without notice" | Plot capped at `MAX_PLOT_CHARS` (30k chars); story capped at 60k chars / 60 segments |
| Async client | `AsyncTypeSafeClient().system_one(state, questions, model=...)`; `response.answers[name]`, `response.model` | Concurrent requests; versioned model id shown in the UI |
| Structured criteria | Advanced structure / Choice pages: criteria values may be JSON objects, and field names such as `what`, `not_for`, `examples`, `signals` are free-form | Used for the Score levels and Choice options |
| Default model | `jev-latest` alias (currently `jev-1.13.0`) | Overridable with `TYPESAFE_DEFAULT_MODEL` |

### Wikipedia / MediaWiki

Verified against the MediaWiki API docs **and by calling the live en.wikipedia.org API**,
because the docs were stale in one place:

| Detail | Status | Endpoint |
| --- | --- | --- |
| Search | ✅ called live | `action=query&list=search&srsearch=&srnamespace=0&srlimit=` → `query.search[] {pageid, title, snippet, wordcount}` |
| Short descriptions | ✅ called live | `action=query&prop=description&pageids=a\|b\|c` → `query.pages[] {pageid, description}` |
| Section list | ✅ called live | `action=parse&pageid=&prop=tocdata` → `parse.tocdata.sections[] {line, index, anchor, hLevel}` |
| `prop=sections` is deprecated | ✅ confirmed live | It still responds, but returns `"prop=sections" has been deprecated. Please use "prop=tocdata" instead.` `tocdata` returns no warning, so the app uses `tocdata`. |
| Section content | ✅ called live | `action=parse&pageid=&prop=text&section=<index>&disabletoc=1&disableeditsection=1` → `parse.text` (HTML) |
| `formatversion=2` | ✅ called live | Unwraps `{"*": "..."}` into plain strings |
| `parse.tocdata` can be `null` | ✅ hit live | An article with no headings returns null, not `{}` — the code handles it |
| User-Agent policy | ✅ read at its current URL | [foundation.wikimedia.org/wiki/Policy:User-Agent_policy](https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy) (the mediawiki.org page is now a redirect) |

**`prop=extracts` (TextExtracts) was deliberately rejected.** Its own documentation warns
that it returns intro-only content when parameters are omitted, that `exlimit > 1` only
works together with `exintro`, and that plaintext mode can retain citations. `action=parse`
with an explicit section index is predictable; TextExtracts is not.

<a id="user-agent"></a>**User-Agent.** The policy requires
`<client>/<version> (<contact>) <library>/<version>` and warns that generic agents such as
`python-requests` "may be blocked without notice". The app sends
`StoryArc/0.2 (<WIKI_CONTACT>) httpx2/<version>`. **Set `WIKI_CONTACT` in `.env` to your own
URL or email** — the default is a placeholder and is not a valid contact.

Requests to Wikipedia are serial, at most four per story (search, descriptions, sections,
section text), and every article is cached after the first fetch.

### Assumed or chosen (not specified by any doc)

- Composition weights, thresholds and colors are starting points, not values from the docs.
- `RANK_WEIGHTS` (0.70 `is_main_character` / 0.30 mentions) and `IS_CHARACTER_THRESHOLD`
  (0.5) are my choices. They decide who appears on the chart — see the *Jurassic Park* note
  under Known limits.
- `SENTENCES_PER_SEGMENT = 3` is a readability choice for summary-length text.
- `MAX_CANDIDATES = 8` and `TOP_CHARACTERS = 3`.
- The `PLOT_SECTION_TITLES` list and its preference order.
- The capitalized-run name heuristic, its stopword and honorific lists.
- `MAX_PLOT_CHARS = 30_000` and the concurrency limit of 8 are conservative choices, not
  documented numbers.
- `Cache-Control: no-cache` on the frontend assets is a dev-server convenience so edits show
  up on reload.
- If any segment's request fails (after SDK retries), the whole analysis returns an error.
  Segments that succeeded stay cached, so a retry only re-asks the failed ones.

## Notes from sample runs (jev-1.13.0)

- ***Hamlet*** (15 segments). Hamlet, Claudius and Polonius are chosen. Segment 1 is clamped
  flat as stage-setting. Segment 9 — the arras, Polonius stabbed — reads rage at confidence
  1.00 for Hamlet, intensity 0.82, with `active_conflict` 0.99 and `sets_the_stage` 0.04.
  The same segment reads rage at only 0.55 confidence for Claudius, who is not in the room.
- ***The Great Gatsby***. Jay Gatsby, Daisy Buchanan and Tom Buchanan, with "Gatsby",
  "Daisy" and "Tom" correctly merged into their full names, and "Buchanan" correctly left
  unmerged because it is ambiguous between two characters.
- ***Jurassic Park*** (1993 film). Hammond, Ellie Sattler and Dennis Nedry — see the ranking
  note under Known limits for why Alan Grant does not make the cut.
- **Sample story, paste path** (11 paragraphs). Mara, Edric Voss and Tomas are found without
  being named. Mara's line matches the original single-character version of this app:
  flat through the stage-setting, joy at the homecoming, a rage peak at the argument, a
  despair dip at the empty tower, a dashed uncertain climax, then relief and joy.
- ***Little Red Riding Hood*** (6 segments). A good stress test: the plot section is short
  and padded with publication history, so the gate has to throw out "Grimms", "Brothers
  Grimm", "German" and "Sanitized". It does — but Charles Perrault survives at exactly 0.50,
  and the wolf is never a candidate at all. See Known limits.
- ***Sonnet 18***. Rejected with "has no plot, synopsis, or summary section", listing
  Structure, Context, Analysis, Recordings, Notes, References, External links.
