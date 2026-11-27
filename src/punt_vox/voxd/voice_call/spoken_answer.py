"""Classify what the person said as a yes, a no, or neither, by closed word lists."""

from __future__ import annotations

import re
from typing import Final, Literal, final

__all__ = ["SpokenAnswer"]

type Answer = Literal["yes", "no", "other"]

_YES: Final = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "ok",
        "okay",
        "go ahead",
        "do it",
        "confirm",
        "approve",
        "affirmative",
        "correct",
        "yes please",
        "please do",
        "that is right",
    }
)
_NO: Final = frozenset(
    {
        "no",
        "nope",
        "cancel",
        "stop",
        "don't",
        "do not",
        "never mind",
        "nevermind",
        "deny",
        "negative",
        "no thanks",
        "no thank you",
    }
)
_NOT_WORD: Final = re.compile(r"[^\w']+")


@final
class SpokenAnswer:
    """Free text never confirms an action: only a whole utterance from a closed list."""

    __slots__ = ()

    @staticmethod
    def normalize(text: str) -> str:
        """Return lowercase words joined by single spaces, punctuation removed."""
        return " ".join(_NOT_WORD.sub(" ", text.casefold()).split())

    @staticmethod
    def of(text: str) -> Answer:
        """Return ``yes`` or ``no`` for a whole utterance on a list, else ``other``."""
        words = SpokenAnswer.normalize(text)
        if words in _YES:
            return "yes"
        if words in _NO:
            return "no"
        return "other"
