"""Preserve amount semantics and prosody while stripping other symbols."""

from __future__ import annotations

import re
import unicodedata

from punt_vox._currency_speech import CurrencySpeech

_VARIATION_SELECTOR_RE = re.compile(r"[\uFE00-\uFE0F\U000E0100-\U000E01EF]")
_FILE_PATH_RE = re.compile(r"^[~/.]?/")
_PROSODY_PUNCTUATION = frozenset(".,?!:;\u2014\u2013")
_SYMBOL_NAME_RE = re.compile(
    r"(.*[-/,])?(_*[A-Z](?=#)|\w*[^\W\d_](?=\+\+))(#|\+\+)([\W_]*)"
)
_SPOKEN_MARKS = {"#": "sharp", "++": "plus plus"}
_NUMBER = r"[-\u2212]?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d[\d,]*(?:\.\d+)?|\.\d+)"
_NUMERIC_PERCENT_RE = re.compile(
    rf"\A{_NUMBER}(?:%?[-\u2013]{_NUMBER})?%(?:[^\w%]|_)*\Z"
)


class SpeechPunctuation:
    """Separate spoken semantics from punctuation in a speech token."""

    @classmethod
    def split(cls, token: str) -> tuple[str, str]:
        """Return spoken core and prosody suffix for a whitespace-delimited token.

        Leading punctuation is discarded except file path and identifier prefixes
        (``~/._``), a numeric minus, and signed or unsigned currency amounts.
        Trailing non-prosody symbols are discarded to avoid synthesis artifacts,
        except the ``#`` and ``++`` that name languages such as C# and C++, and
        a ``%`` after a number, which is spoken as "percent".
        """
        money = rf"{CurrencySpeech.prose_pattern(token).pattern}(?:[\W_]*$|—)"
        leading = rf"^(?:[-\u2212]{{2,}}|(?!(?:{money}|[-\u2212]\.?\d))[^\w~/.])+"
        core_candidate = re.sub(leading, "", token)
        if named := _SYMBOL_NAME_RE.fullmatch(core_candidate):
            joined, name, mark, raw_suffix = named.groups(default="")
            return f"{joined}{name} {_SPOKEN_MARKS[mark]}", cls._prosody(raw_suffix)
        core, raw_suffix = cls._split_trailing(cls._spell_percent(core_candidate))
        return CurrencySpeech.speak_prose(core), cls._prosody(raw_suffix)

    @staticmethod
    def is_path(core: str) -> bool:
        """Recognize path prefixes on a token without leading wrappers."""
        return _FILE_PATH_RE.match(core) is not None

    @staticmethod
    def _spell_percent(core_candidate: str) -> str:
        """Say a number or range's ``%`` as "percent" and its range dash as "to"."""
        return _NUMERIC_PERCENT_RE.sub(
            lambda amount: re.sub(
                r"(?<=[\d%])[-\u2013](?=[-\u2212]?[\d.])", " to ", amount[0]
            ).replace("%", " percent"),
            core_candidate,
        )

    @staticmethod
    def _prosody(raw_suffix: str) -> str:
        return "".join(filter(_PROSODY_PUNCTUATION.__contains__, raw_suffix))

    @staticmethod
    def _split_trailing(core_candidate: str) -> tuple[str, str]:
        """Split off trailing symbols, keeping attached marks that affect speech."""
        suffix_start = len(core_candidate)
        for index in range(suffix_start - 1, -1, -1):
            ch = core_candidate[index]
            if ch.isalnum():
                break
            category = unicodedata.category(ch)
            if category not in ("Mn", "Mc") or _VARIATION_SELECTOR_RE.match(ch):
                suffix_start = index
        return core_candidate[:suffix_start], core_candidate[suffix_start:]
