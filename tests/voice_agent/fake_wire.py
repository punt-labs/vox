"""A loopback ElevenLabs control endpoint that records what voxd asks of it."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Final, Self
from urllib.parse import urlsplit

from punt_vox.voxd.voice_agent.call_overrides import PLACEHOLDER_KEY

if TYPE_CHECKING:
    from collections.abc import Generator

API_KEY: Final = "localhost-test-key"
SIGNED_URL: Final = "ws://127.0.0.1:42609/convai?conversation_signature=not-for-logs"
_TOOLS: Final = "/v1/convai/tools/"
_AGENTS: Final = "/v1/convai/agents/"


class Requests:
    """Record requests and let tests change the server response between calls.

    ``calls`` holds every request except the reads that check a resource's
    marker before a delete; those are in ``verifications``. The remote account
    is ``tools`` (id to description) and ``agents`` (id to name); a delete
    removes from it only when ``delete_forgets`` is set, because most tests
    reuse one id for every generation.
    """

    calls: list[tuple[str, str, object]]
    verifications: list[str]
    tools: dict[str, str]
    agents: dict[str, str]
    next_tool_ids: list[str]
    create_tool_id: str
    disconnect_on_tool_creation: bool
    create_agent_status: int
    signed_url_status: int
    delete_tool_status: int
    delete_agent_status: int
    delete_forgets: bool
    url: str

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.calls = []
        self.verifications = []
        self.tools = {}
        self.agents = {}
        self.next_tool_ids = []
        self.create_tool_id = "tool_clock"
        self.disconnect_on_tool_creation = False
        self.create_agent_status = self.signed_url_status = 200
        self.delete_tool_status = self.delete_agent_status = 404
        self.delete_forgets = False
        self.url = ""
        return self

    def deletes(self) -> list[str]:
        """Return the paths of the DELETE requests, in order."""
        return [path for method, path, _ in self.calls if method == "DELETE"]


@contextmanager
def serve() -> Generator[Requests]:
    """Serve a fresh endpoint on loopback until the block exits."""
    requests = Requests()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            self._observe(body)
            if self.path == "/v1/convai/tools":
                if requests.disconnect_on_tool_creation:
                    self.close_connection = True
                    return
                tool_id = (
                    requests.next_tool_ids.pop(0)
                    if requests.next_tool_ids
                    else requests.create_tool_id
                )
                requests.tools[tool_id] = body["tool_config"]["description"]
                self._reply(200, {"id": tool_id})
            elif self.path == "/v1/convai/agents/create":
                if requests.create_agent_status == 200:
                    requests.agents["agent_vox"] = body["name"]
                self._reply(requests.create_agent_status, {"agent_id": "agent_vox"})
            else:
                self._reply(500, {})

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path.startswith(_TOOLS):
                self._describe(path.removeprefix(_TOOLS), requests.tools, "tool")
            elif path.startswith(_AGENTS):
                self._describe(path.removeprefix(_AGENTS), requests.agents, "agent")
            elif (
                self.path == "/v1/convai/conversation/get-signed-url?agent_id=agent_vox"
            ):
                self._observe(None)
                self._reply(requests.signed_url_status, {"signed_url": SIGNED_URL})
            else:
                self._observe(None)
                self._reply(500, {})

        def do_DELETE(self) -> None:
            self._observe(None)
            path = urlsplit(self.path).path
            if path.startswith(_AGENTS):
                self._delete(
                    path.removeprefix(_AGENTS),
                    requests.agents,
                    requests.delete_agent_status,
                )
            elif path.startswith(_TOOLS):
                self._delete(
                    path.removeprefix(_TOOLS),
                    requests.tools,
                    requests.delete_tool_status,
                )
            else:
                self._reply(500, {})

        def _delete(self, resource_id: str, held: dict[str, str], status: int) -> None:
            if requests.delete_forgets and status == 200:
                held.pop(resource_id, None)
            self._reply(status, {"secret": "body not for logs"})

        def _describe(self, resource_id: str, held: dict[str, str], kind: str) -> None:
            self._check_key()
            requests.verifications.append(f"{kind} {resource_id}")
            if resource_id not in held:
                self._reply(404, {})
            elif kind == "tool":
                self._reply(
                    200,
                    {
                        "id": resource_id,
                        "tool_config": {"description": held[resource_id]},
                    },
                )
            else:
                self._reply(200, {"agent_id": resource_id, "name": held[resource_id]})

        def _observe(self, body: object) -> None:
            self._check_key()
            requests.calls.append((self.command, self.path, body))

        def _check_key(self) -> None:
            # A live control sends the key; one built under the REST
            # override sends the placeholder instead.
            assert self.headers["xi-api-key"] in {API_KEY, PLACEHOLDER_KEY}

        def _reply(self, code: int, body: dict[str, object]) -> None:
            response = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        requests.url = f"http://127.0.0.1:{server.server_port}"
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        thread.start()
        try:
            yield requests
        finally:
            server.shutdown()
            thread.join()
