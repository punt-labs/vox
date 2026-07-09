"""Write provider API keys to the daemon's ``keys.env`` file."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Self

from punt_vox.keys import PROVIDER_KEY_NAMES

logger = logging.getLogger(__name__)

_HEADER = (
    "# vox provider keys — loaded by voxd at startup\n"
    "# Written by: vox daemon install\n"
    "# Edit with your normal editor — no sudo required.\n\n"
)
_CONTROL_CHARS = "\x00\n\r"


class KeysEnvWriter:
    """Write provider keys into a ``keys.env`` file for voxd."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def write(self, env: dict[str, str], keys_path: Path) -> Path:
        """Write ``keys.env`` at *keys_path* (mode 0600), merging existing keys.

        Preserves any keys already present that the caller did not override; an
        empty value in *env* removes the key. The write is atomic (temp file at
        mode 0600 + fsync + ``os.replace``) so a crash never leaves a partial
        credentials file and the secret never exists at wider-than-0600 perms.
        """
        self._harden_parent(keys_path)
        self._reject_irregular(keys_path)
        existing, force_fresh = self._read_existing(keys_path)
        content = _HEADER + self._render(self._merge(existing, env))
        if force_fresh:
            self._unlink_broken(keys_path)
        self._write_atomic(keys_path, content)
        return keys_path

    def _harden_parent(self, keys_path: Path) -> None:
        """Create the parent directory at mode 0700, or tighten it if present."""
        keys_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keys_path.parent.chmod(0o700)

    def _reject_irregular(self, keys_path: Path) -> None:
        """Abort the install if *keys_path* exists as a non-regular file."""
        if keys_path.is_symlink() or (keys_path.exists() and not keys_path.is_file()):
            msg = (
                f"{keys_path} exists but is not a regular file. "
                "Remove it manually and re-run install."
            )
            raise SystemExit(msg)

    def _read_existing(self, keys_path: Path) -> tuple[dict[str, str], bool]:
        """Return the merge-base keys and whether to force a fresh write.

        An unreadable file (permission error, corruption, non-UTF-8) yields no
        keys and a force-fresh flag, so the broken file is replaced rather than
        merged into.
        """
        if not keys_path.exists():
            return {}, False
        try:
            text = keys_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "Could not read existing %s: %s — will overwrite with env values",
                keys_path,
                exc,
            )
            return {}, True
        return self._parse(text), False

    def _parse(self, text: str) -> dict[str, str]:
        """Return the ``key=value`` pairs in *text*, skipping blanks and comments."""
        pairs: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key := key.strip():
                pairs[key] = value.strip()
        return pairs

    def _merge(self, existing: dict[str, str], env: dict[str, str]) -> dict[str, str]:
        """Return *existing* overlaid with the provider keys from *env*.

        A control-character value is refused (line-injection defense: without
        it an attacker-controlled env var could smuggle extra ``key=value``
        lines into the file); an empty value removes the key.
        """
        merged = dict(existing)
        for k in PROVIDER_KEY_NAMES:
            if k not in env:
                continue
            value = env[k]
            if not value:
                merged.pop(k, None)
            elif any(c in value for c in _CONTROL_CHARS):
                logger.warning(
                    "Refusing to write %s: value contains control characters", k
                )
            else:
                merged[k] = value
        return merged

    def _render(self, merged: dict[str, str]) -> str:
        """Return the file body: sorted ``key=value`` lines with a trailing newline."""
        return "\n".join(f"{k}={v}" for k, v in sorted(merged.items()) if v) + "\n"

    def _unlink_broken(self, keys_path: Path) -> None:
        """Remove an unreadable existing file before writing a fresh one."""
        try:
            keys_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not unlink unreadable %s: %s — write may fail", keys_path, exc
            )

    def _write_atomic(self, keys_path: Path, content: str) -> None:
        """Write *content* to *keys_path* atomically at mode 0600.

        Write a temp file in the same directory at 0600, fsync it, then
        ``Path.replace`` it into place. The replace is atomic, so a crash
        mid-write can never leave a partially-written credentials file (losing
        provider keys); ``mkstemp`` already creates the temp at 0600, so the
        secret is never wider-than-0600 in the window. ``os.fdopen`` takes
        ownership of the descriptor *first* so the ``with`` block always closes
        it -- even if the ``fchmod`` (a belt-and-suspenders re-assert) raises.
        """
        fd, tmp_name = tempfile.mkstemp(dir=keys_path.parent, prefix=".keys.env.")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(keys_path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
