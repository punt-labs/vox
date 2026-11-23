"""Text normalization for natural speech synthesis.

Converts programmer-facing strings (snake_case, camelCase, abbreviations)
to natural spoken English before passing text to TTS providers.  Applied
once per segment, before ``SynthesisRequest`` construction.
"""

from __future__ import annotations

import re
import unicodedata
from typing import final

from punt_vox._inline_code import InlineCode
from punt_vox._speech_abbreviations import SpeechAbbreviations
from punt_vox._speech_punctuation import SpeechPunctuation

# ---------------------------------------------------------------------------
# Pronounceable acronyms — TTS engines handle these as words
# ---------------------------------------------------------------------------

_PRONOUNCEABLE_ACRONYMS: frozenset[str] = frozenset(
    {
        # 2-letter English words — TTS pronounces these correctly
        "OK",
        "IT",
        "OR",
        "ON",
        "IN",
        "NO",
        "IF",
        "IS",
        "AT",
        "UP",
        "TO",
        "GO",
        "DO",
        "SO",
        "BE",
        "BY",
        "MY",
        "AM",
        "AN",
        "AS",
        "US",
        "HE",
        "ME",
        "WE",
        # 3-5 letter English words common in ALL_CAPS identifiers
        "MAX",
        "MIN",
        "ADD",
        "GET",
        "SET",
        "PUT",
        "RUN",
        "LOG",
        "MAP",
        "POP",
        "END",
        "ALL",
        "ANY",
        "HAS",
        "LEN",
        "KEY",
        "NEW",
        "OLD",
        "OUT",
        "ERR",
        "MSG",
        "ACK",
        "NET",
        "RAN",
        "RAW",
        "ROW",
        "TAB",
        "TAG",
        "USE",
        "BIT",
        "BUS",
        "CAN",
        "HIT",
        "MIX",
        "PIN",
        "TOP",
        "VIA",
        "WAR",
        "BUG",
        "FIX",
        "TRY",
        "FOR",
        "NOT",
        "VAL",
        "RED",
        "DIM",
        "HOME",
        "TIME",
        "TASK",
        "CALL",
        "FIND",
        "LOOP",
        "STEP",
        "CASE",
        "HELP",
        "HASH",
        "LOCK",
        "FREE",
        "FULL",
        "HALF",
        "LONG",
        "MAIN",
        "MAKE",
        "MARK",
        "MASK",
        "KEEP",
        "KILL",
        "FROM",
        "INTO",
        "HAVE",
        "BEEN",
        "ELSE",
        "JUST",
        "MUCH",
        "ONCE",
        "ONLY",
        "OVER",
        "SOME",
        "TAKE",
        "THAN",
        "THAT",
        "THEM",
        "THEN",
        "THIS",
        "VERY",
        "WHAT",
        "WHEN",
        "WILL",
        "WITH",
        "WORK",
        "ZERO",
        "BACK",
        "BASE",
        "BIND",
        "BODY",
        "BOTH",
        "CHAR",
        "DEEP",
        "EACH",
        "EDGE",
        "EVEN",
        "EXEC",
        "FLAT",
        "FORK",
        "GATE",
        "GOAL",
        "GONE",
        "GOOD",
        "GROW",
        "HARD",
        "HEAD",
        "HERE",
        "HIGH",
        "HOOK",
        "IDLE",
        "ITEM",
        "JOBS",
        "JUMP",
        "LAZY",
        "LEAF",
        "LEFT",
        "LESS",
        "LIKE",
        "LIVE",
        "MISS",
        "MORE",
        "MUST",
        "NEED",
        "NONE",
        "NORM",
        "PEEK",
        "PICK",
        "PLAY",
        "PREV",
        "PULL",
        "PUSH",
        "RATE",
        "REST",
        "RICH",
        "ROLL",
        "ROOT",
        "RULE",
        "SAFE",
        "SCAN",
        "SEED",
        "SELF",
        "SHOW",
        "SHUT",
        "SIGN",
        "SLIM",
        "SLOW",
        "SNAP",
        "SOFT",
        "SPAN",
        "SPEC",
        "SPIN",
        "SWAP",
        "TAIL",
        "TEMP",
        "TERM",
        "TICK",
        "TIER",
        "TINY",
        "TOOL",
        "TRIM",
        "TURN",
        "UNIT",
        "USED",
        "USER",
        "WALK",
        "WALL",
        "WANT",
        "WARM",
        "WEAK",
        "WIDE",
        "WILD",
        "WRAP",
        "RETRY",
        "COUNT",
        "VALUE",
        "DATA",
        "TYPE",
        "NAME",
        "FILE",
        "PATH",
        "SIZE",
        "MODE",
        "CODE",
        "OPEN",
        "READ",
        "SEND",
        "WAIT",
        "DONE",
        "FAIL",
        "PASS",
        "STOP",
        "TEST",
        "TRUE",
        "NULL",
        "VOID",
        "INIT",
        "EXIT",
        "LAST",
        "NEXT",
        "SORT",
        "CHECK",
        "CLOSE",
        "START",
        "STATE",
        "ERROR",
        "EVENT",
        "QUERY",
        "RESET",
        "WRITE",
        "TOKEN",
        "DEBUG",
        "LEVEL",
        "QUEUE",
        "INDEX",
        "INPUT",
        "LIMIT",
        "MATCH",
        "MERGE",
        "MOUNT",
        "PARSE",
        "PATCH",
        "PAUSE",
        "POINT",
        "POWER",
        "PRINT",
        "PROXY",
        "RANGE",
        "REPLY",
        "ROUND",
        "ROUTE",
        "SCENE",
        "SCOPE",
        "SCORE",
        "SETUP",
        "SHARE",
        "SHELL",
        "SHIFT",
        "SHORT",
        "SLEEP",
        "SPACE",
        "SPLIT",
        "STACK",
        "STAGE",
        "STORE",
        "STRIP",
        "SUPER",
        "TABLE",
        "THEME",
        "TIMER",
        "TITLE",
        "TOTAL",
        "TOUCH",
        "TRACE",
        "TRACK",
        "TRAIN",
        "UPPER",
        "VALID",
        "WATCH",
        "WHERE",
        "WHILE",
        "WORLD",
        "YIELD",
        "ABORT",
        "APPLY",
        "BATCH",
        "BLOCK",
        "BREAK",
        "BUILD",
        "CACHE",
        "CATCH",
        "CHAIN",
        "CHUNK",
        "CLAIM",
        "CLASS",
        "CLEAN",
        "CLEAR",
        "CLONE",
        "COLOR",
        "CONST",
        "COVER",
        "CRASH",
        "CYCLE",
        "DEFER",
        "DEPTH",
        "DRAFT",
        "DRAIN",
        "DRIVE",
        "EMPTY",
        "ENTER",
        "EXTRA",
        "FAULT",
        "FETCH",
        "FIELD",
        "FINAL",
        "FIRST",
        "FIXED",
        "FLAGS",
        "FLASH",
        "FLOAT",
        "FLUSH",
        "FOCUS",
        "FORCE",
        "FRAME",
        "FRESH",
        "FRONT",
        "GIVEN",
        "GRACE",
        "GRAPH",
        "GREEN",
        "GROUP",
        "GUARD",
        "GUESS",
        "HAPPY",
        "HEART",
        "HEAVY",
        "HUMAN",
        "IMAGE",
        "INNER",
        "LARGE",
        "LATER",
        "LAYER",
        "LEASE",
        "LIGHT",
        "LOCAL",
        "LOWER",
        "MAGIC",
        "MINOR",
        "MODEL",
        "MOVED",
        "MULTI",
        "MUTEX",
        "NEVER",
        "NEWER",
        "NOISE",
        "NOTED",
        "OFFER",
        "ORDER",
        "OTHER",
        "OUTER",
        "OWNER",
        "PANEL",
        "PHASE",
        "PLAIN",
        "PRIME",
        "PROTO",
        "QUICK",
        "QUIET",
        "QUOTA",
        "RAISE",
        "READY",
        "REALM",
        "REFER",
        "RIGHT",
        "SINCE",
        "SMALL",
        "SOLID",
        "SOUTH",
        "SPENT",
        "STILL",
        "STOCK",
        "STUFF",
        "THEIR",
        "THIRD",
        "THROW",
        "TODAY",
        "UNION",
        "UNTIL",
        "USING",
        "WHITE",
        "SKIP",
        "DROP",
        "COPY",
        "MOVE",
        "SAVE",
        "LOAD",
        "SYNC",
        "TEXT",
        "WORD",
        "LINE",
        "PAGE",
        "NODE",
        "TREE",
        "LIST",
        "POOL",
        "PIPE",
        "PORT",
        "HOST",
        "LINK",
        "INFO",
        "WARN",
        # Acronyms TTS engines pronounce correctly as words
        "JSON",
        "SQL",
        "NATS",
        "AWS",
        "YAML",
        "CUDA",
        "RAM",
        "ROM",
        "LAN",
        "WAN",
        "SIM",
        "GIF",
        "BASH",
        "RUST",
        "JAVA",
        "PERL",
        "RUBY",
    }
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Split camelCase and letter/digit boundaries.
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z])(?=[A-Z])"  # fooBar → foo Bar
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # HTMLParser → HTML Parser
    r"|(?<=[a-zA-Z])(?=[0-9])"  # foo2bar → foo 2bar
    r"|(?<=[0-9])(?=[a-zA-Z])"  # foo2bar → foo2 bar (combined: foo 2 bar)
)

