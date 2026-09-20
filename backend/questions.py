"""The narrow, typed questions System One answers.

Two request shapes, both built here.

1. Character confirmation, one request per story (`candidate_questions`):

    {
      "story": {"title": ..., "plot": ...},
      "candidates": [{"name": ..., "mentions": n}, ...]
    }

   Two Nouls per candidate, all in one request so they run in parallel.
   `candidates[i].mentions` is in the state as context but no question ever
   refers to it: jev-1.13 is documented as poor at counting and numeric
   comparison, so code does the counting and the ranking arithmetic.

2. Journey analysis, one request per segment covering every character
   (`segment_questions`):

    {
      "characters": [{"name": ...}, ...],
      "segment": {"index": i, "text": ...},
      "context": {"previous_segment": ...}
    }

   Five questions are asked once per segment because they are about the
   narration, not about anyone in particular. Three are asked per character,
   suffixed _0/_1/_2.

Question ids are for code only; the model never sees them, so every
instruction carries its full meaning and references state by backticked path.
The `characters[0].name` / `candidates[0].name` syntax is the documented way
to point at an element of a state array.
"""

from __future__ import annotations

import hashlib
import json

from typesafe_sdk import Choice, Noul, NoulCriteria, Score

FEELINGS = ("joy", "rage", "despair", "relief")
ACTION_LEVELS = 5


def questions_fingerprint(questions: dict) -> str:
    """Stable hash of question definitions, used as part of the cache key."""
    payload = {k: q.model_dump(mode="json") for k, q in sorted(questions.items())}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. Character confirmation: "jev confirms" what "code counts" proposed
# ---------------------------------------------------------------------------


def build_candidate_state(title: str, plot: str, candidates: list[dict]) -> dict:
    return {
        "story": {"title": title, "plot": plot},
        "candidates": [{"name": c["name"], "mentions": c["mentions"]} for c in candidates],
    }


def candidate_questions(count: int) -> dict:
    """Two Nouls per candidate: is it a character, and does it drive the plot?"""
    questions: dict = {}
    for i in range(count):
        who = f"`candidates[{i}].name`"
        questions[f"is_character_{i}"] = Noul(
            instructions=(
                f"In `story.plot`, is {who} a character - a person, an animal, or a "
                f"being that acts - rather than a place, a group, an object, or a title?"
            ),
            criteria=NoulCriteria(
                true=f"{who} is an individual who acts, speaks, or has things happen to them",
                false=(
                    f"{who} is a place, a nation, an organization, a crowd, an object, "
                    f"a species, or the title of the work"
                ),
            ),
        )
        questions[f"is_main_character_{i}"] = Noul(
            instructions=(
                f"Do the actions or the fate of {who} drive the events of `story.plot`?"
            ),
            criteria=NoulCriteria(
                true=(
                    f"What {who} does, wants, or suffers causes or shapes the events of the plot"
                ),
                false=(
                    f"{who} appears but the plot would run much the same way without them"
                ),
            ),
        )
    return questions


# ---------------------------------------------------------------------------
# 2. Journey analysis: shared per segment + per character
# ---------------------------------------------------------------------------


def build_segment_state(names: list[str], index: int, text: str, previous: str) -> dict:
    return {
        "characters": [{"name": name} for name in names],
        "segment": {"index": index, "text": text},
        "context": {"previous_segment": previous},
    }


