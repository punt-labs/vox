"""Own the punt-managed ``@``-import section of ``~/.claude/CLAUDE.md``.

Claude Code loads ``~/.claude/CLAUDE.md`` into every session and resolves any
top-level ``@path`` line as an included file. Punt tools register their usage
guides as such lines inside a shared managed section so the guides load in
every project without a per-project edit.

:class:`GlobalClaudeImports` owns ``~/.claude/CLAUDE.md``. It splices the
managed ``<!-- punt:mandatory-reading -->`` section deterministically (import
lines sorted), repairs a half-written section instead of appending a second
one, and writes only when the rendered text differs from what is already on
disk -- so re-running never churns the file's mtime. The vox-side artifact that
writes a usage guide and registers its own import line lives in
:mod:`punt_vox.guidance`.

The ``@``-lines are emitted at top level (never inside a code fence): Claude
Code does not resolve imports written inside code spans. For the same reason
the parser is fence-aware on *input* -- a ``<!-- punt:mandatory-reading -->``
marker that appears inside a fenced block (a tool documenting this feature, a
pasted README) is literal text, not a managed delimiter, and survives a
reconcile byte-for-byte.

**Single-writer assumption.** The managed section is shared: any punt tool may
register its own import line at its own install time. Those installs are rare
and manual, so writes are serialized in practice rather than by a lock. Each
write is atomic (temp file + ``fsync`` + ``os.replace``), so a crash mid-write
never corrupts the user's hand-authored ``CLAUDE.md``; two *concurrent*
installers could still lose one registration to a last-writer-wins race, which
is out of scope here (a lock would be overkill for manual, infrequent installs).
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["GlobalClaudeImports"]


@final
class _Fence:
    """Tracks Markdown code-fence depth while scanning a document line by line.

    A fenced block opens on a line beginning with three or more backticks or
    tildes and closes on a bare run of the same fence character. Inside a
    fence every line is literal text, so the reconciler must not read a
    managed marker there. Feed each line's stripped form in document order;
    :attr:`inside` reports whether the following lines are fenced content.
    """

    __slots__ = ("_char",)

    # The fence character (``` `` ``` `` or ``~``) of the open block, or ``None``
    # when not inside a fence. ``None`` is the documented "no open fence" state,
    # not a failure to produce a value.
    _char: str | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._char = None
        return self

    @property
    def inside(self) -> bool:
        """Return whether the scanner is currently within a fenced block."""
        return self._char is not None

    def feed(self, stripped: str) -> bool:
        """Update state from *stripped*; return True if it is a fence delimiter.

        A delimiter is the opening fence when outside a block, or the matching
        closing fence when inside one. Content lines return False.
        """
        if self._char is None:
            opener = self._opener_char(stripped)
            if opener is None:
                return False
            self._char = opener
            return True
        if self._closes(stripped):
            self._char = None
            return True
        return False

    @staticmethod
    def _opener_char(stripped: str) -> str | None:
        """Return the fence character if *stripped* opens a fence, else None.

        ``None`` is the contract for "not a fence line" (absence), matching the
        ``dict.get`` idiom -- it is not a value the caller failed to produce.
        """
        for char in ("`", "~"):
            if stripped.startswith(char * 3):
                return char
        return None

    def _closes(self, stripped: str) -> bool:
        """Return whether *stripped* is a bare closing fence for the open char.

        A closing fence is a run of the open character (three or more) with no
        trailing info string; ```` ```python ```` opens but never closes.
        """
        char = self._char
        if char is None:
            return False
        return stripped.startswith(char * 3) and stripped == char * len(stripped)


@final
class GlobalClaudeImports:
    """Owns the punt-managed import section of ``~/.claude/CLAUDE.md``.

    The section is delimited by shared markers so a future punt-owned
    multi-tool reconcile supersedes this one cleanly. All import lines in
    the section are kept sorted, and the whole file is rewritten only when
    its text actually changes.
    """

    __slots__ = ("_path",)

    _path: Path

    # Shared markers -- not vox-specific -- so any punt tool can own the
    # same section and a future reconcile can collapse them together.
    _OPEN = "<!-- punt:mandatory-reading -->"
    _CLOSE = "<!-- /punt:mandatory-reading -->"
    # Names the auto-loaded reality: the imported docs load with no action
    # from the reader, so the header must not read like a manual instruction.
    _HEADER = "## Tool Guidance (auto-loaded)"

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the managed ``CLAUDE.md`` path."""
        return self._path

    def register(self, import_line: str) -> bool:
        """Add *import_line* to the managed section. Return True if written."""
        return self._reconcile(lambda imports: imports | {import_line})

    def prune(self, import_line: str) -> bool:
        """Remove *import_line* from the managed section. Return True if written."""
        return self._reconcile(lambda imports: imports - {import_line})

    def _reconcile(self, update: Callable[[frozenset[str]], frozenset[str]]) -> bool:
        """Parse, apply *update* to the import set, and write only if changed.

        The no-op-when-unchanged guard runs first: if the rendered text equals
        what is already on disk the file is never touched, so re-running never
        churns its mtime. See the module docstring for the single-writer
        assumption that lets an atomic replace stand in for a lock.
        """
        original = self._read()
        kept, imports = self._parse(original)
        new_text = self._render(kept, update(frozenset(imports)))
        if new_text == original:
            return False
        self._write_atomic(new_text)
        return True

    def _write_atomic(self, text: str) -> None:
        """Replace the file's contents atomically.

        Write *text* to a temporary file in the target's own directory, flush
        and ``fsync`` it, then ``Path.replace`` it over the target.
        ``Path.replace`` wraps ``os.replace`` -- an atomic rename on POSIX
        (macOS and Linux, the only supported platforms) -- so an interrupted
        write (SIGKILL, power loss) leaves the original ``CLAUDE.md`` untouched
        rather than truncated: the user's hand-authored content is never at
        risk, only the pending replacement.

        ``os.fdopen`` takes ownership of the ``mkstemp`` descriptor *first* so
        the ``with`` block always closes it on every exit path. If ``fdopen``
        itself raises, the raw fd is closed explicitly (it never took
        ownership) -- otherwise a repeated install would leak a descriptor per
        failure. The temp file is unlinked on *any* exception before the
        rename -- ``OSError`` or otherwise -- so no ``.claude-md-*.tmp`` is
        orphaned on any failure path (fdopen, write, flush, fsync, chmod, or
        replace).
        """
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=".claude-md-", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)  # fdopen did not take ownership -- close the raw fd
            tmp.unlink(missing_ok=True)
            raise
        try:
            with handle:  # owns fd now; closes it on every exit path
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp creates the temp at 0600 and Path.replace preserves the
            # source's mode, so without this an existing 0644 CLAUDE.md would
            # silently become 0600 on the first reconcile. Match the target's
            # current mode before the rename; a brand-new file gets 0644.
            tmp.chmod(self._replacement_mode())
            tmp.replace(self._path)
        except BaseException:
            # Any failure before the successful rename -- of any exception type,
            # not just OSError -- leaves the temp behind. Unlink it so no
            # ``.claude-md-*.tmp`` is orphaned (a raising fsync, a non-OSError
            # chmod, a KeyboardInterrupt mid-write all land here). The unlink is
            # suppressed if it itself raises, so the bare ``raise`` re-raises the
            # real cause rather than the cleanup error. Reached only before the
            # rename: once ``tmp.replace`` succeeds the temp no longer exists.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    def _replacement_mode(self) -> int:
        """Return the permission bits to stamp on the replacement file.

        Preserve the target's current mode when it already exists so an atomic
        replace never changes how the user's ``CLAUDE.md`` is exposed. A
        brand-new file gets 0644 (``rw-r--r--``) -- the conventional default a
        plain write would inherit -- rather than the 0600 ``mkstemp`` gives the
        temp. Called before the rename, while the target (if any) still exists.
        """
        if self._path.is_file():
            return stat.S_IMODE(self._path.stat().st_mode)
        return 0o644

    def _read(self) -> str:
        """Return the file's text, or ``""`` when it does not exist."""
        if not self._path.is_file():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _parse(self, text: str) -> tuple[list[str], set[str]]:
        """Split *text* into non-managed lines and the managed import set.

        Non-managed lines keep their original endings
        (``splitlines(keepends=True)``), so ``"".join(kept)`` reproduces the
        user's content byte-for-byte -- trailing blank lines and all.

        Every managed marker (well-formed pair, a lone open that never
        closes, or a stray close) is consumed, and the managed header and
        its padding are dropped. Unknown content that somehow sits between
        markers is preserved rather than destroyed. The result is that
        re-rendering always yields exactly one canonical section.

        The newline terminating the line immediately before a real ``OPEN``
        marker is the separator :meth:`_render` injects to anchor the marker
        on its own line, so it is stripped back off here. That makes the
        write/parse pair lossless: a body with no final newline round-trips
        to no final newline, and one that ends in blank lines keeps every one.

        Marker detection is column-0 only (see :meth:`_is_marker`): an indented
        marker is literal content, matching Markdown's own rule that four-space
        indentation opens a code block. Combined with the fence-awareness below,
        both ways of writing a marker as documentation -- indented or fenced --
        survive a reconcile.

        Parsing is fence-aware: any line inside a Markdown code fence (a
        ```` ``` ```` or ``~~~`` block) is literal text and is preserved
        byte-for-byte, so a ``<!-- punt:mandatory-reading -->`` marker that
        someone documented inside a fenced block is never mistaken for a real
        delimiter and never eaten.
        """
        kept: list[str] = []
        imports: set[str] = set()
        inside = False
        fence = _Fence()
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
        return kept, imports

    @staticmethod
    def _is_marker(line: str, marker: str) -> bool:
        """Return whether *line* is *marker* sitting flush at column 0.

        :meth:`_render` always emits the ``OPEN``/``CLOSE`` markers with no
        leading whitespace, so only a column-0 match is a real managed
        delimiter. Comparing the raw line (newline stripped, indentation kept)
        rather than ``line.strip()`` means an *indented* marker -- a Markdown
        indented code block, or one pasted into prose -- stays literal content
        instead of being consumed and its section mangled. Trailing whitespace
        likewise disqualifies it, since ``_render`` never writes any.
        """
        return line.rstrip("\n") == marker

    @staticmethod
    def _drop_separator(kept: list[str]) -> None:
        """Remove the render-injected newline that anchors ``OPEN`` on its line.

        The separator lives on the last kept line before the marker; stripping
        exactly that one newline is what makes an install->uninstall round-trip
        preserve the user's final-newline state byte-for-byte.
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

    def _render(self, kept: list[str], imports: frozenset[str]) -> str:
        """Rebuild the file from the verbatim non-managed content plus one section.

        *kept* holds the user's lines verbatim (see :meth:`_parse`), so content
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
        # Always inject exactly one newline between the user's content and the
        # managed section. :meth:`_parse` strips exactly this newline off the
        # line before OPEN, so the pair round-trips losslessly: no final newline
        # stays no final newline, and trailing blank lines are all preserved.
        return f"{body}\n{section}\n"

    def _build_section(self, imports: frozenset[str]) -> str:
        """Render the canonical managed section with sorted import lines."""
        lines = [self._OPEN, self._HEADER, "", *sorted(imports), self._CLOSE]
        return "\n".join(lines)
