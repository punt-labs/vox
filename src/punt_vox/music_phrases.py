"""Server-authored DJ-booth phrasing for the ``♪`` music panel line.

The music MCP tools hold the real action plus its ``style``/``name``, so they
are the correct author of the flavored panel line -- the ``suppress-output``
hook is a display surface, not a content generator.  Each pool is an immutable
tuple keyed by action bucket; :class:`MusicMarquee` selects one per call and
fills in ``{style}``/``{name}``.  The line carries no ``♪`` prefix -- the tool
adds it once at emit -- and stays short so the prefixed panel line is ≤ 80 cols.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Self, final

GENERATING_WITH_STYLE: tuple[str, ...] = (
    "dropping a {style} beat",
    "{style} in the booth",
    "cueing up {style}",
    "{style} on the decks",
    "spinning up some {style}",
    "{style} — beat incoming",
)

GENERATING_NO_STYLE: tuple[str, ...] = (
    "beat incoming",
    "stepping up to the decks",
    "warming up the decks",
    "cueing the first track",
)

STOPPED: tuple[str, ...] = (
    "fading out",
    "that's a wrap",
    "decks off",
    "last call",
    "killing the lights",
)

REPLAY_WITH_NAME: tuple[str, ...] = (
    "now spinning: {name}",
    "{name} on the decks",
    "{name} on repeat",
    "pulling {name} from the crate",
    "{name} — encore",
)

REPLAY_RADIO: tuple[str, ...] = (
    "back to the crate",
    "shuffling the crate",
    "radio mode — full crate",
)

SKIP: tuple[str, ...] = (
    "mixing the next one in",
    "next track loading",
    "cueing the next",
    "on to the next",
)


@final
class MusicMarquee:
    """Author a randomized DJ-booth line for one music-panel action.

    The chooser is injected so tests are deterministic -- production passes
    :func:`random.choice`; a test passes a fixed picker and asserts the exact
    line, never a live-RNG outcome.
    """

    _choose: Callable[[Sequence[str]], str]

    def __new__(cls, chooser: Callable[[Sequence[str]], str] = random.choice) -> Self:
        self = super().__new__(cls)
        self._choose = chooser
        return self

    def generating(self, style: str | None) -> str:
        """Return a music-on line, filling in ``{style}`` when one is given."""
        if style:
            return self._choose(GENERATING_WITH_STYLE).format(style=style)
        return self._choose(GENERATING_NO_STYLE)

    def stopped(self) -> str:
        """Return a music-off line."""
        return self._choose(STOPPED)

    def replay(self, name: str | None) -> str:
        """Return a replay line: named when ``name`` is given, else radio."""
        if name:
            return self._choose(REPLAY_WITH_NAME).format(name=name)
        return self._choose(REPLAY_RADIO)

    def skip(self) -> str:
        """Return a skip/next line."""
        return self._choose(SKIP)
