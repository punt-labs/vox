"""Text normalization for natural speech synthesis.

Converts programmer-facing strings (snake_case, camelCase, abbreviations)
to natural spoken English before passing text to TTS providers.  Applied
once per segment, before ``SynthesisRequest`` construction.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Abbreviation dictionary — whole-word expansions
# ---------------------------------------------------------------------------

_ABBREVIATIONS: dict[str, str] = {
    # Programmer terms
    "stderr": "standard error",
    "stdout": "standard output",
    "stdin": "standard input",
    "eof": "EOF",
    "env": "environment",
    "dir": "directory",
    "config": "configuration",
    "args": "arguments",
    "params": "parameters",
    "auth": "authentication",
    "repo": "repository",
    "src": "source",
    "tmp": "temporary",
    "deps": "dependencies",
    "impl": "implementation",
    "init": "initialize",
    "ctx": "context",
    # Internet slang
    "lol": "laughing out loud",
    "rofl": "rolling on the floor laughing",
    "lmao": "laughing my ass off",
    "lmfao": "laughing my fucking ass off",
    "smh": "shaking my head",
    "tbh": "to be honest",
    "imo": "in my opinion",
    "imho": "in my humble opinion",
    "fwiw": "for what it's worth",
    "afaik": "as far as I know",
    "brb": "be right back",
    "ttyl": "talk to you later",
    "ftw": "for the win",
    "omg": "oh my god",
    "wtf": "what the fuck",
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# camelCase / PascalCase boundary: insert space before an uppercase letter
# that follows a lowercase letter or digit, or before a run of uppercase
# letters followed by a lowercase letter (e.g. "HTMLParser" → "HTML Parser").
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z])(?=[A-Z])"  # fooBar → foo Bar
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # HTMLParser → HTML Parser
    r"|(?<=[a-zA-Z])(?=[0-9])"  # foo2bar → foo 2bar
    r"|(?<=[0-9])(?=[a-zA-Z])"  # foo2bar → foo2 bar (combined: foo 2 bar)
)

# A "programmer token" contains underscores or camelCase/digit boundaries.
# We process these; plain English words pass through unchanged.
_HAS_UNDERSCORE = re.compile(r"_")
_HAS_CAMEL = re.compile(r"[a-z][A-Z]|[A-Z]{2,}[a-z]|[a-zA-Z][0-9]|[0-9][a-zA-Z]")

# Tokens that look like file paths — leave these alone.
_FILE_PATH_RE = re.compile(r"^[~/.]?/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_for_speech(text: str) -> str:
    """Normalize programmer strings in *text* to natural spoken English.

    Transforms applied per token (whitespace-delimited):

    1. **File paths** (start with ``/``, ``~/``, ``./``) — skipped.
    2. **snake_case** — underscores replaced with spaces, parts processed
       individually for abbreviation expansion.
    3. **camelCase / PascalCase** — split on case boundaries.
    4. **Known abbreviations** — expanded (case-insensitive whole-word).

    Idempotent: running twice produces the same result.
    """
    words = text.split()
    result: list[str] = []
    for word in words:
        result.append(_normalize_token(word))
    return " ".join(result)


def _normalize_token(token: str) -> str:
    """Normalize a single whitespace-delimited token."""
    # Preserve punctuation wrapping the token (e.g. "(stderr)" → "(standard error)")
    prefix, core, suffix = _strip_punctuation(token)

    if not core:
        return token

    # Skip file paths
    if _FILE_PATH_RE.match(core):
        return token

    # snake_case: split on underscores, process each part
    if _HAS_UNDERSCORE.search(core):
        parts = core.split("_")
        expanded = " ".join(_expand_part(p) for p in parts if p)
        return prefix + expanded + suffix

    # camelCase / PascalCase: split on case boundaries
    if _HAS_CAMEL.search(core):
        split = _CAMEL_BOUNDARY.sub(" ", core)
        parts = split.split()
        expanded = " ".join(_expand_part(p) for p in parts)
        return prefix + expanded + suffix

    # Standalone abbreviation
    expanded = _expand_abbreviation(core)
    if expanded != core:
        return prefix + expanded + suffix

    # Return reconstructed token (punctuation may have been stripped)
    return prefix + core + suffix


def _expand_part(part: str) -> str:
    """Expand a single sub-token (after snake/camel splitting)."""
    expanded = _expand_abbreviation(part)
    if expanded != part:
        return expanded
    # ALL_CAPS stays uppercase (TTS spells acronyms)
    if part.isupper() and len(part) > 1:
        return part
    return part.lower() if part[0].isupper() and not part.isupper() else part


def _expand_abbreviation(word: str) -> str:
    """Look up a word in the abbreviation dictionary (case-insensitive)."""
    return _ABBREVIATIONS.get(word.lower(), word)


def _strip_punctuation(token: str) -> tuple[str, str, str]:
    """Strip leading/trailing punctuation, returning (prefix, core, suffix).

    Leading ``~/._`` are kept in core (file path / identifier prefixes).
    Trailing punctuation (commas, parens, etc.) is preserved in *suffix*.
    Trailing underscores are discarded — they're separators, not speech.
    """
    start = 0
    end = len(token)
    while start < end and not token[start].isalnum() and token[start] not in "~/._":
        start += 1
    # Peel off trailing punctuation (not underscore, not alnum) → suffix
    suffix_start = end
    while suffix_start > start:
        ch = token[suffix_start - 1]
        if ch.isalnum() or ch == "_":
            break
        suffix_start -= 1
    # Discard trailing underscores (separators, not speech)
    core_end = suffix_start
    while core_end > start and token[core_end - 1] == "_":
        core_end -= 1
    return token[:start], token[start:core_end], token[suffix_start:]
