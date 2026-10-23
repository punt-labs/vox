"""Write provider API keys to the daemon's ``keys.env`` file."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Self

from punt_vox.atomic_file import AtomicFile
from punt_vox.keys import HEADER_CARRIED_KEY_NAMES, PROVIDER_KEY_NAMES, parse_keys_env

logger = logging.getLogger(__name__)

_HEADER = (
    "# vox provider keys — loaded by voxd at startup\n"
    "# Written by: vox daemon install\n"
    "# Edit with your normal editor — no sudo required.\n\n"
)
# Values sent as HTTP header content (HEADER_CARRIED_KEY_NAMES) must be
# printable non-space ASCII, 0x21-0x7E, which also rejects a pasted smart quote.
# The AWS secret key only signs and never goes on the wire. The region does reach
# the wire, as part of the endpoint host name, but botocore validates it against
# its own host-label pattern before building a request, so it, the secret key,
# and the plain settings (profile, provider) get the line-break check alone.
_HEADER_SAFE = frozenset(map(chr, range(0x21, 0x7F)))
_CONTROL_CHARS = frozenset("\x00\n\r")
_VALUE_CHECK = dict.fromkeys(HEADER_CARRIED_KEY_NAMES, _HEADER_SAFE.issuperset)
_KEPT = "Ignoring empty %s; keeping its keys.env value (edit the file to remove it)"
_REFUSED = "Refusing to write %s: value contains a character no HTTP header may carry"
_CONTROL = "Refusing to write %s: value contains control characters"
_UNREADABLE = "Could not read existing %s: %s — will overwrite with env values"
_UNLINK_FAILED = "Could not unlink unreadable %s: %s — write may fail"


class KeysEnvWriter:
    """Write provider keys into a ``keys.env`` file for voxd."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def write(self, env: dict[str, str], keys_path: Path) -> Path:
        """Write ``keys.env`` at *keys_path* (mode 0600), merging existing keys.

        Preserves stored keys the caller did not override; an empty value never
        removes one. The write is atomic (0600 temp file + fsync + ``os.replace``),
        so a crash leaves no partial file and the secret is never wider than 0600.
        """
        self._harden_parent(keys_path)
        self._reject_irregular(keys_path)
        existing, force_fresh = self._read_existing(keys_path)
        content = _HEADER + self._render(self._merge(existing, env))
        if force_fresh:
            self._unlink_broken(keys_path)
        # mode=0o600 forces the secret's perms even over an existing wider file;
        # AtomicFile's default would preserve an existing mode (a leak risk here).
        AtomicFile(keys_path).replace(content, mode=0o600)
        return keys_path

    def _harden_parent(self, keys_path: Path) -> None:
        """Create the parent directory at mode 0700, or tighten it if present."""
        keys_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keys_path.parent.chmod(0o700)

    def _reject_irregular(self, keys_path: Path) -> None:
        """Abort the install if *keys_path* exists as a non-regular file."""
        if keys_path.is_symlink() or (keys_path.exists() and not keys_path.is_file()):
            raise SystemExit(
                f"{keys_path} exists but is not a regular file. "
                "Remove it manually and re-run install."
            )

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
            logger.warning(_UNREADABLE, keys_path, exc)
            return {}, True
        return parse_keys_env(text), False

    def _merge(self, existing: dict[str, str], env: dict[str, str]) -> dict[str, str]:
        """Return *existing* overlaid with the provider keys present in *env*.

        An empty value never removes a stored key: a failed secret lookup
        exports ``KEY=""`` rather than unsetting it, and must not wipe a working
        credential. A value failing its :data:`_VALUE_CHECK` is refused, which
        also stops a newline smuggling extra ``key=value`` lines into the file.
        """
        merged = dict(existing)
        for key in sorted(PROVIDER_KEY_NAMES & env.keys()):
            value = env[key]
            if not value:
                if merged.get(key):
                    self._warn_kept(key)
            elif _VALUE_CHECK.get(key, _CONTROL_CHARS.isdisjoint)(value):
                merged[key] = value
            else:
                self._warn_refused(key)
        return merged

    def _warn_kept(self, key: str) -> None:
        """Log, by name only, that an empty *key* left the stored value alone."""
        logger.warning(_KEPT, key)

    def _warn_refused(self, key: str) -> None:
        """Log, by name only, that *key* was refused; a stored value is kept."""
        logger.warning(_REFUSED if key in HEADER_CARRIED_KEY_NAMES else _CONTROL, key)

    def _render(self, merged: dict[str, str]) -> str:
        """Return the file body: sorted ``key=value`` lines with a trailing newline."""
        return "\n".join(f"{k}={v}" for k, v in sorted(merged.items()) if v) + "\n"

    def _unlink_broken(self, keys_path: Path) -> None:
        """Remove an unreadable existing file before writing a fresh one."""
        try:
            keys_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(_UNLINK_FAILED, keys_path, exc)
