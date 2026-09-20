"""Code counts names; System One judges them.

Mention counting stays in code on purpose. Jev 1.13's documented jaggedness
(docs.typesafe.ai/model-jaggedness/jev-1.13) includes being poor at counting
and numeric comparison, so the model is never asked how often a name appears —
only whether a name that code found is a character, and whether that character
drives the plot.

Candidate extraction is a capitalized-run heuristic, not an NER model: it adds
no dependency and its failure mode is over-generation, which is exactly what
the `is_character` gate is there to clean up. Known limits are in the README.
"""

from __future__ import annotations

import re
from collections import Counter

from story import split_sentences

# --- Tunable constants -------------------------------------------------------

# How many candidates survive counting and get sent to System One.
MAX_CANDIDATES = 8
# How many characters end up on the chart.
TOP_CHARACTERS = 3
# is_character probability at or above this passes the gate.
IS_CHARACTER_THRESHOLD = 0.5
# How the surviving candidates are ranked. Weights sum to 1.
RANK_WEIGHTS = {
    "is_main_character": 0.70,  # Noul: do this character's actions drive events
    "mentions": 0.30,           # normalized mention count (code)
}

# -----------------------------------------------------------------------------

_CAP = r"[A-ZÀ-Þ]"
_LOWER = r"[a-zß-ÿ’'’\-]"
_CAP_WORD = rf"{_CAP}{_LOWER}+"
# Lowercase particles that stay inside a name: "Ludwig van Beethoven".
# "of" and "the" are deliberately absent. They join a name to a title or a
# place far more often than to another name ("Prince Hamlet of Denmark",
# "the King of England"), and gluing those together wrecks the canonical name.
# The cost is that "Joan of Arc" is read as "Joan"; the benefit is that no
# character is renamed after their kingdom.
_PARTICLE = r"(?:de|del|della|van|von|der|di|da|le|la|bin|ibn)"
_NAME_RUN = re.compile(rf"{_CAP_WORD}(?:\s+(?:{_PARTICLE}\s+)?{_CAP_WORD})*")
_POSSESSIVE = re.compile(r"[’'\u2019]s$|[’'\u2019]$")

# Titles stripped from the front of a run so "Dr. Grant" and "Grant" merge.
_HONORIFICS = {
    "mr", "mrs", "ms", "miss", "dr", "doctor", "professor", "prof", "captain",
    "capt", "king", "queen", "prince", "princess", "lord", "lady", "sir", "dame",
    "uncle", "aunt", "general", "colonel", "sergeant", "lieutenant", "major",
    "father", "mother", "sister", "brother", "saint", "st", "president",
    "senator", "judge", "detective", "inspector", "officer", "reverend", "rabbi",
    "imam", "master", "mistress", "chief", "agent", "admiral", "commander",
}

# Words that are capitalized only because they start a sentence.
_STOPWORDS = {
    "the", "a", "an", "he", "she", "it", "they", "his", "her", "hers", "their",
    "theirs", "its", "our", "your", "my", "this", "that", "these", "those",
    "after", "before", "when", "while", "during", "although", "though", "despite",
    "because", "since", "however", "meanwhile", "later", "earlier", "eventually",
    "finally", "suddenly", "soon", "then", "now", "once", "as", "at", "in", "on",
    "to", "with", "without", "but", "and", "or", "if", "so", "for", "from", "by",
    "of", "upon", "having", "being", "both", "each", "all", "some", "many", "most",
    "one", "two", "three", "four", "five", "back", "next", "first", "second",
    "third", "last", "instead", "nevertheless", "unable", "unaware", "using",
    "unlike", "inside", "outside", "above", "below", "over", "under", "there",
    "here", "what", "who", "which", "whose", "where", "why", "how", "not", "no",
    "yes", "another", "several", "still", "just", "only", "even", "much", "more",
    "meanwhile", "afterward", "afterwards", "together", "alone", "away", "down",
    "up", "out", "off", "about", "against", "between", "through", "until", "upon",
}

# Capitalized words that are never a character name.
_NON_NAMES = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "chapter", "act", "scene", "part", "book", "volume", "prologue", "epilogue",
    "act i", "act ii", "act iii", "world war", "christmas", "easter",
}


def _clean_run(run: str) -> str | None:
    """Trim honorifics and sentence-initial stopwords; reject non-names."""
    # "Gatsby's" and "Grimms'" are the same name as "Gatsby" and "Grimms".
    # Only a trailing apostrophe is stripped, so "O'Brien" survives intact.
    words = [_POSSESSIVE.sub("", word) for word in run.split()]
    while words and words[0].lower().strip(".") in _HONORIFICS:
        words.pop(0)
    while words and words[0].lower() in _STOPWORDS:
        words.pop(0)
    while words and words[-1].lower() in _STOPWORDS:
        words.pop()
    if not words:
        return None

    name = " ".join(words)
    if name.lower() in _NON_NAMES:
        return None
    if len(words) == 1:
        word = words[0]
        if len(word) < 2 or word.lower() in _STOPWORDS or word.lower() in _NON_NAMES:
            return None
    return name


