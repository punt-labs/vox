"""Doctor sub-check for the Claude Desktop config and its vox MCP registration.

Claude Desktop is an optional host: every row this check produces is optional,
so an absent, unreadable, or unlocatable config never fails ``vox doctor``. It
reads someone else's config file -- a system boundary -- and so owns the
parse-and-classify logic that boundary needs, apart from the daemon checks in
``doctor.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.desktop_install import DesktopInstaller
from punt_vox.doctor_result import OK, OPTIONAL, CheckResult

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["DesktopCheck"]


@final
class DesktopCheck:
    """Verdict for Claude Desktop: config present or not, vox registered or not.

    Stateless past the imports, like :class:`~punt_vox.doctor_mpv.MpvCheck`:
    the config location is asked of :class:`DesktopInstaller` on every run.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        return super().__new__(cls)

    def run(self) -> list[CheckResult]:
        """Return the config row and the registration row.

        ``config_path`` is a *partial* function: it names the file only on the
        platforms vox has verified Claude Desktop's location for, and raises
        for the rest rather than guessing one. Every other sub-check still has
        an answer on such a host, so the refusal degrades to a single optional
        row -- a doctor run that aborts on an uncheckable optional dependency
        reports nothing about the ones that matter. This mirrors
        :meth:`~punt_vox.service.installer.ServiceInstaller.detect_platform`,
        whose ``SystemExit`` Typer renders as one clean line.
        """
        try:
            config_path = DesktopInstaller.config_path()
        except ValueError as exc:
            return [CheckResult.of(OPTIONAL, f"Claude Desktop: {exc}", required=False)]

        if not config_path.exists():
            return [
                CheckResult.of(
                    OPTIONAL, "Claude Desktop config: not found", required=False
                ),
                self._not_registered(),
            ]

        # Out of jail (under ~/Library or $XDG_CONFIG_HOME, neither data root):
        # present/absent verdict only -- the absolute config path never crosses
        # to a client.
        return [
            CheckResult.of(OK, "Claude Desktop config: present", required=False),
            self._registration_verdict(config_path),
        ]

    @staticmethod
    def _not_registered() -> CheckResult:
        """Return the "no vox entry" row, with the command that creates one."""
        return CheckResult.of(
            OPTIONAL,
            "Claude Desktop MCP: not registered (run 'vox desktop install')",
            required=False,
        )

    @classmethod
    def _registration_verdict(cls, config_path: Path) -> CheckResult:
        """Return whether an existing config registers the vox MCP server.

        Reading someone else's config file is a system boundary: unparseable
        JSON, an unreadable file, bytes that are not UTF-8, or a top level that
        is not a JSON object are all reported as "could not read", never as a
        crash and never as a false "not registered". The non-object case is the
        one that bites silently -- ``json.loads`` accepts a bare list or string
        happily and only the ``mcpServers`` lookup afterwards would raise.
        """
        parsed: object
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Unreadable and non-object share one verdict, so a parse failure
            # falls through to the same gate rather than duplicating the row.
            parsed = None
        if not isinstance(parsed, dict):
            return CheckResult.of(
                OPTIONAL, "Claude Desktop MCP: could not read config", required=False
            )
        servers = cast("dict[str, object]", parsed).get("mcpServers")
        if isinstance(servers, dict) and "vox" in servers:
            return CheckResult.of(OK, "Claude Desktop MCP: registered", required=False)
        return cls._not_registered()
