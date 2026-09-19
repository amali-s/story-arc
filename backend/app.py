"""FastAPI backend: the only place the TypeSafe API key is used.

Code owns the workflow:
  1. segment the story (story.py, deterministic)
  2. compute narration features (story.py, deterministic)
  3. ask System One the per-segment questions, one request per segment, cached
  4. compose chart values (compose.py, deterministic)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # TYPESAFE_API_KEY is read from the environment by the SDK

from cache import AnswerCache  # noqa: E402
from compose import compose, display_config  # noqa: E402
from questions import QUESTIONS, build_state, questions_fingerprint  # noqa: E402
from story import MAX_SEGMENTS, MAX_STORY_CHARS, narration_features, split_segments  # noqa: E402

MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")
# Segments analyzed concurrently. Each is one request with all questions.
MAX_CONCURRENT_REQUESTS = 8

app = FastAPI(title="Story Arc")
cache = AnswerCache()
QUESTIONS_FP = questions_fingerprint()


class AnalyzeRequest(BaseModel):
    story: str = Field(min_length=1, max_length=MAX_STORY_CHARS)
    character: str = Field(min_length=1, max_length=120)


async def _answer_segment(
    client: AsyncTypeSafeClient, sem: asyncio.Semaphore, state: dict
) -> tuple[dict, bool, str]:
    key = cache.key(state, QUESTIONS_FP, MODEL)
    if (hit := cache.get(key)) is not None:
        return hit["answers"], True, hit["model"]
    async with sem:
        response = await client.system_one(state, QUESTIONS, model=MODEL)
    answers = {name: answer.model_dump(mode="json") for name, answer in response.answers.items()}
    cache.put(key, {"answers": answers, "model": response.model})
    return answers, False, response.model


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    character = req.character.strip()
    segments = split_segments(req.story)
    if not segments:
        raise HTTPException(400, "The story is empty.")
    if len(segments) > MAX_SEGMENTS:
        raise HTTPException(
            400, f"The story splits into {len(segments)} segments; the limit is {MAX_SEGMENTS}."
        )

    states = [
        build_state(character, i, text, segments[i - 1] if i else "")
        for i, text in enumerate(segments)
    ]
    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    try:
        async with AsyncTypeSafeClient() as client:
            results = await asyncio.gather(*(_answer_segment(client, sem, s) for s in states))
    except TypeSafeError as exc:
        raise HTTPException(502, f"TypeSafe request failed: {exc}") from exc

    points = []
    for i, (text, (answers, cached, model)) in enumerate(zip(segments, results)):
        features = narration_features(text).to_dict()
        points.append(
            {
                "index": i,
                "text": text,
                "features": features,
                "answers": answers,
                "composed": compose(answers, features),
                "cached": cached,
                "model": model,
            }
        )
    return {
        "character": character,
        "points": points,
        "api_calls": sum(1 for _, cached, _ in results if not cached),
        "config": display_config(),
    }


FRONTEND = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