def _runs(text: str):
    """Yield (run, at_sentence_start) for every capitalized run in `text`."""
    for sentence in split_sentences(text):
        for match in _NAME_RUN.finditer(sentence):
            yield match.group(0), match.start() == 0


def _trim_sentence_start(name: str, trusted: set[str]) -> str:
    """Drop a leading common noun that is capitalized only by sentence position.

    "Industrialist John Hammond" opens a sentence, so the heuristic cannot tell
    the role noun from the name. If the whole run is never seen mid-sentence
    but some suffix of it is, that longest suffix is the real name. Names that
    do appear mid-sentence ("Alan Grant") are left alone.
    """
    if name in trusted:
        return name
    words = name.split()
    for start in range(1, len(words)):
        suffix = " ".join(words[start:])
        if suffix in trusted:
            return suffix
    return name


def count_names(text: str) -> Counter:
    """Every capitalized name-like run in `text`, with how often it appears."""
    runs = list(_runs(text))
    # A name seen mid-sentence is trustworthy: nothing but being a name explains
    # its capital letter there.
    trusted = {
        name for run, at_start in runs
        if not at_start and (name := _clean_run(run))
    }

    counts: Counter = Counter()
    for run, at_start in runs:
        name = _clean_run(run)
        if not name:
            continue
        if at_start:
            name = _trim_sentence_start(name, trusted)
        counts[name] += 1
    return counts


def _is_part_of(short: str, long: str) -> bool:
    """True when `short` is a strict word-prefix or word-suffix of `long`."""
    a, b = short.split(), long.split()
    if len(a) >= len(b):
        return False
    return b[: len(a)] == a or b[-len(a) :] == a


def merge_aliases(counts: Counter) -> list[dict]:
    """Fold shorter forms of a name into the full name they belong to.

    Longest names are established first, then every shorter name that is a
    word-prefix or word-suffix of exactly one of them is merged into it:
    "Gatsby" into "Jay Gatsby", "Riding Hood" into "Little Red Riding Hood".

    Only unambiguous merges are made. "Buchanan" matches both "Tom Buchanan"
    and "Daisy Buchanan", so it stays a candidate of its own rather than being
    guessed at. Nicknames and pronouns are never merged - see the README.
    """
    ordered_names = sorted(counts, key=lambda n: (-len(n.split()), -counts[n], n))

    canonical: list[str] = []
    entries: dict[str, dict] = {}
    for name in ordered_names:
        owners = [c for c in canonical if _is_part_of(name, c)]
        if len(owners) == 1:
            entry = entries[owners[0]]
            entry["mentions"] += counts[name]
            entry["aliases"].append(name)
        else:
            canonical.append(name)
            entries[name] = {"name": name, "mentions": counts[name], "aliases": []}

    result = sorted(entries.values(), key=lambda c: (-c["mentions"], c["name"]))
    for entry in result:
        entry["aliases"].sort()
    return result


def candidates(text: str, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Top `limit` candidate names by mention count, aliases already merged."""
    return merge_aliases(count_names(text))[:limit]


def rank(
    candidate_list: list[dict],
    answers: dict,
    threshold: float = IS_CHARACTER_THRESHOLD,
    weights: dict | None = None,
    top: int = TOP_CHARACTERS,
) -> tuple[list[dict], list[dict]]:
    """Combine the is_character gate, is_main_character, and mention share.

    Returns (chosen, considered). `considered` keeps every candidate with its
    scores so the UI can show what was rejected and why.
    """
    weights = weights or RANK_WEIGHTS
    most = max((c["mentions"] for c in candidate_list), default=0) or 1

    considered = []
    for i, candidate in enumerate(candidate_list):
        is_character = answers[f"is_character_{i}"]["noul"]
        is_main = answers[f"is_main_character_{i}"]["noul"]
        share = candidate["mentions"] / most
        considered.append(
            {
                **candidate,
                # A Noul answer carries no separate confidence: the probability
                # is the answer (NoulAnswer has only `type` and `noul`).
                "is_character": round(is_character, 4),
                "is_main_character": round(is_main, 4),
                "mention_share": round(share, 4),
                "score": round(
                    weights["is_main_character"] * is_main + weights["mentions"] * share, 4
                ),
                "passes_gate": is_character >= threshold,
            }
        )

    chosen = sorted(
        (c for c in considered if c["passes_gate"]), key=lambda c: -c["score"]
    )[:top]
    considered.sort(key=lambda c: -c["score"])
    return chosen, considered
