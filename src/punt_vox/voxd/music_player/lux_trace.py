r"""``LuxTrace`` -- the one ``[lux]``-prefixed logger for the music-player lux legs.

Every lifecycle line the lux legs emit carries a single greppable ``[lux]`` prefix,
so one ``grep '\[lux\]' vox.log`` replays the whole connect / subscribe / push /
inbound-event / disconnect / reconnect story of voxd's lux legs in order. This class
owns that prefix and the level convention -- INFO for a normal transition, WARNING
for a recoverable fault, ERROR for a refusal, ``exception`` for the unexpected -- so
no call site repeats the literal and the levels can never drift across the six leg
modules that share it.

The adapter wraps a module logger rather than subclassing one: each leg passes its
own ``getLogger(__name__)`` so the ``client.<role>.`` / daemon name prefix and the
per-module grep still work, while ``%s``-style args stay lazy (the prefix is prepended
to the format template, never to the interpolated result).

Traceback lines stay on the raw ``logger.exception`` at each call site (ruff BLE001
recognises the logger there, and only inside an ``except`` block is the active
exception available); those messages carry the same ``[lux]`` literal, so one grep
still spans the normal transitions here and the faults there.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, final

if TYPE_CHECKING:
    from collections.abc import MutableMapping

__all__ = ["LuxTrace"]

_PREFIX = "[lux] "


@final
class LuxTrace(logging.LoggerAdapter[logging.Logger]):
    """Emit one ``[lux]``-prefixed lifecycle line per lux-leg transition."""

    def process(
        self, msg: object, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        """Prefix the format template without interpolating its record arguments."""
        return f"{_PREFIX}{msg}", kwargs
