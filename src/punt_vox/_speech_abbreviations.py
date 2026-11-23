"""Whole-word expansions for spoken developer text."""

from __future__ import annotations

from typing import ClassVar, final


@final
class SpeechAbbreviations:
    """Look up programmer and conversational abbreviations."""

    _WORDS: ClassVar[dict[str, str]] = {
        # Programmer terms
        "stderr": "standard error",
        "stdout": "standard output",
        "stdin": "standard input",
        "eof": "E O F",
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
        "lmfao": "laughing my head off",
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
        "wtf": "what the heck",
    }

    @classmethod
    def expand(cls, word: str) -> str | None:
        """Return the whole-word expansion, if known."""
        return cls._WORDS.get(word.lower())
