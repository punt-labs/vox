"""Bounded tool history and its curated projection."""

from __future__ import annotations

from typing import Self, final

from punt_vox.voxd.hook_context_projection import HookToolResult, ToolUpdate
from punt_vox.voxd.hook_failures import HookFailures


@final
class HookToolHistory:
    """Own tool retention, retry failures and the derived file/result view."""

    __slots__ = ("_failures", "_tools")
    _failures: HookFailures
    _tools: dict[int, ToolUpdate]

    def __new__(cls) -> Self:
        """Create empty history with an independent failure tracker."""
        self = super().__new__(cls)
        self._failures = HookFailures()
        self._tools = {}
        return self

    def ingest(self, sequence: int, event: str, payload: dict[str, object]) -> None:
        """Retain only the newest 48 tool updates."""
        update = ToolUpdate.from_payload(payload, event)
        self._failures.observe(sequence, update)
        self._tools[sequence] = update
        if len(self._tools) > 48:
            del self._tools[min(self._tools)]

    def files_and_results(self) -> tuple[list[str], list[HookToolResult]]:
        """Project bounded files and completed results in sender order."""
        files: dict[str, None] = {}
        results: list[HookToolResult] = []
        for sequence, update in sorted(self._tools.items()):
            if update.file:
                files.pop(update.file, None)
                files[update.file] = None
            if update.completed and update.result:
                results.append(update.as_result(sequence))
        return list(files)[-8:], results[-5:]

    def open_failure(self) -> HookToolResult | None:  # None when no failure remains.
        """Return the newest unresolved failure."""
        return self._failures.latest()
