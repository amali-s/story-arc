"""Composition: turn System One answers + narration features into chart values.

All policy lives here as plain code. Tune the constants below; changing them
never re-calls the API, because raw answers are cached (see cache.py).
"""

from __future__ import annotations

import colorsys

from questions import ACTION_LEVELS, FEELINGS

# ---------------------------------------------------------------------------
# Tunable weights and thresholds
# ---------------------------------------------------------------------------

# Intensity is a weighted sum of signals, each already on 0..1. Weights sum to 1.
INTENSITY_WEIGHTS = {
    "action_intensity": 0.40,  # Score, normalized by (levels - 1)
    "physical_action": 0.15,   # Noul
    "active_conflict": 0.15,   # Noul
    "stakes_raised": 0.10,     # Noul
    "urgent_pacing": 0.10,     # Noul
    "pacing_features": 0.10,   # deterministic narration features (below)
}

# How the deterministic pacing features combine into one 0..1 signal.
PACING_FEATURE_WEIGHTS = {
    "short_sentence_share": 0.40,
    "punctuation": 0.30,       # ! and ? per sentence, capped at 1
    "brevity": 0.20,           # shorter average sentences -> higher
    "dialogue_ratio": 0.10,
}
# Average sentence length (words) mapped to brevity 1.0 .. 0.0.
BREVITY_SHORT_WORDS = 8
BREVITY_LONG_WORDS = 28

# Stage-setting clamp: flat line for exposition.
STAGE_SETTING_THRESHOLD = 0.7   # sets_the_stage probability at or above this...
STAGE_LOW_INTENSITY = 0.35      # ...and intensity below this...
STAGE_CLAMPED_INTENSITY = 0.03  # ...clamps intensity to this value.

# Offstage: character_is_focal below this fades the segment.
FOCAL_THRESHOLD = 0.5
OFFSTAGE_OPACITY = 0.3

# Uncertain intensity: action_intensity confidence below this -> dashed line.
INTENSITY_CONFIDENCE_THRESHOLD = 0.6

# Feeling colors (hex) blended by Choice probabilities.
FEELING_COLORS = {
    "joy": "#f2c318",      # yellow
    "rage": "#e0332b",     # red
    "despair": "#7b3fbf",  # purple
    "relief": "#2f7fe0",   # blue
}
# Saturation scale by feeling confidence: MIN at confidence 0, 1.0 at confidence 1.
MIN_SATURATION_FACTOR = 0.12
# Blend by the full probability distribution (True) or use only the top choice.
BLEND_BY_PROBABILITIES = True


# ---------------------------------------------------------------------------


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def pacing_signal(features: dict) -> dict:
    sentences = max(features["sentences"], 1)
    parts = {
        "short_sentence_share": features["short_sentence_share"],
        "punctuation": _clip((features["exclamations"] + features["questions"]) / sentences),
        "brevity": _clip(
            (BREVITY_LONG_WORDS - features["avg_sentence_length"])
            / (BREVITY_LONG_WORDS - BREVITY_SHORT_WORDS)
        ),
        "dialogue_ratio": features["dialogue_ratio"],
    }
    value = sum(PACING_FEATURE_WEIGHTS[k] * v for k, v in parts.items())
    return {"value": round(value, 4), "parts": {k: round(v, 4) for k, v in parts.items()}}


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(_clip(c) * 255):02x}" for c in rgb)


def feeling_color(probabilities: dict[str, float], choice: str, confidence: float) -> str:
    if BLEND_BY_PROBABILITIES:
        weights = {f: probabilities.get(f, 0.0) for f in FEELINGS}
    else:
        weights = {f: 1.0 if f == choice else 0.0 for f in FEELINGS}
    total = sum(weights.values()) or 1.0
    rgb = [0.0, 0.0, 0.0]
    for feeling, w in weights.items():
        for i, c in enumerate(_hex_to_rgb(FEELING_COLORS[feeling])):
            rgb[i] += c * w / total
    h, l, s = colorsys.rgb_to_hls(*rgb)
    s *= MIN_SATURATION_FACTOR + (1 - MIN_SATURATION_FACTOR) * _clip(confidence)
    return _rgb_to_hex(colorsys.hls_to_rgb(h, l, s))


def compose(answers: dict, features: dict) -> dict:
    """answers: raw per-question answer dicts (as returned by the API)."""
    nouls = {k: answers[k]["noul"] for k in (
        "character_is_focal", "sets_the_stage", "physical_action",
        "active_conflict", "stakes_raised", "urgent_pacing",
    )}
    action = answers["action_intensity"]
    feeling = answers["narrated_feeling"]
    pacing = pacing_signal(features)

    signals = {
        "action_intensity": action["score"] / (ACTION_LEVELS - 1),
        "physical_action": nouls["physical_action"],
        "active_conflict": nouls["active_conflict"],
        "stakes_raised": nouls["stakes_raised"],
        "urgent_pacing": nouls["urgent_pacing"],
        "pacing_features": pacing["value"],
    }
    raw_intensity = sum(INTENSITY_WEIGHTS[k] * v for k, v in signals.items())
    stage_clamped = (
        nouls["sets_the_stage"] >= STAGE_SETTING_THRESHOLD and raw_intensity < STAGE_LOW_INTENSITY
    )
    intensity = STAGE_CLAMPED_INTENSITY if stage_clamped else raw_intensity

    offstage = nouls["character_is_focal"] < FOCAL_THRESHOLD
    return {
        "intensity": round(_clip(intensity), 4),
        "raw_intensity": round(_clip(raw_intensity), 4),
        "stage_clamped": stage_clamped,
        "intensity_signals": {k: round(v, 4) for k, v in signals.items()},
        "pacing": pacing,
        "intensity_uncertain": action["confidence"] < INTENSITY_CONFIDENCE_THRESHOLD,
        "offstage": offstage,
        "opacity": OFFSTAGE_OPACITY if offstage else 1.0,
        "color": feeling_color(feeling["probabilities"], feeling["choice"], feeling["confidence"]),
    }


def display_config() -> dict:
    """Constants the frontend needs for the legend."""
    return {"feeling_colors": FEELING_COLORS, "offstage_opacity": OFFSTAGE_OPACITY}
