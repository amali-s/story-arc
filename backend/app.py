"""FastAPI backend: the only place the TypeSafe API key is used.

Code owns the whole workflow. System One never searches and never counts; it
only judges text that this backend retrieved and split.

  /api/search      1. Wikipedia title search            (code, no AI)
  /api/plot        2. plot section -> plain text        (code, no AI)
                   3. segmentation                      (code, no AI)
  /api/characters  4. candidate names + mention counts  (code, no AI)
                   5. one request: is_character / is_main_character per
                      candidate, then ranking           (code ranks)
  /api/journeys    6. one request per segment, all characters at once
                   7. composition into chart values     (code, no AI)

The frontend calls them in that order so each stage can report progress.
Every stage is cached, so later stages re-derive earlier ones for free.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # TYPESAFE_API_KEY is read from the environment by the SDK

import characters as chars  # noqa: E402
import wiki  # noqa: E402
from cache import AnswerCache  # noqa: E402
from compose import compose, display_config  # noqa: E402
from questions import (  # noqa: E402
    build_candidate_state,
    build_segment_state,
    candidate_questions,
    questions_fingerprint,
    segment_questions,
)
from story import (  # noqa: E402
    MAX_SEGMENTS,
    MAX_STORY_CHARS,
    PARAGRAPHS,
    SENTENCES,
    SENTENCES_PER_SEGMENT,
    narration_features,
    split_segments,
)

MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")
# Segments analyzed concurrently. Each is one request with all questions.
MAX_CONCURRENT_REQUESTS = 8

app = FastAPI(title="Story Arc")
cache = AnswerCache()


# ---------------------------------------------------------------------------
# Sources: a Wikipedia article, or text the user pasted
# ---------------------------------------------------------------------------


class Source(BaseModel):
    kind: Literal["wikipedia", "text"] = "wikipedia"
    pageid: int | None = None
    story: str | None = Field(default=None, max_length=MAX_STORY_CHARS)
    title: str | None = Field(default=None, max_length=200)


@dataclass
class Document:
    doc_id: str
    title: str
    url: str
    section: str
    attribution: str
    text: str
    segments: list[str]
    mode: str

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "url": self.url,
            "section": self.section,
            "attribution": self.attribution,
            "segments": self.segments,
            "segment_mode": self.mode,
            "sentences_per_segment": SENTENCES_PER_SEGMENT,
            "chars": len(self.text),
        }


async def resolve(source: Source) -> Document:
    """Turn a source into retrieved, segmented text. Wikipedia fetches are cached."""
    if source.kind == "wikipedia":
        if not source.pageid:
            raise HTTPException(400, "Pick a Wikipedia article first.")
        doc_id = f"wiki:{source.pageid}"
        cached = cache.get_document(doc_id)
        if cached is None:
            async with wiki.client() as http:
                try:
                    plot = await wiki.fetch_plot(http, source.pageid)
                except wiki.PlotNotFound as exc:
                    listed = ", ".join(exc.sections[:8]) or "none"
                    raise HTTPException(
                        404,
                        f"“{exc.title}” has no plot, synopsis, or summary section, so there is "
                        f"nothing to chart. Its sections are: {listed}. Poems and sonnets often "
                        f"have only analysis sections — try a novel, film, or folk tale.",
                    ) from exc
                except wiki.WikiError as exc:
                    raise HTTPException(502, str(exc)) from exc
            cached = plot.to_dict()
            cache.put_document(doc_id, cached)
        return Document(
            doc_id=doc_id,
            title=cached["title"],
            url=cached["url"],
            section=cached["section"],
            attribution=cached["attribution"],
            text=cached["text"],
            segments=split_segments(cached["text"], SENTENCES),
            mode=SENTENCES,
        )

    text = (source.story or "").strip()
    if not text:
        raise HTTPException(400, "The story is empty.")
    return Document(
        doc_id="text:" + hashlib.sha256(text.encode()).hexdigest()[:16],
        title=(source.title or "Pasted text").strip(),
        url="",
        section="",
        attribution="",
        text=text,
        segments=split_segments(text, PARAGRAPHS),
        mode=PARAGRAPHS,
    )


def _check_segments(document: Document) -> None:
    if not document.segments:
        raise HTTPException(400, "The text is empty.")
    if len(document.segments) > MAX_SEGMENTS:
        raise HTTPException(
            400,
            f"The text splits into {len(document.segments)} segments; the limit is {MAX_SEGMENTS}.",
        )


# ---------------------------------------------------------------------------
# System One calls
# ---------------------------------------------------------------------------


async def _ask(
    client: AsyncTypeSafeClient,
    state: dict,
    questions: dict,
    fingerprint: str,
    sem: asyncio.Semaphore | None = None,
) -> tuple[dict, bool, str]:
    """One System One request, cached on (state, questions, model)."""
    key = cache.key(state, fingerprint, MODEL)
    if (hit := cache.get(key)) is not None:
        return hit["answers"], True, hit["model"]
    if sem is not None:
        async with sem:
            response = await client.system_one(state, questions, model=MODEL)
    else:
        response = await client.system_one(state, questions, model=MODEL)
    answers = {name: answer.model_dump(mode="json") for name, answer in response.answers.items()}
    cache.put(key, {"answers": answers, "model": response.model})
    return answers, False, response.model


async def confirm_characters(document: Document) -> dict:
    """Step 4-5: code counts names, one request confirms them, code ranks them.

    The raw answers are cached on the state, so re-ranking with different
    weights costs nothing and `/api/journeys` reuses `/api/characters`.
    """
    candidates = chars.candidates(document.text)
    if not candidates:
        raise HTTPException(
            422,
            "No character names were found in this text. The name finder looks for "
            "capitalized names, so a summary written without them will come up empty.",
        )

    state = build_candidate_state(
        document.title, document.text[: wiki.MAX_PLOT_CHARS], candidates
    )
    questions = candidate_questions(len(candidates))
    fingerprint = questions_fingerprint(questions)

    try:
        async with AsyncTypeSafeClient() as client:
            answers, cached, model = await _ask(client, state, questions, fingerprint)
    except TypeSafeError as exc:
        raise HTTPException(502, f"TypeSafe request failed: {exc}") from exc

    chosen, considered = chars.rank(candidates, answers)
    if not chosen:
        raise HTTPException(
            422,
            "None of the names found look like characters — they read as places, "
            "groups, or objects. Try a different article.",
        )

    note = ""
    if len(chosen) < chars.TOP_CHARACTERS:
        note = (
            f"Only {len(chosen)} of {len(candidates)} candidate names passed the "
            f"“is a character” check, so the chart shows "
            f"{len(chosen)} instead of {chars.TOP_CHARACTERS}."
        )

    return {
        "characters": chosen,
        "considered": considered,
        "note": note,
        "api_calls": 0 if cached else 1,
        "model": model,
        "gate": {
            "is_character_threshold": chars.IS_CHARACTER_THRESHOLD,
            "rank_weights": chars.RANK_WEIGHTS,
        },
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/search")
async def search(q: str = Query(min_length=1, max_length=200)) -> dict:
    """Step 1. Code searches; the user picks. Nothing is sent to System One."""
    async with wiki.client() as http:
        try:
            results = await wiki.search(http, q)
        except wiki.WikiError as exc:
            raise HTTPException(502, str(exc)) from exc
    return {"query": q, "results": [r.to_dict() for r in results]}


@app.post("/api/plot")
async def plot(source: Source) -> dict:
    """Steps 2-3. Retrieval and segmentation, both deterministic."""
    document = await resolve(source)
    _check_segments(document)
    return document.to_dict()


@app.post("/api/characters")
async def characters_endpoint(source: Source) -> dict:
    """Steps 4-5. Code counts names, System One confirms, code ranks."""
    document = await resolve(source)
    _check_segments(document)
    return {"title": document.title, **await confirm_characters(document)}


@app.post("/api/journeys")
async def journeys(source: Source) -> dict:
    """Steps 6-7. One request per segment covering every character."""
    document = await resolve(source)
    _check_segments(document)
    confirmed = await confirm_characters(document)
    names = [c["name"] for c in confirmed["characters"]]

    questions = segment_questions(len(names))
    fingerprint = questions_fingerprint(questions)
    states = [
        build_segment_state(names, i, text, document.segments[i - 1] if i else "")
        for i, text in enumerate(document.segments)
    ]

    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    try:
        async with AsyncTypeSafeClient() as client:
            results = await asyncio.gather(
                *(_ask(client, state, questions, fingerprint, sem) for state in states)
            )
    except TypeSafeError as exc:
        raise HTTPException(502, f"TypeSafe request failed: {exc}") from exc

    points = []
    for i, (text, (answers, cached, model)) in enumerate(zip(document.segments, results)):
        features = narration_features(text).to_dict()
        points.append(
            {
                "index": i,
                "text": text,
                "features": features,
                "answers": answers,
                "composed": [compose(answers, features, k) for k in range(len(names))],
                "cached": cached,
                "model": model,
            }
        )

    return {
        "document": document.to_dict(),
        "characters": confirmed["characters"],
        "considered": confirmed["considered"],
        "note": confirmed["note"],
        "gate": confirmed["gate"],
        "points": points,
        "api_calls": confirmed["api_calls"] + sum(1 for _, cached, _ in results if not cached),
        "config": display_config(),
    }


FRONTEND = ROOT / "frontend"


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Make the browser revalidate the page and its assets on every load.

    Without this the browser applies heuristic freshness to /static/*, so an
    edited app.js or style.css keeps serving from cache after a reload.
    `no-cache` still allows a 304 via ETag, so nothing is re-downloaded unless
    it actually changed.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
