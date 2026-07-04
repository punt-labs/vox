"""Vibe-to-prompt mapping for music generation."""

from __future__ import annotations

from collections import Counter

_TIME_OF_DAY: tuple[tuple[range, str], ...] = (
    (range(6, 12), "late-morning energy, fresh and building momentum"),
    (range(12, 17), "afternoon focus, steady and locked in"),
    (range(17, 22), "evening wind-down, reflective but still moving"),
)
_LATE_NIGHT = "late-night deep focus, minimal and hypnotic"

_COMMIT_THRESHOLD = 3
_FAIL_THRESHOLD = 2
_FLOW_STATE = "productive flow state, things are shipping"
_GRINDING = "grinding through a tough problem, tension building"
_STEADY = "steady working pace"

# One descriptor per generated track so a pool spans instrumentation and texture.
_VARIATION_DESCRIPTORS: tuple[str, ...] = (
    "warm analog pads, slow evolving swells",
    "crisp arpeggios, tighter rhythmic groove",
    "deep sub-bass, sparse minimal percussion",
    "airy plucked strings, bright and open",
    "muted Rhodes chords, jazzy and mellow",
    "granular textures, glitchy micro-rhythms",
    "lush string ensemble, cinematic and wide",
    "dusty lo-fi tape hiss, boom-bap drums",
    "shimmering bells, high sparkling overtones",
    "driving four-on-the-floor kick, propulsive",
    "breathy woodwinds, gentle and organic",
    "detuned synth stabs, retro and gritty",
)

_LOOP_SUFFIX = (
    "Loopable, no distinct intro or outro, smooth ambient texture that cycles "
    "naturally. Driving beat but not overwhelming — background music for deep work."
)


def vibe_to_prompt(
    vibe: str | None,
    vibe_tags: str | None,
    style: str | None,
    hour: int,
    signals: list[str],
    variation: int | None = None,
) -> str:
    """Assemble a prompt; ``variation`` adds a descriptor, ``None`` is back-compat."""
    layers = [
        _layer_style_mood_feel(vibe, vibe_tags, style),
        _layer_time_of_day(hour),
        _layer_work_intensity(signals),
        _layer_variation(variation),
        _LOOP_SUFFIX,
    ]
    return ". ".join(layer for layer in layers if layer)


def _layer_style_mood_feel(
    vibe: str | None,
    vibe_tags: str | None,
    style: str | None,
) -> str:
    parts = [f"{style} music" if style else "ambient music"]
    if vibe:
        parts.append(f"{vibe} mood")
    if vibe_tags:
        tags = vibe_tags.replace("[", " ").replace("]", " ").split()
        if clean := ", ".join(tags):
            parts.append(f"{clean} feel")
    return ", ".join(parts)


def _layer_time_of_day(hour: int) -> str:
    for hours, phrase in _TIME_OF_DAY:
        if hour in hours:
            return phrase
    return _LATE_NIGHT


def _layer_work_intensity(signals: list[str]) -> str:
    counts = Counter(signals)
    if counts.get("git-commit", 0) >= _COMMIT_THRESHOLD:
        return _FLOW_STATE
    if counts.get("tests-fail", 0) >= _FAIL_THRESHOLD:
        return _GRINDING
    return _STEADY


def _layer_variation(variation: int | None) -> str:
    if variation is None:
        return ""
    return _VARIATION_DESCRIPTORS[variation % len(_VARIATION_DESCRIPTORS)]
