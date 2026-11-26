"""Repository documentation lookup through DeepWiki's public MCP server."""

from __future__ import annotations

from typing import Final, Self, final

from fastmcp import Client
from fastmcp.client.transports import ClientTransport, StreamableHttpTransport
from mcp.types import TextContent

__all__ = ["DeepWikiDocs"]

URL: Final = "https://mcp.deepwiki.com/mcp"
# The one tool voxd calls: a server that offers other tools is not followed there.
_TOOL: Final = "ask_question"


@final
class DeepWikiDocs:
    """Ask one fixed question tool; no URL or tool name comes from the model."""

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
        """Return the documentation answer for ``question`` about ``repo``."""
        async with Client(self._transport) as client:
            result = await client.call_tool(
                _TOOL, {"repoName": repo, "question": question}
            )
        return "\n".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