# Tokens that look like file paths — leave these alone.
_FILE_PATH_RE = re.compile(r"^[~/.]?/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


VIBE_TAG_RE = re.compile(r"\[([a-z]+)\]")


def strip_vibe_tags(text: str) -> str:
    """Remove ElevenLabs-style expressive tags from *text*.

    Strips bracketed tokens **at any position** that contain a single
    lowercase-alpha word -- e.g. ``[serious]``, ``[warm]``, ``[sighs]``.

    Preserves brackets that don't match this narrow pattern:
    ``[Figure 1]`` (has space/uppercase), ``[1]`` (digits),
    ``[citation needed]`` (two words), ``[slow breath]`` (two words),
    ``[IMPORTANT]`` (all caps).

    Collapses any extra whitespace left behind after removal.

    .. note::

       ``resolve.strip_expressive_tags`` strips only *leading* tags and
       is used during vibe application (before synthesis).  This function
       strips tags at *any* position and is used by providers that do not
       support expressive tags at all (say, espeak) to sanitise the full
       text before passing it to a subprocess.
    """
    result = VIBE_TAG_RE.sub("", text)
    # Collapse runs of whitespace left behind by removal.
    result = re.sub(r"  +", " ", result).strip()
    # Guard: if stripping removed all content, return original text
    # unchanged so providers don't receive an empty string.
    if not result:
        return text
    return result


def normalize_for_speech(text: str) -> str:
    """Normalize programmer strings in *text* to natural spoken English.

    Transforms applied per token (whitespace-delimited):

    1. **Currency amounts** — spoken as money in prose and literally in code.
    2. **File paths** (start with ``/``, ``~/``, ``./``) — skipped.
    3. **snake_case** — underscores replaced with spaces, parts processed
       individually for abbreviation expansion.
    4. **camelCase / PascalCase** — split on case boundaries.
    5. **Known abbreviations** — expanded (case-insensitive whole-word).

    Idempotent: running twice produces the same result.
    """
    return SpeechText.normalize(text)


@final
class SpeechText:
    """Normalize raw speech text without losing inline code context."""

    @classmethod
    def normalize(cls, text: str) -> str:
        """Normalize text without interpreting expressive tags."""
        return cls._words(
            InlineCode.rewrite(text, SpeechPunctuation.spell_code_currency)
        )

    @classmethod
    def for_synthesis(cls, text: str, *, expressive: bool) -> str:
        """Normalize text, retaining expressive tags only for supported models."""
        prepared = InlineCode.rewrite(text, SpeechPunctuation.spell_code_currency)
        if not expressive:
            return cls._words(VIBE_TAG_RE.sub("", prepared))
        return " ".join(filter(None, cls._expressive_parts(prepared)))

    @classmethod
    def _expressive_parts(cls, text: str) -> list[str]:
        parts: list[str] = []
        cursor = 0
        for tag in VIBE_TAG_RE.finditer(text):
            parts.extend((cls._words(text[cursor : tag.start()]), tag[0]))
            cursor = tag.end()
        parts.append(cls._words(text[cursor:]))
        return parts

    @staticmethod
    def _words(text: str) -> str:
        return " ".join(filter(None, map(_normalize_token, text.split())))


def _normalize_token(token: str) -> str:
    """Normalize a single whitespace-delimited token."""
    core, suffix = SpeechPunctuation.split(token)

    # File paths: keep core as-is but still strip non-speech wrapping
    if not core or _FILE_PATH_RE.match(core):
        return core + suffix

    if "_" in core:
        return " ".join(map(_expand_snake_part, filter(None, core.split("_")))) + suffix

    parts = _CAMEL_BOUNDARY.split(core)
    if len(parts) > 1:
        return _expand_parts(parts) + suffix
    return (SpeechAbbreviations.expand(core) or _space_acronym(core)) + suffix


def _expand_snake_part(part: str) -> str:
    """Look up the whole snake_case part before splitting it further."""
    return SpeechAbbreviations.expand(part) or _expand_parts(
        _CAMEL_BOUNDARY.split(part)
    )


def _expand_parts(parts: list[str]) -> str:
    """Expand and rejoin identifier pieces."""
    return " ".join(map(_expand_part, parts))


def _expand_part(part: str) -> str:
    """Expand a single sub-token after identifier splitting."""
    expanded = SpeechAbbreviations.expand(part)
    if expanded:
        return expanded
    # ALL_CAPS: space out acronyms so TTS spells them letter-by-letter,
    # unless the word is pronounceable (real word or known acronym).
    if part.isupper():
        return _space_acronym(part)
    return part.lower() if part[0].isupper() else part


def _space_acronym(part: str) -> str:
    """Space out ALL_CAPS tokens so TTS engines spell them letter-by-letter.

    Returns the input unchanged if it contains digits, is in the
    pronounceable-acronyms allowlist, or has fewer than 2 or more than 5
    letters. Combining marks stay with their letters.
    """
    if not part.isupper():
        return part
    letters = "".join(ch for ch in part if not unicodedata.category(ch).startswith("M"))
    if letters in _PRONOUNCEABLE_ACRONYMS:
        return part
    if 2 <= len(letters) <= 5 and letters.isalpha() and part[0].isalpha():
        return re.sub(r"(?<=.)(?=\w)", " ", part)
    return part
