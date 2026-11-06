"""Mask secret-shaped substrings in a call agent's reply before it is spoken.

Threat model: the call agent reads the repository, and a spoken turn can ask
it to read back a local secret (``.env``, a credentials file, an API key
committed somewhere); unmasked, that secret is synthesized to speech in
whatever room the human is in. The secret deny-list
(:class:`~.secret_path_policy.SecretPathPolicy`) ends such a turn before its
reply is spoken; this is the last line, over whatever text does reach speech
-- a reasonable, documented pattern set, not a universal secret detector. The
caller (:meth:`~.reply_delivery.ReplyDelivery.deliver`) logs the original,
unredacted text to the 0600 ``vox.log`` separately; only the redacted text
ever reaches :class:`~.speak_fn.SpeakFn`.
"""

from __future__ import annotations

import re
from typing import Self, final

__all__ = ["reply_redactor"]

_REDACTED = "[redacted]"

# Provider API-key prefixes with a long, distinctively random suffix --
# OpenAI/Anthropic (sk-...), OpenAI project keys (sk-proj-...), GitHub
# tokens (ghp_/gho_/ghu_/ghs_/ghr_), Slack (xox[abpr]-...), AWS access keys
# (AKIA...), Google API keys (AIza...).
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
)

# A KEY=value / TOKEN=value shaped assignment -- an env-file line spoken
# back verbatim, whatever the variable's actual name. A bare PASSWORD or
# SECRET counts too; a bare key or token does not, as "the key: it returns"
# is ordinary speech.
_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)|PASSWORD|SECRET)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)

# The userinfo of a URL -- ``user:password@`` or a token used as the user.
_URL_USERINFO_PATTERN = re.compile(r"(?<=://)[^/\s@]+(?=@)")

# A long, unbroken run of high-entropy characters -- base64 or hex, unusual
# in ordinary spoken/written text at this length.
_HIGH_ENTROPY_PATTERN = re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}\b")


@final
class ReplyRedactor:
    """Mask every secret-shaped substring in a reply, in place of synthesis."""

    __slots__ = ()

    _PATTERNS: tuple[re.Pattern[str], ...] = (
        _URL_USERINFO_PATTERN,
        *_KEY_PATTERNS,
        _ASSIGNMENT_PATTERN,
        _HIGH_ENTROPY_PATTERN,
    )

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def __call__(self, text: str) -> str:
        """Return *text* with every secret-shaped substring replaced."""
        redacted = text
        for pattern in self._PATTERNS:
            redacted = pattern.sub(_REDACTED, redacted)
        return redacted


reply_redactor: ReplyRedactor = ReplyRedactor()
