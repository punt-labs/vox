"""Voice metadata: blurbs, excuses, and display helpers."""

from __future__ import annotations

import random

from punt_vox.types import VoiceNotFoundError

VOICE_BLURBS: dict[tuple[str, str], str] = {
    # ElevenLabs (popular English voices)
    ("elevenlabs", "matilda"): (
        "Warm and thoughtful — sounds like she read the docs before answering"
    ),
    ("elevenlabs", "aria"): (
        "Bright and clear — could narrate your life and you'd keep listening"
    ),
    ("elevenlabs", "roger"): (
        "Steady and reassuring — the voice you want explaining turbulence"
    ),
    ("elevenlabs", "charlie"): ("Relaxed and genuine — telling you this over coffee"),
    ("elevenlabs", "drew"): (
        "Eloquent and calm — thinks before speaking, every word lands"
    ),
    ("elevenlabs", "sarah"): (
        "Poised and articulate — boardroom-ready but never stiff"
    ),
    ("elevenlabs", "laura"): (
        "Expressive and warm — brings stories to life without trying"
    ),
    ("elevenlabs", "george"): (
        "Rich and composed — could read a phone book and make it interesting"
    ),
    ("elevenlabs", "jessica"): (
        "Friendly and upbeat — makes everything sound like good news"
    ),
    ("elevenlabs", "river"): (
        "Calm and unhurried — late-night radio host who never rushes"
    ),
    ("elevenlabs", "lily"): ("Gentle and precise — the quiet expert in the room"),
    ("elevenlabs", "callum"): (
        "Crisp and energetic — always slightly ahead of schedule"
    ),
    # OpenAI (all 9)
    ("openai", "nova"): ("Balanced and versatile — the reliable all-rounder"),
    ("openai", "alloy"): ("Neutral and clear — gets out of the way of the words"),
    ("openai", "echo"): ("Deep and measured — gravitas without the drama"),
    ("openai", "fable"): ("Warm and expressive — born to tell stories"),
    ("openai", "onyx"): ("Low and authoritative — the voice of serious announcements"),
    ("openai", "shimmer"): ("Bright and airy — light on its feet"),
    ("openai", "coral"): ("Smooth and natural — easy to listen to for hours"),
    ("openai", "sage"): ("Thoughtful and steady — wisdom in every syllable"),
    ("openai", "ash"): ("Grounded and direct — no frills, all substance"),
    # Polly (popular English voices)
    ("polly", "joanna"): ("Clear and professional — the classic narrator"),
    ("polly", "matthew"): ("Warm and conversational — a newscast you'd actually watch"),
    ("polly", "ruth"): ("Confident and modern — neural voice with presence"),
    ("polly", "amy"): ("British and crisp — the voice of polished documentation"),
}

_VOICE_EXCUSES = [
    "is temporarily indisposed",
    "is grabbing something to eat",
    "stepped out for a coffee",
    "is in the bathroom",
]


def voice_not_found_message(exc: VoiceNotFoundError) -> str:
    """Build a friendly, brand-appropriate message for an unknown voice."""
    excuse = random.choice(_VOICE_EXCUSES)
    suggestions = random.sample(exc.available, min(3, len(exc.available)))
    return f"{exc.voice_name} {excuse}. How about {', '.join(suggestions)}?"
