"""Deterministic story processing: segmentation and narration-pacing features.

No model calls happen here. Everything is plain text processing so the results
are reproducible and free.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Paragraphs with fewer words than this are merged into a neighbor.
MIN_SEGMENT_WORDS = 40
# A sentence with this many words or fewer counts as "short".
SHORT_SENTENCE_WORDS = 8
# Guardrails on input size (cost and the ~32k-token state budget per request).
MAX_STORY_CHARS = 60_000
MAX_SEGMENTS = 60

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+")
_WORD = re.compile(r"[A-Za-z0-9’']+")
# Straight double quotes, curly double quotes, and guillemets.
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|«[^»]*»")


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


def split_segments(story: str) -> list[str]:
    """Split a story into ordered segments.

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


def narration_features(text: str) -> Features:
    sentences = [s for s in _SENTENCE_SPLIT.split(text.replace("\n", " ")) if _word_count(s)]
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
