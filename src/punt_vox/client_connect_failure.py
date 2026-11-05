"""One failed connect to voxd, turned into the error a caller sees.

The connect URI carries the daemon's token in its query string, and some of
websockets' errors carry the URI: ``InvalidURI`` renders as
``"<uri> isn't a valid URI: ..."``. That text reaches ``vox daemon status``,
start and restart reports, install verification and MCP replies, so a
:class:`ConnectFailure` never renders a cause that can hold the URI, and
redacts any ``token=`` value from the finished message as a backstop.

It also decides the error's type. A refusal on every address -- nothing
listens -- is :class:`~punt_vox.client_errors.VoxdRefusedError`, the one
failure that means "not running". Anything else, from a timeout to a
listener that rejected the handshake, is a plain
:class:`~punt_vox.client_errors.VoxdConnectionError`: something may be there.
"""

from __future__ import annotations

import re
from typing import ClassVar, Self, cast, final

from websockets.exceptions import InvalidURI

from punt_vox.client_errors import VoxdConnectionError, VoxdRefusedError

__all__ = ["ConnectFailure"]


@final
class ConnectFailure:
    """A connect to *endpoint* that raised; render it without the token."""

    __slots__ = ("_causes", "_endpoint")

    # Any ``token=`` query value, wherever a cause put it.
    _TOKEN: ClassVar[re.Pattern[str]] = re.compile(r"token=[^&\s#'\"]+")

    _endpoint: str
    _causes: tuple[Exception, ...]

    def __new__(cls, endpoint: str, exc: Exception) -> Self:
        """*endpoint* is the URI without its query; *exc* is what connect raised.

        A host with several addresses (``localhost``: ``::1`` and
        ``127.0.0.1``) fails as an ``ExceptionGroup`` of one error per address.
        """
        self = super().__new__(cls)
        self._endpoint = endpoint
        if isinstance(exc, ExceptionGroup):
            # isinstance narrows to ExceptionGroup[Unknown]; an
            # ExceptionGroup's members are Exceptions by construction.
            self._causes = cast("ExceptionGroup[Exception]", exc).exceptions
        else:
            self._causes = (exc,)
        return self

    def error(self) -> VoxdConnectionError:
        """Return the typed error: a refusal only when every address refused."""
        detail = "; ".join(self._describe(cause) for cause in self._causes)
        msg = self._TOKEN.sub(
            "token=[REDACTED]", f"Cannot connect to voxd at {self._endpoint}: {detail}"
        )
        if all(isinstance(cause, ConnectionRefusedError) for cause in self._causes):
            return VoxdRefusedError(msg)
        return VoxdConnectionError(msg)

    @staticmethod
    def _describe(cause: Exception) -> str:
        """Return *cause* as text that names no URI.

        ``InvalidURI`` is the one cause whose text embeds the URI; its reason
        (``cause.msg``) is kept, the URI is not.
        """
        if isinstance(cause, InvalidURI):
            return f"invalid voxd URI ({cause.msg})"
        return str(cause)
