"""A loopback stand-in for the three ElevenLabs REST calls a call needs."""

from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, Self, cast, final
from urllib.parse import parse_qs, urlsplit

from punt_vox.voxd.voice_agent.call_overrides import PLACEHOLDER_KEY

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["FakeConvAIRest"]

_TOOLS = "/v1/convai/tools"
_AGENTS = "/v1/convai/agents"
_SIGNED_URL = "/v1/convai/conversation/get-signed-url"


@final
class _Server(ThreadingHTTPServer):
    """Keep a handler's exception for the test instead of printing it."""

    fake: FakeConvAIRest  # Bound by the fake right after construction.

    def handle_error(self, request: object, client_address: object) -> None:
        del request, client_address
        self.fake.record_error(f"handler failed: {traceback.format_exc().strip()}")


class _Handler(BaseHTTPRequestHandler):
    """Answer the fake's routes on behalf of the owning :class:`FakeConvAIRest`."""

    @property
    def _fake(self) -> FakeConvAIRest:
        return cast("_Server", self.server).fake

    def do_POST(self) -> None:
        self._seen()
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self._authorized():
            return
        match self.path:
            case "/v1/convai/tools":
                self._reply({"id": self._fake.issue("tool")})
            case "/v1/convai/agents/create":
                self._reply({"agent_id": self._fake.issue("agent")})
            case _:
                self._unknown_path()

    def do_GET(self) -> None:
        self._seen()
        if not self._authorized():
            return
        url = urlsplit(self.path)
        if url.path != _SIGNED_URL:
            self._unknown_path()
            return
        agent_id = parse_qs(url.query).get("agent_id", [""])[0]
        if self._fake.issued(f"{_AGENTS}/{agent_id}"):
            self._reply({"signed_url": self._fake.signed_url})
        else:
            self._error("unknown agent_id")
            self._reply({"detail": "not found"}, status=404)

    def do_DELETE(self) -> None:
        self._seen()
        if not self._authorized():
            return
        if self._fake.issued(urlsplit(self.path).path):
            self._reply({})
        else:
            self._error("unknown id")
            self._reply({"detail": "not found"}, status=404)

    def _seen(self) -> None:
        self._fake.requests.append((self.command, self.path))

    def _authorized(self) -> bool:
        if self.headers.get("xi-api-key") == PLACEHOLDER_KEY:
            return True
        self._error("key is not the override placeholder")
        self._reply({"detail": "not the override placeholder key"}, status=401)
        return False

    def _unknown_path(self) -> None:
        self._error("unknown path")
        self._reply({"detail": "not found"}, status=404)

    def _error(self, reason: str) -> None:
        self._fake.record_error(f"{self.command} {self.path}: {reason}")

    def _reply(self, body: dict[str, object], *, status: int = 200) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@final
class FakeConvAIRest:
    """Answer tool and agent creation and sign conversations with one URL.

    Each request is recorded as ``(method, path)`` in ``requests`` so a test
    can prove the daemon provisioned before it dialled. Anything the daemon
    should never do -- a key other than the override placeholder, a path the
    fake does not serve, a signed-URL request or a DELETE for an id the fake
    never issued, a request that makes the handler raise -- is recorded in
    ``errors``, which the fixture asserts is empty. A query string (the real
    client deletes with ``?force=true``) never changes which id a request
    names.
    """

    __slots__ = (
        "_errors",
        "_issued",
        "_requests",
        "_server",
        "_signed_url",
        "_thread",
    )
    _signed_url: str
    _requests: list[tuple[str, str]]
    _errors: list[str]
    _issued: list[str]
    _server: _Server
    _thread: Thread

    def __new__(cls, signed_url: str) -> Self:
        """Serve *signed_url* as the answer to every get-signed-url request."""
        self = super().__new__(cls)
        self._signed_url = signed_url
        self._requests = []
        self._errors = []
        self._issued = []
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        return self

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def signed_url(self) -> str:
        return self._signed_url

    @property
    def requests(self) -> list[tuple[str, str]]:
        return self._requests

    @property
    def errors(self) -> list[str]:
        return self._errors

    def record_error(self, text: str) -> None:
        """Keep one thing the daemon did that the real API would not accept."""
        self._errors.append(text)

    def issue(self, kind: str) -> str:
        """Hand out the next ``tool_N`` or ``agent_N`` id and remember it."""
        resource_id = f"{kind}_{len(self._issued) + 1}"
        self._issued.append(resource_id)
        return resource_id

    def issued(self, path: str) -> bool:
        """Return whether *path* (no query) names an id this fake handed out."""
        prefix, _, resource_id = path.rpartition("/")
        return prefix in {_TOOLS, _AGENTS} and resource_id in self._issued

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,  # Context-manager protocol shape.
        exc: BaseException | None,  # Context-manager protocol shape.
        tb: TracebackType | None,  # Context-manager protocol shape.
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            msg = "fake REST server thread did not stop within 5 s"
            raise RuntimeError(msg)
