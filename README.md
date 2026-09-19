# Story Arc

Visualizes a character's journey through a story from how the story is narrated.

- **Height and shape** of the line show action intensity.
- **Color** shows the feeling TypeSafe reads in the narration: joy (yellow), rage (red), despair (purple), relief (blue).
- **Brightness** shows how sure it is. **Dashed** means uncertain intensity. **Faded** means the character is offstage.

It shows TypeSafe System One's "code in control" pattern. Code splits the story into segments, computes pacing features, runs the requests and draws the chart. System One answers only eight narrow, typed questions per segment.

## Run it

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or plain `pip`).

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
echo 'TYPESAFE_API_KEY=your_key_here' > .env   # skip if .env already exists
.venv/bin/uvicorn app:app --app-dir backend --port 8000
```

Open http://localhost:8000, click **Load sample story** (character: Mara), then click **Analyze**.

The API key is read from `.env` by the Python backend only. The browser talks to `/api/analyze` and never sees the key. `.env` is git-ignored.

## How it works

```
backend/
  story.py      1-3. segmentation + narration features (deterministic, no AI)
  questions.py  4.   state shape and the 8 System One questions
  app.py        4.   one System One request per segment, concurrent, cached
  cache.py           SQLite cache of raw answers (.cache/system_one.sqlite)
  compose.py    5.   intensity, color, flags: ALL WEIGHTS AND THRESHOLDS LIVE HERE
frontend/
  index.html, style.css, app.js   6. SVG chart, tooltip, legend (rendering only)
  sample.js                       original sample story
```

1. **Segmentation** (`story.py`): segments are paragraphs separated by blank lines. If the text has no blank lines, single line breaks are used. Paragraphs under `MIN_SEGMENT_WORDS` (40) are merged into the next paragraph.
2. **Narration features** (`story.py`): average sentence length, share of short sentences, `!`/`?` counts, and dialogue ratio (share of characters inside quotes).
3. **System One** (`questions.py`, `app.py`): each segment is one request with this state:
   `{"character": {"name"}, "segment": {"index", "text"}, "context": {"previous_segment"}}`.
   All 8 questions go in that one request, so they're evaluated in parallel. Segments run concurrently, up to `MAX_CONCURRENT_REQUESTS` (8) at a time.
   - Noul: `character_is_focal`, `sets_the_stage`, `physical_action`, `active_conflict`, `stakes_raised`, `urgent_pacing`
   - Score (5 levels, each with `what` and `signals`): `action_intensity`
   - Choice (4 options, each with `what`, `not_for` and `examples`): `narrated_feeling`
4. **Composition** (`compose.py`) turns answers into chart values:
   - `intensity` is a weighted sum of: `action_intensity.score / 4`, the four action Nouls, and a pacing-features signal.
   - If `sets_the_stage >= 0.7` and intensity is below `0.35`, intensity is clamped to `0.03`, which draws a flat line.
   - `color` blends the four feeling colors by the Choice `probabilities`. Its saturation is then scaled by `narrated_feeling.confidence`.
   - If `character_is_focal < 0.5`, the segment is offstage: opacity 0.3.
   - If `action_intensity.confidence < 0.6`, the segment's intensity is uncertain and drawn dashed.
5. **Chart** (`app.js`): monotone cubic (Fritsch–Carlson) interpolation. It's smooth but never overshoots, so flat stretches stay flat and sudden jumps stay sharp. Each point's half of the line takes that point's dash and opacity. Color is a gradient between neighboring points. Hovering (or tabbing to) a point shows its excerpt, all four feeling probabilities with confidence, intensity with Score confidence, every Noul probability, and the pacing features.

### Tuning

Every weight and threshold is a constant at the top of [`backend/compose.py`](backend/compose.py):
`INTENSITY_WEIGHTS`, `PACING_FEATURE_WEIGHTS`, `STAGE_SETTING_THRESHOLD`, `STAGE_LOW_INTENSITY`, `STAGE_CLAMPED_INTENSITY`, `FOCAL_THRESHOLD`, `OFFSTAGE_OPACITY`, `INTENSITY_CONFIDENCE_THRESHOLD`, `FEELING_COLORS`, `MIN_SATURATION_FACTOR` and `BLEND_BY_PROBABILITIES`.
Segmentation constants are at the top of [`backend/story.py`](backend/story.py). Restart the server after editing.

Changing a composition constant **does not** re-call the API. The cache key hashes the exact state, the question definitions and the model name. Only edits to a segment (or the segment before it), the character, or the questions cause new calls. Delete `.cache/` to force a fresh run.

## TypeSafe API details: verified vs. assumed

Verified against https://docs.typesafe.ai (API reference, Python SDK pages, Confidence, Score, Choice, Advanced structure, Models) and by inspecting the installed `typesafe-sdk` 0.7.0:

| Detail | Source | What the app relies on |
| --- | --- | --- |
| Choice answers include the full distribution | API reference: Choice answer `probabilities` (sum to 1); SDK `ChoiceAnswer` fields `choice`, `confidence`, `probabilities` | Color blends by probability |
| Score range | Score page: "from 0 to the top level number"; divide by `len(criteria) - 1` | 5 levels → 0–4, normalized by 4 |
| Auth | SDK reads `TYPESAFE_API_KEY` from the environment (`constants.API_KEY_ENV`); HTTP uses `Authorization: Bearer` | Key loaded from `.env` in the backend |
| Limits | Primitives page: question count limited only by the token budget; Models page: 64k tokens per request, 32k for state + longest question, 1,200 req/min, 250k tok/s, and these "can change without notice" | ~1 paragraph of state per request is far under budget; story capped at 60k chars / 60 segments; SDK retries 429/529 with backoff |
| Async client | `AsyncTypeSafeClient().system_one(state, questions, model=...)`; `response.answers[name]`, `response.model` | Concurrent requests; versioned model id shown in the UI |
| Structured criteria | Advanced structure / Choice pages: criteria values may be JSON objects, and field names such as `what`, `not_for`, `examples`, `signals` are free-form | Used for the Score levels and Choice options |
| Default model | `jev-latest` alias (currently `jev-1.13.0`) | Overridable with `TYPESAFE_DEFAULT_MODEL` |

Assumed or chosen (not specified by the docs):

- Composition weights, thresholds and colors are my starting points, not values from the docs. Tune them on your own stories.
- The concurrency limit of 8 is a conservative choice, not a documented number.
- If any segment's request fails (after SDK retries), the whole analysis returns an error. Segments that succeeded stay cached, so a retry only re-asks the failed ones.

## Notes from the sample run (jev-1.13.0)

- Segments 1 and 5 are read as offstage. Paragraph 1 describes the island before Mara appears; paragraph 5 is Voss alone in his office.
- Segments 1–2 are clamped flat as stage-setting and come out muted, because the feeling reading is uncertain.
- The argument (7) is read as rage and the empty tower (8) as despair, both with high confidence. The relief (10) and joy (11) endings match the story.
- The climax (9) gets the highest action score (3.5/4) but only 0.59 Score confidence, so it's drawn dashed. The feeling read there is split between rage and relief, so its color is muted.
