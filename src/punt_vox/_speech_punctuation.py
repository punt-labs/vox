"""Preserve amount semantics and prosody while stripping other symbols."""

from __future__ import annotations

import re
import unicodedata

from punt_vox._currency_speech import CurrencySpeech

_VARIATION_SELECTOR_RE = re.compile(r"[\uFE00-\uFE0F\U000E0100-\U000E01EF]")
_FILE_PATH_RE = re.compile(r"^[~/.]?/")
_CODE_PATH_RE = re.compile(r"^(?:\$[a-zA-Z_]\w*|[\w.~+-]*)\\?/")
_TOKEN_RE = re.compile(r"\S+")
_PROSODY_PUNCTUATION = frozenset(".,?!:;\u2014\u2013")
_SYMBOL_NAME_RE = re.compile(r"(_*[A-Z](?=#)|\w*[^\W\d_](?=\+\+))(#|\+\+)([\W_]*)")
_SPOKEN_MARKS = {"#": "sharp", "++": "plus plus"}


class SpeechPunctuation:
    """Separate spoken semantics from punctuation in a speech token."""

    @classmethod
    def split(cls, token: str) -> tuple[str, str]:
        """Return spoken core and prosody suffix for a whitespace-delimited token.

        Leading punctuation is discarded except file path and identifier prefixes
        (``~/._``), a numeric minus, and signed or unsigned currency amounts.
        Trailing non-prosody symbols are discarded to avoid synthesis artifacts,
        except the ``#`` and ``++`` that name languages such as C# and C++.
        """
        currency = CurrencySpeech.prose_pattern(token).pattern
        leading = (
            rf"^(?:[-\u2212]{{2,}}|(?!(?:{currency}[\W_]*$|[-\u2212]\.?\d))[^\w~/.])+"
        )
        core_candidate = re.sub(leading, "", token)
        if named := _SYMBOL_NAME_RE.fullmatch(core_candidate):
            name, mark, raw_suffix = named.groups()
            return f"{name} {_SPOKEN_MARKS[mark]}", cls._prosody(raw_suffix)
        core, raw_suffix = cls._split_trailing(core_candidate)
        return CurrencySpeech.speak_prose(core), cls._prosody(raw_suffix)

    @classmethod
    def spell_code_currency(cls, text: str, *, in_path: bool) -> str:
        """Say a code currency symbol literally rather than as a monetary unit."""
        attached = _TOKEN_RE.match(text) if in_path else None
        start = attached.end() if attached else 0
        return text[:start] + _TOKEN_RE.sub(cls._spell_code_token, text[start:])

    @staticmethod
    def is_code_path(text: str) -> bool:
        """Recognize paths inside or directly adjacent to an inline span."""
        return _CODE_PATH_RE.match(text.lstrip("`'\"\u2018\u2019“”([{<")) is not None

    @staticmethod
    def is_path(core: str) -> bool:
        """Recognize path prefixes on a token without leading wrappers."""
        return _FILE_PATH_RE.match(core) is not None

    @classmethod
    def _spell_code_token(cls, match: re.Match[str]) -> str:
        token = match[0]
        return token if cls.is_code_path(token) else CurrencySpeech.speak_code(token)

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
