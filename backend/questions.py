"""The narrow, typed questions System One answers about each story segment.

State shape sent with every request (see `build_state`):

    {
      "character": {"name": ...},
      "segment": {"index": i, "text": ...},
      "context": {"previous_segment": ...}
    }

Question ids are for code only; the model never sees them, so every
instruction carries its full meaning and references state by backticked path.
"""

from __future__ import annotations

import hashlib
import json

from typesafe_sdk import Choice, Noul, NoulCriteria, Score

FEELINGS = ("joy", "rage", "despair", "relief")
ACTION_LEVELS = 5


def build_state(character: str, index: int, text: str, previous: str) -> dict:
    return {
        "character": {"name": character},
        "segment": {"index": index, "text": text},
        "context": {"previous_segment": previous},
    }


QUESTIONS = {
    "character_is_focal": Noul(
        instructions=(
            "Is `character.name` present in, or the focus of, `segment.text`? "
            "Count pronouns and descriptions that clearly refer to `character.name`."
        ),
        criteria=NoulCriteria(
            true="`character.name` appears in or is the focus of this passage",
            false="The passage is about other people, places, or events while `character.name` is absent",
        ),
    ),
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
    "stakes_raised": Noul(
        instructions=(
            "Compared with `context.previous_segment`, does `segment.text` raise the stakes "
            "for `character.name`? If `context.previous_segment` is empty, judge whether "
            "`segment.text` already puts something important to `character.name` at risk."
        ),
        criteria=NoulCriteria(
            true="New danger, loss, deadline, or consequence for `character.name` appears or grows",
            false="The stakes stay the same or fall",
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
    "narrated_feeling": Choice(
        instructions={
            "question": "What feeling does the narration of `segment.text` convey at this point in the story?",
            "focus": (
                "Judge the feeling the telling creates through word choice, imagery, and pacing, "
                "not only what `character.name` says they feel. Use `context.previous_segment` "
                "to tell whether tension is building or releasing."
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


def questions_fingerprint() -> str:
    """Stable hash of the question definitions, used as part of the cache key."""
    payload = {k: q.model_dump(mode="json") for k, q in sorted(QUESTIONS.items())}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
