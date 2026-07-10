"""Parse and render the punt-managed ``@``-import block within a Markdown file."""

from __future__ import annotations

from typing import Self, final

from punt_vox.markdown_fence import Fence

__all__ = ["ManagedSection"]


@final
class ManagedSection:
    """The marker grammar of the punt-managed import block.

    Splits a Markdown document into the user's verbatim content and the set of
    ``@path`` import lines held between shared markers, and rebuilds the
    document from that split with a single canonical section (import lines
    sorted). The markers are shared -- not vox-specific -- so a future
    punt-owned multi-tool reconcile can collapse them together.

    Parsing is fence-aware and column-0 only: a marker that is indented (a
    Markdown code block) or sits inside a ```` ``` ````/``~~~`` fence is literal
    content, preserved byte-for-byte, never mistaken for a delimiter.
    """

    __slots__ = ()

    # Shared markers -- any punt tool can own the same section.
    _OPEN = "<!-- punt:mandatory-reading -->"
    _CLOSE = "<!-- /punt:mandatory-reading -->"
    # Names the auto-loaded reality: the imported docs load with no action from
    # the reader, so the header must not read like a manual instruction.
    _HEADER = "## Tool Guidance (auto-loaded)"

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def parse(self, text: str) -> tuple[list[str], frozenset[str]]:
        """Split *text* into non-managed lines and the managed import set.

        Non-managed lines keep their original endings
        (``splitlines(keepends=True)``), so ``"".join(kept)`` reproduces the
        user's content byte-for-byte -- trailing blank lines and all.

        Every managed marker (well-formed pair, a lone open that never closes,
        or a stray close) is consumed, and the managed header and its padding
        are dropped; unknown content between markers is preserved rather than
        destroyed. Re-rendering thus yields exactly one canonical section.

        The newline before a real ``OPEN`` marker is the separator
        :meth:`render` injects; :meth:`_drop_separator` strips it back off so
        the render/parse pair is lossless.
        """
        kept: list[str] = []
        imports: set[str] = set()
        inside = False
        fence = Fence()
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            was_fenced = fence.inside
            if fence.feed(stripped) or was_fenced:
                # Fence delimiter or fenced content: literal, never a marker.
                kept.append(line)
                continue
            if self._is_marker(line, self._OPEN):
                self._drop_separator(kept)
                inside = True
                continue
            if self._is_marker(line, self._CLOSE):
                inside = False
                continue
            if inside:
                self._absorb_managed(line, stripped, kept, imports)
                continue
            kept.append(line)
        return kept, frozenset(imports)

    @staticmethod
    def _is_marker(line: str, marker: str) -> bool:
        """Return whether *line* is *marker* sitting flush at column 0.

        :meth:`render` emits the markers at column 0 with no surrounding
        whitespace, so only an exact column-0 match is a real delimiter.
        Comparing the raw line (ending stripped, indentation kept) rather than
        ``line.strip()`` keeps an *indented* or trailing-space marker -- pasted
        prose or a Markdown code block -- as literal content instead of eating
        its section. ``rstrip("\\r\\n")`` strips the ending only (not a trailing
        space), so a CRLF/lone-CR marker line ``...-->\\r`` still matches and its
        existing section is recognized rather than duplicated on register.
        """
        return line.rstrip("\r\n") == marker

    @staticmethod
    def _drop_separator(kept: list[str]) -> None:
        """Remove the render-injected newline that anchors ``OPEN`` on its line.

        :meth:`render` injects exactly one ``\\n`` between the user's content
        and the section; this strips exactly that one ``\\n`` back off, so an
        install->uninstall round-trip preserves the user's content byte-for-byte
        regardless of line ending. Because the reader keeps endings verbatim,
        one rule covers all: a LF/CRLF body puts the separator on its own line
        (dropped whole); a no-final-newline body has it appended to the last
        line (``\\n`` peels off); a lone-CR body fuses ``\\r`` with the injected
        ``\\n`` into ``\\r\\n`` that ``splitlines`` rejoins, and dropping the
        ``\\n`` restores the lone ``\\r``.
        """
        if kept and kept[-1].endswith("\n"):
            kept[-1] = kept[-1][:-1]

    def _absorb_managed(
        self, line: str, stripped: str, kept: list[str], imports: set[str]
    ) -> None:
        """Route a line found between ``OPEN`` and ``CLOSE``.

        Import lines feed the managed set; the canonical header and its blank
        padding are dropped; anything else is preserved rather than destroyed.
        """
        if stripped.startswith("@"):
            imports.add(stripped)
        elif stripped and stripped != self._HEADER:
            kept.append(line)

    def render(self, kept: list[str], imports: frozenset[str]) -> str:
        """Rebuild the file from the verbatim non-managed content plus one section.

        *kept* holds the user's lines verbatim (see :meth:`parse`), so content
        outside the managed section is preserved byte-for-byte. When no import
        remains the result is exactly the user's content -- uninstalling a file
        that had no section before install restores it byte-for-byte.
        """
        body = "".join(kept)
        if not imports:
            return body
        section = self._build_section(imports)
        if not body:
            return f"{section}\n"
        # Inject exactly one newline between the user's content and the section;
        # :meth:`_drop_separator` strips exactly this newline back off on parse.
        return f"{body}\n{section}\n"

    def _build_section(self, imports: frozenset[str]) -> str:
        """Render the canonical managed section with sorted import lines."""
        lines = [self._OPEN, self._HEADER, "", *sorted(imports), self._CLOSE]
        return "\n".join(lines)