# Asked once per segment: these are about the narration, not about a person.
SHARED_QUESTIONS = {
    "sets_the_stage": Noul(
        instructions=(
            "Does `segment.text` mainly describe setting, background, or exposition "
            "rather than events happening in the story's present?"
        ),
        criteria=NoulCriteria(
            true="Mostly description of place, history, routine, or who someone is",
            false="Mostly events, actions, or exchanges unfolding now",
        ),
    ),
    "physical_action": Noul(
        instructions="Does `segment.text` narrate physical action or movement happening now?",
        criteria=NoulCriteria(
            true="Bodies or objects move in the story's present: running, fighting, climbing, falling, grabbing",
            false="Stillness, talk, thought, memory, or description",
        ),
    ),
    "active_conflict": Noul(
        instructions="Does `segment.text` show a conflict or confrontation actively unfolding?",
        criteria=NoulCriteria(
            true="Opposing people or forces clash in this passage: argument, threat, fight, pursuit, struggle against danger",
            false="No clash is happening in this passage, even if one is remembered or expected",
        ),
    ),
    "urgent_pacing": Noul(
        instructions="Does the narration in `segment.text` compress time or create urgency?",
        criteria=NoulCriteria(
            true="Rapid succession of moments, clipped sentences, countdowns, 'suddenly', no room to breathe",
            false="Time moves slowly or is summarized calmly; the telling lingers",
        ),
    ),
    "action_intensity": Score(
        instructions=(
            "How intense is the action narrated in `segment.text` for the story "
            "at this point? Rate what happens in this passage, not the story overall."
        ),
        criteria=[
            {
                "what": "Calm or descriptive",
                "signals": [
                    "Setting, background, routine, or reflection",
                    "Nothing is at risk in the moment",
                ],
            },
            {
                "what": "Mild tension",
                "signals": [
                    "A hint of trouble, unease, or an unanswered question",
                    "Characters notice something is off but nothing has happened yet",
                ],
            },
            {
                "what": "Clear rising action",
                "signals": [
                    "A problem is actively developing",
                    "Characters make choices or move toward a confrontation",
                ],
            },
            {
                "what": "Intense action",
                "signals": [
                    "Open conflict, pursuit, or danger in progress",
                    "Fast physical events with real risk",
                ],
            },
            {
                "what": "Climactic peak",
                "signals": [
                    "The decisive confrontation or all-out struggle of the story, against a person, nature, or time",
                    "Everything is at stake and the outcome turns on this moment",
                ],
            },
        ],
    ),
}

SHARED_NOULS = ("sets_the_stage", "physical_action", "active_conflict", "urgent_pacing")
# Asked once per character per segment, suffixed _0/_1/_2.
PER_CHARACTER_NOULS = ("character_is_focal", "stakes_raised")


def _per_character_questions(k: int) -> dict:
    who = f"`characters[{k}].name`"
    return {
        f"character_is_focal_{k}": Noul(
            instructions=(
                f"Is {who} present in, or the focus of, `segment.text`? "
                f"Count pronouns and descriptions that clearly refer to {who}."
            ),
            criteria=NoulCriteria(
                true=f"{who} appears in or is the focus of this passage",
                false=f"The passage is about other people, places, or events while {who} is absent",
            ),
        ),
        f"stakes_raised_{k}": Noul(
            instructions=(
                f"Compared with `context.previous_segment`, does `segment.text` raise the stakes "
                f"for {who}? If `context.previous_segment` is empty, judge whether "
                f"`segment.text` already puts something important to {who} at risk."
            ),
            criteria=NoulCriteria(
                true=f"New danger, loss, deadline, or consequence for {who} appears or grows",
                false="The stakes stay the same or fall",
            ),
        ),
        f"narrated_feeling_{k}": Choice(
            instructions={
                "question": (
                    f"What feeling does the narration of `segment.text` convey around {who} "
                    f"at this point?"
                ),
                "focus": (
                    f"Judge the feeling the telling creates through word choice, imagery, and pacing, "
                    f"not only what {who} says they feel. Use `context.previous_segment` "
                    f"to tell whether tension is building or releasing."
                ),
            },
            criteria={
                "joy": {
                    "what": "Warmth, delight, triumph, lightness; the telling is bright and open",
                    "not_for": "Mere release of tension after danger with no real gladness (relief)",
                    "examples": [
                        "Laughter spilled across the square as the lanterns went up.",
                        "She held the trophy over her head and the whole crowd roared with her.",
                    ],
                },
                "rage": {
                    "what": "Anger, hostility, violent tension; the telling is hot, harsh, and aimed at someone",
                    "not_for": "Hopelessness or grief with no target to strike at (despair)",
                    "examples": [
                        "He slammed the ledger shut. 'You lied to every one of us.'",
                        "Her fists shook as the soldiers dragged her brother away.",
                    ],
                },
                "despair": {
                    "what": "Hopelessness, grief, dread, defeat; the telling is heavy, dark, and closing in",
                    "not_for": "Anger directed outward at an enemy (rage)",
                    "examples": [
                        "There was no one left to call. The line rang and rang.",
                        "The water kept rising, and every door she tried was locked.",
                    ],
                },
                "relief": {
                    "what": "Tension releasing, danger passing, calm after strain; the telling exhales",
                    "not_for": "Celebration or delight that goes beyond the easing of strain (joy)",
                    "examples": [
                        "The footsteps faded down the hall, and he let himself breathe.",
                        "When the fever broke at dawn, the room finally went quiet.",
                    ],
                },
            },
        ),
    }


def segment_questions(count: int) -> dict:
    """All questions for one segment: shared ones plus three per character."""
    questions = dict(SHARED_QUESTIONS)
    for k in range(count):
        questions.update(_per_character_questions(k))
    return questions
