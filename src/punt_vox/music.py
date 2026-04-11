"""Vibe-to-prompt mapping for music generation."""

from __future__ import annotations

from collections import Counter

# -- Layer 2: time-of-day brackets (hour -> phrase) -------------------------

_TIME_OF_DAY: tuple[tuple[range, str], ...] = (
    (range(6, 12), "late-morning energy, fresh and building momentum"),
    (range(12, 17), "afternoon focus, steady and locked in"),
    (range(17, 22), "evening wind-down, reflective but still moving"),
    # 22-05 handled as the fallback
)

_LATE_NIGHT = "late-night deep focus, minimal and hypnotic"

# -- Layer 3: work-intensity thresholds -------------------------------------

_COMMIT_THRESHOLD = 3
_FAIL_THRESHOLD = 2

_FLOW_STATE = "productive flow state, things are shipping"
_GRINDING = "grinding through a tough problem, tension building"
_STEADY = "steady working pace"

# -- Layer 4: constant suffix -----------------------------------------------

_LOOP_SUFFIX = (
    "Loopable, no distinct intro or outro, smooth ambient texture "
    "that cycles naturally. Driving beat but not overwhelming \u2014 "
    "background music for deep work."
)

_DEFAULT_STYLE = "ambient"


def vibe_to_prompt(
    vibe: str | None,
    vibe_tags: str | None,
    style: str | None,
    hour: int,
    signals: list[str],
) -> str:
    """Assemble a music-generation prompt from session context.

    Each of the four layers contributes a distinct dimension. Layers
    with no content are omitted; the remaining layers are joined with
    ``". "`` separators.
    """
    layers: list[str] = [
        _layer_style_mood_feel(vibe, vibe_tags, style),
        _layer_time_of_day(hour),
        _layer_work_intensity(signals),
        _LOOP_SUFFIX,
    ]
    return ". ".join(layer for layer in layers if layer)


# -- Layer builders ---------------------------------------------------------


def _layer_style_mood_feel(
    vibe: str | None,
    vibe_tags: str | None,
    style: str | None,
) -> str:
    """Build layer 1: style + mood + feel."""
    parts: list[str] = []

    if style:
        parts.append(f"{style} music")
    else:
        parts.append(f"{_DEFAULT_STYLE} music")

    if vibe:
        parts.append(f"{vibe} mood")

    if vibe_tags:
        # Strip brackets: "[cheerful]" -> "cheerful"
        clean = vibe_tags.strip().strip("[]")
        if clean:
            parts.append(f"{clean} feel")

    return ", ".join(parts)


def _layer_time_of_day(hour: int) -> str:
    """Map hour (0-23) to a time-of-day phrase."""
    for hours, phrase in _TIME_OF_DAY:
        if hour in hours:
            return phrase
    return _LATE_NIGHT


def _layer_work_intensity(signals: list[str]) -> str:
    """Derive work-intensity phrase from recent signal strings."""
    counts = Counter(signals)
    if counts.get("git-commit", 0) >= _COMMIT_THRESHOLD:
        return _FLOW_STATE
    if counts.get("tests-fail", 0) >= _FAIL_THRESHOLD:
        return _GRINDING
    return _STEADY
