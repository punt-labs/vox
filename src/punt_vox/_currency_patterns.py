"""Numeric and sign-position patterns for attached currency."""

from __future__ import annotations

import re
from typing import final

_SIGN = r"[-+\u2212]"
_WHOLE = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_DOTTED = r"\d{1,3}(?:\.\d{3})+"
_EURO_FRACTION = r",\d{2}(?!\d|[.,]\d)"
_EURO_DECIMAL = rf"(?:{_DOTTED}|{_WHOLE}){_EURO_FRACTION}"
_PREFIX_AMOUNT = (
    rf"(?P<{{p}}sign_before>{_SIGN})?"
    r"(?P<{p}symbol>{symbol})"
    rf"(?({{p}}sign_before)|(?P<{{p}}sign_after>{_SIGN})?)"
    r"(?P<{p}amount>{number})"
)


@final
class CurrencyPatterns:
    """Build money patterns without extending currency notation in code."""

    NUMBER = rf"{_WHOLE}(?:\.\d+)?|\.\d+"
    BARE_RANGE = (
        rf"(?:\u2013|-(?(comma_decimal)|(?(euro)(?={_EURO_DECIMAL})|(?!))))"
        rf"(?P<bare_end>(?(euro)(?P<bare_euro>{_EURO_DECIMAL}"
        rf"|(?(dotted){_DOTTED}(?!\d|[.,]\d)|(?!)))|(?!))|{NUMBER})"
    )

    @classmethod
    def prose_fields(cls, match: re.Match[str], prefix: str) -> tuple[str, str, str]:
        """Read the currency symbol, canonical number, and sign."""
        amount, decimal = match.group(prefix + "amount", prefix + "comma_decimal")
        sign = "".join(
            filter(None, match.group(prefix + "sign_before", prefix + "sign_after"))
        )
        return match[prefix + "symbol"], cls._canonical(amount, decimal), sign

    @classmethod
    def bare_end(cls, match: re.Match[str]) -> str:
        """Read the canonical number for a range end with no currency sign."""
        return cls._canonical(match["bare_end"] or "", match["bare_euro"])

    @staticmethod
    def _canonical(number: str, euro_number: str | None) -> str:
        if euro_number is None:
            return number
        return ".".join(part.replace(".", ",") for part in euro_number.rsplit(",", 1))

    @classmethod
    def code_amount(cls, symbols: str) -> str:
        """Match prefix amounts in code using the original number grammar."""
        return _PREFIX_AMOUNT.format(p="", symbol=f"[{symbols}]", number=cls.NUMBER)

    @classmethod
    def prose_amount(cls, symbols: str, prefix: str = "") -> str:
        """Allow a comma decimal only when the euro symbol was matched."""
        symbol = rf"(?P<{prefix}euro>€)|[{symbols.replace('€', '')}]"
        decimal = rf"(?:(?P<{prefix}dotted>{_DOTTED})|{_WHOLE}){_EURO_FRACTION}"
        comma = rf"(?({prefix}euro)(?P<{prefix}comma_decimal>{decimal})|(?!))"
        return _PREFIX_AMOUNT.format(
            p=prefix, symbol=symbol, number=rf"{comma}|{cls.NUMBER}"
        )

    @classmethod
    def suffix_amount(cls, symbols: str) -> str:
        """Match attached suffix amounts including comma decimals before €."""
        return (
            rf"(?P<leading>_*[^\w\s~/.,`{symbols}]*?)"
            rf"(?P<sign>{_SIGN}?)(?P<amount>{_EURO_DECIMAL}(?=€)|{cls.NUMBER})"
            rf"(?P<symbol>[{symbols}])"
        )
