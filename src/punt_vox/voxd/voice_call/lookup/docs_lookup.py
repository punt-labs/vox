"""Repository documentation lookup through DeepWiki's public MCP server."""

from __future__ import annotations

from typing import ClassVar, Final, Self, final

from fastmcp import Client
from fastmcp.client.transports import ClientTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.types import TextContent

__all__ = ["DeepWikiDocs"]

URL: Final = "https://mcp.deepwiki.com/mcp"
_FAILED: Final = "documentation lookup failed; say so and offer to retry"


@final
class DeepWikiDocs:
    """Ask one fixed question tool; no URL or tool name comes from the model."""

    # The one tool voxd calls, and its arguments, as the server lists them. A
    # server that offers other tools is not followed there.
    TOOL: ClassVar[str] = "ask_wiki_question"
    ARGUMENTS: ClassVar[tuple[str, ...]] = ("repoName", "question")

    __slots__ = ("_transport",)
    _transport: ClientTransport

    def __new__(cls, transport: ClientTransport | None = None) -> Self:
        """Bind the endpoint: DeepWiki's public server unless a test passes another."""
        self = super().__new__(cls)
        self._transport = (
            StreamableHttpTransport(URL) if transport is None else transport
        )
        return self

    async def ask(self, repo: str, question: str) -> str:
        """Return the documentation answer for ``question`` about ``repo``.

        A server error, a missing tool or an unreachable server is one spoken
        message; the server's own text and the transport detail stay out of it.
        """
        arguments = dict(zip(self.ARGUMENTS, (repo, question), strict=True))
        try:
            async with Client(self._transport) as client:
                result = await client.call_tool(self.TOOL, arguments)
        except TimeoutError:
            raise
        except (ToolError, RuntimeError, OSError, ValueError) as exc:
            raise ValueError(_FAILED) from exc
        return "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
