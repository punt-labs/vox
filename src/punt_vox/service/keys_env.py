"""Write provider API keys to the daemon's ``keys.env`` file."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self

from punt_vox.keys import PROVIDER_KEY_NAMES

logger = logging.getLogger(__name__)


class KeysEnvWriter:
    """Write provider keys into a ``keys.env`` file for voxd."""

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def write(self, env: dict[str, str], keys_path: Path) -> Path:
        """Write ``keys.env`` to *keys_path*.  chmod 0600.

        Preserves any keys already present in the file that the caller
        did not override.  An empty string in *env* removes the key.

        Runs as the installing user in a user-owned directory.  The
        kernel's normal permission checks are sufficient -- no fd-based
        ownership dance or path-hardening is needed when the process
        cannot write outside its own home.

        Values containing ``\\n``, ``\\r``, or ``\\x00`` are rejected
        (not a privilege defense, just input sanitization -- without
        this an attacker-controlled env var could smuggle extra
        key=value lines into the file).

        If an existing ``keys.env`` is unreadable (permission error,
        corruption, not-a-regular-file, non-UTF-8 bytes), the merge is
        skipped, the broken file is unlinked, and a fresh file is
        written from *env* alone.  Copilot 3048295101 on PR #162.

        The file is created via ``os.open`` with an explicit
        ``mode=0o600`` so there is no instant at which the file exists
        with umask-widened permissions.  Copilot 3048402515 on PR #162.

        The parent directory is also created-or-tightened to mode 0700
        as a belt-and-suspenders step.  Copilot 3048402424 on PR #162.

        If ``keys_path`` itself exists but is not a regular file, the
        install aborts with a clear ``SystemExit``.  Copilot 3048463694
        on PR #162.
        """
        existing: dict[str, str] = {}
        force_fresh = False

        # Create the parent dir at 0700 or tighten it if it already exists.
        keys_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keys_path.parent.chmod(0o700)

        # Reject symlinks, directories, FIFOs, sockets, and device nodes.
        if keys_path.is_symlink() or (keys_path.exists() and not keys_path.is_file()):
            msg = (
                f"{keys_path} exists but is not a regular file. "
                "Remove it manually and re-run install."
            )
            raise SystemExit(msg)

        if keys_path.exists():
            try:
                existing_text = keys_path.read_text()
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(
                    "Could not read existing %s: %s — will overwrite with env values",
                    keys_path,
                    exc,
                )
                existing_text = ""
                force_fresh = True
            for line in existing_text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip()
                if key:
                    existing[key] = value

        merged = dict(existing)
        for k in PROVIDER_KEY_NAMES:
            if k in env:
                if env[k]:
                    value = env[k]
                    if any(c in value for c in "\x00\n\r"):
                        logger.warning(
                            "Refusing to write %s: value contains control characters",
                            k,
                        )
                        continue
                    merged[k] = value
                else:
                    merged.pop(k, None)

        header = (
            "# vox provider keys — loaded by voxd at startup\n"
            "# Written by: vox daemon install\n"
            "# Edit with your normal editor — no sudo required.\n\n"
        )
        lines = [f"{k}={v}" for k, v in sorted(merged.items()) if v]
        content = header + "\n".join(lines) + "\n"

        if force_fresh:
            try:
                keys_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "Could not unlink unreadable %s: %s — write may fail",
                    keys_path,
                    exc,
                )

        # Atomic create-and-truncate with explicit mode 0600.
        fd = os.open(
            str(keys_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        # Belt-and-suspenders: pin to exactly 0o600 regardless of umask.
        keys_path.chmod(0o600)
        return keys_path
