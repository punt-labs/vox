"""The voxd LaunchAgent plist: the env it captures, its XML, and its file."""

from __future__ import annotations

import html
import logging
import os
import plistlib
import textwrap
from typing import TYPE_CHECKING, Self, cast, final
from xml.parsers.expat import ExpatError

from punt_vox.service.backend import BackendCommandError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

_FALLBACK_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# The LaunchAgent plist, with its XML declaration at column 0. Fields arrive
# through str.format; the XML itself carries no braces.
_TEMPLATE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>{label}</string>
        <key>ProcessType</key>
        <string>Interactive</string>
        <key>LimitLoadToSessionType</key>
        <string>Aqua</string>
        <key>ProgramArguments</key>
        <array>
    {args}
        </array>
        <key>EnvironmentVariables</key>
        <dict>
            <key>PATH</key>
            <string>{path}</string>{env}
        </dict>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
    </dict>
    </plist>
""")

# Env vars carried from the installing shell into the LaunchAgent. PATH is not
# among them -- it is always written, with a fallback, because a plist with no
# PATH gives voxd launchd's bare default.
#
# SSL_CERT_FILE and REQUESTS_CA_BUNDLE matter behind a TLS-inspecting corporate
# proxy, which presents its own CA. A launchd-started voxd that lacks them
# cannot reach HTTPS endpoints the installing shell reached, and the only way
# left to supply them is a bootout/bootstrap cycle from an activation script --
# whose asynchronous bootout leaves a window where the CA is untrusted and any
# concurrent fetch fails. Capturing them at install time closes that window.
_CAPTURED_ENV_KEYS = ("VOXD_BIND", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


@final
class VoxdPlist:
    """Own the voxd LaunchAgent plist -- its content and its file on disk.

    Holds no launchd knowledge: it writes and removes a file and answers with
    XML. Every ``launchctl`` interaction belongs to the backend that composes
    this. The plist location is injected rather than derived, so a test can
    author one under ``tmp_path`` without touching the real LaunchAgents dir.
    """

    __slots__ = ("_exec_args_fn", "_label", "_path")

    _label: str
    _path: Path
    _exec_args_fn: Callable[[], list[str]]

    def __new__(
        cls,
        label: str,
        path: Path,
        exec_args_fn: Callable[[], list[str]],
    ) -> Self:
        self = super().__new__(cls)
        self._label = label
        self._path = path
        self._exec_args_fn = exec_args_fn
        return self

    @property
    def path(self) -> Path:
        """Return where this plist is written."""
        return self._path

    def exists(self) -> bool:
        """Return True when the plist is present on disk."""
        return self._path.exists()

    @staticmethod
    def captured_env() -> dict[str, str]:
        """Return the install-time env vars to bake in, omitting unset ones.

        An unset *or empty* var is omitted rather than written as an empty
        string: an empty ``SSL_CERT_FILE`` is worse than an absent one, because
        OpenSSL reads it as "trust nothing here" instead of falling back to the
        system store.
        """
        env = os.environ
        return {key: env[key] for key in _CAPTURED_ENV_KEYS if env.get(key)}

    def _program_args_xml(self) -> str:
        """Return the ``<string>...</string>`` block for ProgramArguments.

        ``html.escape`` XML-escapes each arg -- launchd reads the ``<string>``
        content literally, so ``&``, ``<``, ``>`` and quotes in a path or
        argument have to be entity-encoded, not shell-quoted.
        """
        return "\n".join(
            f"        <string>{html.escape(a)}</string>" for a in self._exec_args_fn()
        )

    def _captured_env_xml(self) -> str:
        """Return the trailing key/string pairs for EnvironmentVariables."""
        return "".join(
            f"\n        <key>{html.escape(k)}</key>"
            f"\n        <string>{html.escape(v)}</string>"
            for k, v in self.captured_env().items()
        )

    def content(self) -> str:
        """Generate the LaunchAgent plist XML.

        LaunchAgents run as the session user by default -- no ``UserName``
        key is needed (and it is invalid for agents).  ``ProcessType=Interactive``
        prevents App Nap-style throttling on the windowless daemon.

        ``LimitLoadToSessionType=Aqua`` pins the job to the graphical login
        session, so voxd inherits the Aqua bootstrap context that grants
        CoreAudio queue access.  Without it, the agent can end up in a
        Background context that mostly works but intermittently fails
        ``AudioQueueStart`` with ``-66681`` after a ~15s block -- 72 such
        failures were logged in one session before the pin was added.
        """
        # `or` rather than a get() default: PATH set to the empty string has to
        # take the fallback too. An empty <string> here leaves voxd with no PATH
        # at all, so every subprocess lookup it makes fails -- the same
        # empty-is-worse-than-absent trap as the CA vars in captured_env().
        path_value = html.escape(os.environ.get("PATH") or _FALLBACK_PATH)
        # Substitute into the already-dedented template: dedenting after
        # substitution would let the argument lines set the common margin and
        # indent the XML declaration, which plistlib rejects.
        return _TEMPLATE.format(
            label=html.escape(self._label),
            args=self._program_args_xml(),
            path=path_value,
            env=self._captured_env_xml(),
        )

    def write(self) -> None:
        """Author the plist on disk with 0644 permissions.

        The encoding is explicit because the XML declares ``UTF-8``, while
        ``write_text`` would otherwise use the process locale: under a
        non-UTF-8 locale a non-ASCII path in ProgramArguments would either
        raise or land as bytes the declaration contradicts.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self.content(), encoding="utf-8")
        self._path.chmod(0o644)
        logger.info("Wrote plist to %s", self._path)

    def installed_bind(self) -> str:
        """Return ``EnvironmentVariables.VOXD_BIND`` from the installed plist.

        ``""`` when the plist is absent or sets none. A plist that cannot be
        read or parsed raises ``BackendCommandError``.
        """
        try:
            return self._bind_in(plistlib.loads(self._path.read_bytes()))
        except FileNotFoundError:
            return ""
        except (OSError, ValueError, ExpatError) as exc:
            argv = ["read", str(self._path)]
            raise BackendCommandError.of(argv, 1, str(exc)) from exc

    @staticmethod
    def _bind_in(data: object) -> str:
        """Return ``EnvironmentVariables.VOXD_BIND`` from parsed plist *data*."""
        try:
            bind = cast("dict[str, dict[str, object]]", data)["EnvironmentVariables"][
                "VOXD_BIND"
            ]
        except (KeyError, TypeError):
            return ""
        return bind if isinstance(bind, str) else ""

    def remove(self) -> None:
        """Delete the plist, tolerating its absence."""
        self._path.unlink(missing_ok=True)
        logger.info("Removed %s", self._path)
