"""Deterministic story processing: segmentation and narration-pacing features.

No model calls happen here. Everything is plain text processing so the results
are reproducible and free.

Two segmentation modes:
  * SENTENCES  — groups of ~SENTENCES_PER_SEGMENT sentences. Used for Wikipedia
    plot summaries, which are a few dense paragraphs; paragraphs would give
    three or four points and a useless chart.
  * PARAGRAPHS — blank-line paragraphs, merged up to MIN_SEGMENT_WORDS. Used
    for pasted stories, where paragraphs are the author's own beats.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# --- Tunable segmentation constants -----------------------------------------

# SENTENCES mode: how many sentences make one segment (the spec's "about 2-3").
SENTENCES_PER_SEGMENT = 3
# A trailing group with fewer than this many sentences is merged back.
MIN_TRAILING_SENTENCES = 2
# PARAGRAPHS mode: paragraphs with fewer words than this are merged into a neighbor.
MIN_SEGMENT_WORDS = 40
# A sentence with this many words or fewer counts as "short".
SHORT_SENTENCE_WORDS = 8
# Guardrails on input size (cost and the ~32k-token state budget per request).
MAX_STORY_CHARS = 60_000
MAX_SEGMENTS = 60

SENTENCES = "sentences"
PARAGRAPHS = "paragraphs"

# ----------------------------------------------------------------------------

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+")
_WORD = re.compile(r"[A-Za-z0-9’']+")
# Straight double quotes, curly double quotes, and guillemets.
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|«[^»]*»")

# Tokens whose trailing period does not end a sentence. Plot summaries are full
# of "Dr. Grant" and "J. R. R. Tolkien", which would otherwise split mid-name.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "st", "sr", "jr", "lt", "capt", "gen", "col",
    "sgt", "rev", "hon", "gov", "pres", "vs", "etc", "approx", "no", "fig", "al",
    "inc", "ltd", "co", "mt", "ft", "e.g", "i.e", "cf",
}
_TRAILING_TOKEN = re.compile(r"([A-Za-z.]+)\.$")


@dataclass
class Features:
    avg_sentence_length: float
    short_sentence_share: float
    exclamations: int
    questions: int
    dialogue_ratio: float
    sentences: int
    words: int

    def to_dict(self) -> dict:
        return asdict(self)


def _word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _is_false_stop(fragment: str) -> bool:
    """True when `fragment`'s final period is an abbreviation or an initial."""
    match = _TRAILING_TOKEN.search(fragment.strip())
    if not match:
        return False
    token = match.group(1).lower().strip(".")
    # A dotted initialism ("U.S.", "e.g.", "Ph.D.") or a single initial ("J.").
    # A real sentence that truly ends in one gets merged forward; that is the
    # cheaper mistake, since segments are groups of sentences anyway.
    return token in _ABBREVIATIONS or len(token) == 1 or "." in token


def split_sentences(text: str) -> list[str]:
    """Sentences, whitespace-normalized, without splitting on abbreviations."""
    flat = " ".join(text.split())
    if not flat:
        return []
    parts = _SENTENCE_SPLIT.split(flat)

    merged: list[str] = []
    for part in parts:
        if merged and _is_false_stop(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [s for s in merged if _word_count(s)]


def split_sentence_groups(
    text: str, per_segment: int = SENTENCES_PER_SEGMENT
) -> list[str]:
    """Group sentences into segments of `per_segment`.

    Groups run straight through paragraph breaks: a plot summary's paragraphs
    are the encyclopedia's structure, not the story's beats.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []
    size = max(1, per_segment)
    groups = [" ".join(sentences[i : i + size]) for i in range(0, len(sentences), size)]
    tail = len(sentences) % size
    if len(groups) > 1 and tail and tail < MIN_TRAILING_SENTENCES:
        groups[-2] = f"{groups[-2]} {groups[-1]}"
        groups.pop()
    return groups


def split_paragraphs(story: str) -> list[str]:
    """Split a story into ordered segments on paragraph boundaries.

    Paragraphs (blank-line separated) are the default unit. If the text has no
    blank lines, single line breaks are used instead. Paragraphs shorter than
    MIN_SEGMENT_WORDS are merged forward into the next paragraph; a short
    trailing paragraph is merged back into the previous one.
    """
    text = story.replace("\r\n", "\n").strip()
    if not text:
        return []

    parts = _PARAGRAPH_SPLIT.split(text)
    if len(parts) == 1:
        parts = text.split("\n")
    paragraphs = [" ".join(p.split()) for p in parts if p.strip()]

    segments: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if _word_count(buffer) >= MIN_SEGMENT_WORDS:
            segments.append(buffer)
            buffer = ""
    if buffer:
        if segments:
            segments[-1] = f"{segments[-1]}\n\n{buffer}"
        else:
            segments.append(buffer)
    return segments


def split_segments(story: str, mode: str = PARAGRAPHS) -> list[str]:
    """Segment `story` in the given mode. See SENTENCES / PARAGRAPHS above."""
    if mode == SENTENCES:
        return split_sentence_groups(story)
    return split_paragraphs(story)


def narration_features(text: str) -> Features:
    sentences = split_sentences(text)
    lengths = [_word_count(s) for s in sentences] or [0]
    words = sum(lengths)
    quoted_chars = sum(len(m) for m in _QUOTED.findall(text))
    return Features(
        avg_sentence_length=round(words / max(len(sentences), 1), 2),
        short_sentence_share=round(
            sum(1 for n in lengths if n <= SHORT_SENTENCE_WORDS) / max(len(sentences), 1), 3
        ),
        exclamations=text.count("!"),
        questions=text.count("?"),
        dialogue_ratio=round(quoted_chars / max(len(text), 1), 3),
        sentences=len(sentences),
        words=words,
    )
