"""The two tool-owned files ``enable`` writes into ``.punt-labs/vox/``.

Both are thin :class:`~punt_vox.tool_owned_file.ToolOwnedFile` wrappers: the
:class:`VoxMarker` is vox's per-repo on-signal (presence is the whole signal),
and the :class:`DepositedGuide` is the surface-aware agent guide overwritten
wholesale on every ``enable`` so it can never drift from the running vox version.
:class:`~punt_vox.enablement.RepoEnablement` composes both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self, final

from punt_vox.guide_stamp import GuideStamp, GuideStampVerdict
from punt_vox.tool_owned_file import ToolOwnedFile

__all__ = ["DepositedGuide", "VoxMarker"]

# The marker's bytes are irrelevant to enablement (presence is the signal), but
# both surfaces write this exact content, so a CLI marker and an MCP marker are
# byte-identical (§ 2.14).
_MARKER_TEXT = (
    "vox is enabled in this repository.\n"
    "\n"
    "Managed by `vox enable` / `vox disable` (and the `/vox enable` / `/vox disable`\n"
    "slash commands). Presence turns vox's per-repo guidance and hooks on;\n"
    "remove it with `vox disable`, not by hand.\n"
)

_OPT_OUT_TEXT = (
    "vox is opted out in this repository.\n"
    "\n"
    "Managed by `vox disable --scope project --opt-out`. Clear with\n"
    "`vox enable --scope project --clear-opt-out`.\n"
)

_LOCAL_DISABLED_TEXT = (
    "vox is disabled locally in this checkout.\n"
    "\n"
    "Managed by `vox disable` (--scope local). Re-enable with `vox enable`.\n"
)


@final
class VoxMarker:
    """An enablement marker file (``enabled``, ``opt-out``, or ``disabled``)."""

    __slots__ = ("_content", "_file")

    _content: str
    _file: ToolOwnedFile

    def __new__(cls, path: Path, base: Path, *, content: str = _MARKER_TEXT) -> Self:
        """Create a vox marker."""
        self = super().__new__(cls)
        self._file = ToolOwnedFile(path, base)
        self._content = content
        return self

    @classmethod
    def opt_out(cls, path: Path, base: Path) -> Self:
        """Create a ``.punt-labs/vox/opt-out`` marker."""
        return cls(path, base, content=_OPT_OUT_TEXT)

    @classmethod
    def local_disabled(cls, path: Path, base: Path) -> Self:
        """Create a ``.punt-labs/local/vox/disabled`` marker."""
        return cls(path, base, content=_LOCAL_DISABLED_TEXT)

    @property
    def path(self) -> Path:
        """Return the marker file path."""
        return self._file.path

    def is_present(self) -> bool:
        """Return whether the marker exists."""
        return self._file.is_present()

    def write(self) -> None:
        """Create the marker, making its directory if absent; refuse a symlink."""
        self._file.write(self._content)

    def remove(self) -> None:
        """Delete the marker; an already-absent marker is a clean no-op."""
        self._file.remove()


@final
class DepositedGuide:
    """The surface-aware user guide deposited in ``.punt-labs/[local/]vox/CLAUDE.md``.

    The guide is a pinned static copy shipped beside the package (TED-17):
    plain ``enable`` deposits it only when absent, and ``enable --refresh``
    rewrites it when its bytes differ (TED-11, TED-12).
    """

    __slots__ = ("_file",)

    _file: ToolOwnedFile

    def __new__(cls, path: Path, base: Path) -> Self:
        """Create a deposited guide."""
        self = super().__new__(cls)
        self._file = ToolOwnedFile(path, base)
        return self

    @property
    def path(self) -> Path:
        """Return the deposited guide path."""
        return self._file.path

    def is_present(self) -> bool:
        """Return whether the guide file exists."""
        return self._file.is_present()

    def deposit(self, *, refresh: bool = False) -> bool:
        """Deposit the guide if absent, or overwrite when *refresh* is ``True``.

        Plain ``enable`` preserves an existing deposit so mixed tool versions do
        not dirty a checkout (TED-9, TED-17). Only ``enable --refresh`` replaces
        the deposit, skipping the write when bytes already match (TED-12).

        Raises:
            OSError: If the packaged asset cannot be read or written.
        """
        if not refresh and self.is_present():
            return False
        try:
            text = GuideStamp.for_packaged_asset().stamped()
        except (OSError, UnicodeDecodeError) as exc:
            msg = (
                f"vox package is missing its agent guide asset ({exc}); "
                "reinstall punt-vox"
            )
            raise OSError(msg) from exc
        return self._file.write(text)

    def verify(self) -> GuideStampVerdict:
        """Compare this deposit's embedded stamp to the packaged asset."""
        return GuideStamp.for_packaged_asset().verify(self._file.path)
