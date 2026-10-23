"""Write provider API keys to the daemon's ``keys.env`` file."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Self

from punt_vox.atomic_file import AtomicFile
from punt_vox.service.keys_env_merge import KeysEnvMerge

logger = logging.getLogger(__name__)

_HEADER = (
    "# vox provider keys — loaded by voxd at startup\n"
    "# Written by: vox daemon install\n"
    "# Edit with your normal editor — no sudo required.\n\n"
)
_REPAIR = "so its stored keys cannot be kept. Fix or remove it, then re-run install."


class KeysEnvWriter:
    """Write provider keys into a ``keys.env`` file for voxd."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def write(self, env: Mapping[str, str], keys_path: Path) -> Path:
        """Write ``keys.env`` at *keys_path* (mode 0600), merging existing keys.

        Preserves stored keys the caller did not override; an empty value never
        removes one (see :class:`KeysEnvMerge`), and an existing file that cannot
        be read raises ``SystemExit`` instead of being overwritten. The write is
        atomic (0600 temp file + fsync + ``os.replace``), so a crash leaves no
        partial file and the secret is never wider than 0600.
        """
        self._harden_parent(keys_path)
        self._reject_irregular(keys_path)
        merge = KeysEnvMerge.over_text(self._stored_text(keys_path), env)
        for notice in merge.notices:
            logger.warning("%s", notice)  # names and reasons only, never values
        content = _HEADER + self._render(merge.merged)
        # mode=0o600 forces the secret's perms even over an existing wider file;
        # AtomicFile's default would preserve an existing mode (a leak risk here).
        AtomicFile(keys_path).replace(content, mode=0o600)
        return keys_path

    def _harden_parent(self, keys_path: Path) -> None:
        """Create the parent directory at mode 0700, or tighten it if present."""
        keys_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keys_path.parent.chmod(0o700)

    def _reject_irregular(self, keys_path: Path) -> None:
        """Abort the install if *keys_path* exists as a non-regular file.

        Neither check follows a symlink, so any symlink (dangling or not) is
        refused along with directories, FIFOs, and devices.
        """
        lexists = keys_path.exists(follow_symlinks=False)
        if lexists and not keys_path.is_file(follow_symlinks=False):
            raise SystemExit(
                f"{keys_path} exists but is not a regular file. "
                "Remove it manually and re-run install."
            )

    def _stored_text(self, keys_path: Path) -> str:
        """Return the text already in *keys_path*; empty when it does not exist.

        A file that exists but cannot be read stops the install with
        ``SystemExit`` naming the path and the problem, and is left as it is:
        writing over it would silently drop every key it holds.
        """
        if not keys_path.exists():
            return ""
        try:
            return keys_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # The decoder's message quotes the offending bytes; name the path only.
            msg = f"{keys_path} is not valid UTF-8 text, {_REPAIR}"
            raise SystemExit(msg) from None
        except OSError as exc:
            msg = f"Cannot read {keys_path} ({exc.strerror}), {_REPAIR}"
            raise SystemExit(msg) from exc

    def _render(self, merged: Mapping[str, str]) -> str:
        """Return the file body: sorted ``key=value`` lines with a trailing newline."""
        return "\n".join(f"{k}={v}" for k, v in sorted(merged.items())) + "\n"
