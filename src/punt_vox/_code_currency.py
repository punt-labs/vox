"""Spell currency signs in inline code literally instead of as money."""

from __future__ import annotations

import re
from typing import final

from punt_vox._currency_speech import CurrencySpeech

_CODE_PATH_RE = re.compile(r"^(?:\$[a-zA-Z_]\w*|[\w.~+-]*)\\?/")
_TOKEN_RE = re.compile(r"\S+")


@final
class CodeCurrency:
    """Say a code currency symbol literally rather than as a monetary unit."""

    @classmethod
    def spell(cls, text: str, *, in_path: bool) -> str:
        """Spell each code token's currency sign, leaving an attached path alone."""
        attached = _TOKEN_RE.match(text) if in_path else None
        start = attached.end() if attached else 0
        return text[:start] + _TOKEN_RE.sub(cls._spell_token, text[start:])

    @staticmethod
    def is_code_path(text: str) -> bool:
        """Recognize paths inside or directly adjacent to an inline span."""
        return _CODE_PATH_RE.match(text.lstrip("`'\"\u2018\u2019“”([{<")) is not None

    @classmethod
    def _spell_token(cls, match: re.Match[str]) -> str:
        token = match[0]
        return token if cls.is_code_path(token) else CurrencySpeech.speak_code(token)
