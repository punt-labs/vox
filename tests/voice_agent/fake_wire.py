"""A loopback ElevenLabs control endpoint that records what voxd asks of it."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Final, Self
from urllib.parse import parse_qs, urlsplit

from punt_vox.voxd.voice_agent.call_overrides import PLACEHOLDER_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

API_KEY: Final = "localhost-test-key"
SIGNED_URL: Final = "ws://127.0.0.1:42609/convai?conversation_signature=not-for-logs"
_TOOLS: Final = "/v1/convai/tools/"
_AGENTS: Final = "/v1/convai/agents/"
_SIGNED_URL: Final = "/v1/convai/conversation/get-signed-url?agent_id="


_UNSET: Final = object()


class Requests:
    """Record requests and let tests change the server response between calls.

    ``calls`` holds every request except the reads that check a resource's
    marker before a delete (``verifications``) and the account listings
    (``listings``). A create can commit and then drop its reply, as a lost
    connection does, and the listings page like the real ones. The remote account
    is ``tools`` (id to description) and ``agents`` (id to name); a delete
    removes from it only when ``delete_forgets`` is set, because most tests
    reuse one id for every generation.
    """

    calls: list[tuple[str, str, object]]
    verifications: list[str]
    tools: dict[str, str]
    agents: dict[str, str]
    next_tool_ids: list[str]
    next_agent_ids: list[str]
    create_tool_id: str
    disconnect_on_tool_creation: bool
    drop_reply_after_tool_commit: bool  # The tool is created; its reply is lost.
    drop_reply_after_agent_commit: bool  # The agent is created; its reply is lost.
    create_tool_status: int
    commit_failed_create: bool  # A create answered with an error still commits.
    list_status: int
    failing_listings: set[str]  # Kinds ("agents", "tools") whose listing answers 500.
    extra_entries: dict[str, list[object]]  # Raw entries a listing appends.
    has_more_override: object  # Replaces a page's has_more unless it is _UNSET.
    list_page_size: int
    listings: list[str]
    create_agent_status: int
    signed_url_status: int
    delete_tool_status: int
    delete_agent_status: int
    delete_forgets: bool
    lookup_body: bytes | None  # Raw reply to a marker lookup; None: the real shape.
    url: str

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.calls = []
        self.verifications = []
        self.tools = {}
        self.agents = {}
        self.next_tool_ids = []
        self.next_agent_ids = []
        self.create_tool_id = "tool_clock"
        self.disconnect_on_tool_creation = False
        self.drop_reply_after_tool_commit = False
        self.drop_reply_after_agent_commit = False
        self.create_tool_status = 200
        self.commit_failed_create = False
        self.list_status = 200
        self.failing_listings = set()
        self.extra_entries = {}
        self.has_more_override = _UNSET
        self.list_page_size = 100
        self.listings = []
        self.create_agent_status = self.signed_url_status = 200
        self.delete_tool_status = self.delete_agent_status = 404
        self.delete_forgets = False
        self.lookup_body = None
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
                if requests.create_tool_status == 200 or requests.commit_failed_create:
                    requests.tools[tool_id] = body["tool_config"]["description"]
                if requests.drop_reply_after_tool_commit:
                    self.close_connection = True
                    return
                self._reply(requests.create_tool_status, {"id": tool_id})
            elif self.path == "/v1/convai/agents/create":
                agent_id = (
                    requests.next_agent_ids.pop(0)
                    if requests.next_agent_ids
                    else "agent_vox"
                )
                if requests.create_agent_status == 200 or requests.commit_failed_create:
                    requests.agents[agent_id] = body["name"]
                if requests.drop_reply_after_agent_commit:
                    self.close_connection = True
                    return
                self._reply(requests.create_agent_status, {"agent_id": agent_id})
            else:
                self._reply(500, {})

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == _TOOLS.rstrip("/"):
                self._list("tools", requests.tools, lambda text: {"description": text})
            elif path == _AGENTS.rstrip("/"):
                self._list("agents", requests.agents, lambda text: text)
            elif path.startswith(_TOOLS):
                self._describe(path.removeprefix(_TOOLS), requests.tools, "tool")
            elif path.startswith(_AGENTS):
                self._describe(path.removeprefix(_AGENTS), requests.agents, "agent")
            elif self.path.startswith(_SIGNED_URL):
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

        def _list(
            self,
            kind: str,
            held: dict[str, str],
            text: Callable[[str], object],
        ) -> None:
            """Reply with one page, in creation order, after the query's cursor."""
            self._check_key()
            requests.listings.append(kind)
            if requests.list_status != 200 or kind in requests.failing_listings:
                self._reply(
                    requests.list_status if requests.list_status != 200 else 500, {}
                )
                return
            query = parse_qs(urlsplit(self.path).query)
            start = int(query.get("cursor", ["0"])[0])
            ids = list(held)[start : start + requests.list_page_size]
            end = start + len(ids)
            more = end < len(held)
            if kind == "tools":
                items: list[object] = [
                    {"id": i, "tool_config": text(held[i])} for i in ids
                ]
            else:
                items = [{"agent_id": i, "name": text(held[i])} for i in ids]
            if not more:
                items.extend(requests.extra_entries.get(kind, []))
            if requests.has_more_override is not _UNSET:
                self._reply(200, {kind: items, "has_more": requests.has_more_override})
                return
            self._reply(
                200,
                {
                    kind: items,
                    "has_more": more,
                    "next_cursor": str(end) if more else None,
                },
            )

        def _delete(self, resource_id: str, held: dict[str, str], status: int) -> None:
            if requests.delete_forgets and status == 200:
                held.pop(resource_id, None)
            self._reply(status, {"secret": "body not for logs"})

        def _describe(self, resource_id: str, held: dict[str, str], kind: str) -> None:
            self._check_key()
            requests.verifications.append(f"{kind} {resource_id}")
            if resource_id not in held:
                self._reply(404, {})
            elif requests.lookup_body is not None:
                self._send(200, requests.lookup_body)
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
            self._send(code, json.dumps(body).encode())

        def _send(self, code: int, response: bytes) -> None:
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
