"""Numeric and sign-position patterns for attached currency."""

from __future__ import annotations

import re
from typing import final

_SIGN = r"[-+\u2212]"
_WHOLE = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
_EURO_DECIMAL = rf"{_WHOLE},\d{{2}}(?!\d|[.,]\d)"
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

    @staticmethod
    def prose_fields(match: re.Match[str], prefix: str) -> tuple[str, str, str]:
        """Read the currency symbol, canonical number, and sign."""
        number = match[prefix + "amount"]
        if match[prefix + "comma_decimal"]:
            number = ".".join(number.rsplit(",", 1))
        sign = match[prefix + "sign_before"] or match[prefix + "sign_after"] or ""
        return match[prefix + "symbol"], number, sign

    @classmethod
    def code_amount(cls, symbols: str) -> str:
        """Match prefix amounts in code using the original number grammar."""
        return _PREFIX_AMOUNT.format(p="", symbol=f"[{symbols}]", number=cls.NUMBER)

    @classmethod
    def prose_amount(cls, symbols: str, prefix: str = "") -> str:
        """Allow a comma decimal only when the euro symbol was matched."""
        symbol = rf"(?P<{prefix}euro>€)|[{symbols.replace('€', '')}]"
        comma = rf"(?({prefix}euro)(?P<{prefix}comma_decimal>{_EURO_DECIMAL})|(?!))"
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
