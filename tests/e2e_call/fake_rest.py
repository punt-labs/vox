"""A loopback stand-in for the three ElevenLabs REST calls a call needs."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["FakeConvAIRest"]


@final
class FakeConvAIRest:
    """Answer tool and agent creation and sign conversations with one URL.

    Each request is recorded as ``(method, path)`` in ``requests`` so a test
    can prove the daemon provisioned before it dialled.
    """

    __slots__ = ("_requests", "_server", "_signed_url", "_thread")
    _signed_url: str
    _requests: list[tuple[str, str]]
    _server: ThreadingHTTPServer
    _thread: Thread

    def __new__(cls, signed_url: str) -> Self:
        """Serve *signed_url* as the answer to every get-signed-url request."""
        self = super().__new__(cls)
        self._signed_url = signed_url
        self._requests = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        return self

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def requests(self) -> list[tuple[str, str]]:
        return self._requests

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

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        rest = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args  # Keep the test output quiet.

            def do_POST(self) -> None:
                rest._requests.append(("POST", self.path))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                match self.path:
                    case "/v1/convai/tools":
                        self._reply({"id": f"tool_{len(rest._requests)}"})
                    case "/v1/convai/agents/create":
                        self._reply({"agent_id": "agent_fake"})
                    case _:
                        self._reply({"detail": "not found"}, status=404)

            def do_GET(self) -> None:
                rest._requests.append(("GET", self.path))
                if self.path.startswith("/v1/convai/conversation/get-signed-url"):
                    self._reply({"signed_url": rest._signed_url})
                else:
                    self._reply({"detail": "not found"}, status=404)

            def do_DELETE(self) -> None:
                rest._requests.append(("DELETE", self.path))
                self._reply({})

            def _reply(self, body: dict[str, object], *, status: int = 200) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler
