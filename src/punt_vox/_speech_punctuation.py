"""Preserve amount semantics and prosody while stripping other symbols."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

_CURRENCY_UNITS = {
    "$": ("dollar", "dollars"),
    "€": ("euro", "euros"),
    "£": ("pound", "pounds"),
    "¥": ("yen", "yen"),
    "₹": ("rupee", "rupees"),
    "₩": ("won", "won"),
    "₽": ("ruble", "rubles"),
}
_SIGN_WORDS = {"-": "minus ", "\u2212": "minus ", "+": "plus ", "": ""}
_CURRENCY_AMOUNT = re.compile(
    rf"(?P<sign_before>[-+\u2212])?(?P<symbol>[{''.join(_CURRENCY_UNITS)}])"
    r"(?(sign_before)|(?P<sign_after>[-+\u2212])?)"
    r"(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
)
_LEADING_NON_SPEECH_RE = re.compile(
    rf"^(?:[-\u2212]{{2,}}|(?!(?:{_CURRENCY_AMOUNT.pattern}[\W_]*$|[-\u2212]\.?\d))"
    r"[^\w~/.])+"
)
_VARIATION_SELECTOR_RE = re.compile(r"[\uFE00-\uFE0F\U000E0100-\U000E01EF]")
_FILE_PATH_RE = re.compile(r"^[~/.]?/")
_CODE_PATH_RE = re.compile(r"^(?:/|[\w.~+-]+/)")
_TOKEN_RE = re.compile(r"\S+")
_PROSODY_PUNCTUATION = frozenset(".,?!:;\u2014\u2013")


class SpeechPunctuation:
    """Separate spoken semantics from punctuation in a speech token."""

    @classmethod
    def split(cls, token: str) -> tuple[str, str]:
        """Return spoken core and prosody suffix for a whitespace-delimited token.

        Leading punctuation is discarded except file path and identifier prefixes
        (``~/._``), a numeric minus, and signed or unsigned currency amounts.
        Trailing non-prosody symbols are discarded to avoid synthesis artifacts.
        """
        core, raw_suffix = cls._split_trailing(_LEADING_NON_SPEECH_RE.sub("", token))
        suffix = "".join(filter(_PROSODY_PUNCTUATION.__contains__, raw_suffix))
        return cls._spell_currency(core), suffix

    @classmethod
    def spell_code_currency(cls, text: str, *, in_path: bool) -> str:
        """Say a code currency symbol literally rather than as a monetary unit."""
        return _TOKEN_RE.sub(
            lambda token: cls._spell_code_token(token, in_path=in_path), text
        )

    @staticmethod
    def is_code_path(text: str) -> bool:
        """Recognize paths inside or directly adjacent to an inline span."""
        return _CODE_PATH_RE.match(_LEADING_NON_SPEECH_RE.sub("", text)) is not None

    @staticmethod
    def is_path(core: str) -> bool:
        """Recognize path prefixes on a token without leading wrappers."""
        return _FILE_PATH_RE.match(core) is not None

    @classmethod
    def _spell_code_token(cls, match: re.Match[str], *, in_path: bool) -> str:
        token = match[0]
        if (in_path and match.start() == 0) or cls.is_code_path(token):
            return token
        return _CURRENCY_AMOUNT.sub(cls._spell_code_symbol, token)

    @staticmethod
    def _spell_code_symbol(match: re.Match[str]) -> str:
        symbol, amount = match.group("symbol", "amount")
        before, after = match.group("sign_before", "sign_after")
        unit, _ = _CURRENCY_UNITS[symbol]
        left = " " if match.string[match.start() - 1 : match.start()].isalnum() else ""
        right = " " if match.string[match.end() : match.end() + 1].isalnum() else ""
        return (
            f"{left}{_SIGN_WORDS[before or '']}{unit} sign "
            f"{_SIGN_WORDS[after or '']}{amount}{right}"
        )

    @staticmethod
    def _spell_currency(core: str) -> str:
        match = _CURRENCY_AMOUNT.fullmatch(core.lstrip("_"))
        if match is None:
            return core
        symbol, amount = match.group("symbol", "amount")
        sign = "".join(filter(None, match.group("sign_before", "sign_after")))
        singular, plural = _CURRENCY_UNITS[symbol]
        is_one = "." not in amount and Decimal(amount.replace(",", "")) == 1
        unit = singular if is_one else plural
        number = "0" + amount if amount.startswith(".") else amount
        return f"{_SIGN_WORDS[sign]}{number} {unit}"

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
