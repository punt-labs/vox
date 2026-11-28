"""The ``mic`` enable/disable tool: one scoped, action-dispatched verb.

``tool-enable-disable.md`` § 2.14 / TED-35: the Claude Code surface writes the
*same* markers as the ``vox enable`` / ``vox disable`` CLI and takes the same
``scope`` parameter (``"local"`` default, or ``"project"``). Enablement is repo
file operations, not daemon-owned audio state, so this tool acts directly on
:class:`~punt_vox.enablement.RepoEnablement`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, Self, final

from mcp.types import TextContent

from punt_vox.enablement import RepoEnablement
from punt_vox.enablement_state import (
    EffectiveEnablement,
    EnablementScope,
    EnablementState,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["EnablementTool"]


def _default_source(scope: EnablementScope) -> RepoEnablement:
    return RepoEnablement.for_cwd(scope=scope)


@final
class EnablementTool:
    """Route a ``mic`` enable/disable action to the per-repo enablement machine."""

    __slots__ = ("_source",)

    _source: Callable[[EnablementScope], RepoEnablement]

    def __new__(
        cls,
        source: Callable[[EnablementScope], RepoEnablement] = _default_source,
    ) -> Self:
        """Create an enablement tool."""
        self = super().__new__(cls)
        self._source = source
        return self

    def dispatch(
        self,
        action: Literal["enable", "disable"],
        scope: EnablementScope = "local",
        *,
        refresh: bool = False,
        opt_out: bool = False,
        clear_opt_out: bool = False,
    ) -> str:
        """Enable or disable vox in the repo the session runs from.

        Args:
            action: ``"enable"`` or ``"disable"``.
            scope: ``"local"`` (personal, gitignored; default) or ``"project"``
                (shared, committed).
            refresh: When ``True`` on ``enable``, overwrite the deposited guide
                from the installed package.
            opt_out: When ``True`` on ``disable`` with ``scope="project"``,
                write committed ``.punt-labs/vox/opt-out``.
            clear_opt_out: When ``True`` on ``enable`` with ``scope="project"``,
                remove ``.punt-labs/vox/opt-out`` and enable.
        """
        if action not in ("enable", "disable"):
            return self._error(f"invalid action '{action}'. Use enable or disable.")
        if scope not in ("local", "project"):
            return self._error(f"invalid scope '{scope}'. Use local or project.")
        try:
            enablement = self._source(scope)
        except ValueError as exc:
            return self._error(str(exc))
        try:
            state = self._apply(
                enablement,
                action,
                refresh=refresh,
                opt_out=opt_out,
                clear_opt_out=clear_opt_out,
            )
        except (OSError, ValueError) as exc:
            return self._error(str(exc))
        reply: dict[str, object] = {
            "action": action,
            "scope": scope,
            "repo": str(enablement.root),
            "enabled": state.on,
            "status": state.status,
            "marker": str(enablement.marker_path),
            "deciding_file": (
                str(state.deciding_file) if state.deciding_file is not None else None
            ),
            "shared_guide_loads": state.shared_guide_loads,
        }
        return json.dumps(reply)

    @staticmethod
    def _apply(
        enablement: RepoEnablement,
        action: Literal["enable", "disable"],
        *,
        refresh: bool,
        opt_out: bool,
        clear_opt_out: bool,
    ) -> EffectiveEnablement:
        """Run the requested transition and return the resulting effective state."""
        if action == "enable":
            return enablement.enable(refresh=refresh, clear_opt_out=clear_opt_out)
        return enablement.disable(opt_out=opt_out)

    @staticmethod
    def dormant_content(name: str) -> list[TextContent] | None:
        """Return a dormant content reply when vox is off in the current repo."""
        if name in ("enablement", "status"):
            return None
        state = EnablementState.resolve_cwd()
        if state is None or state.on:
            return None
        deciding = str(state.deciding_file) if state.deciding_file is not None else None
        payload = json.dumps(
            {
                "error": (
                    f"vox is off in this repository ({state.status}); "
                    "run 'vox enable' or '/vox enable' to enable"
                ),
                "enabled": False,
                "status": state.status,
                "deciding_file": deciding,
            }
        )
        return [TextContent(type="text", text=payload)]

    @staticmethod
    def status_fields() -> dict[str, object]:
        """Return effective enablement fields for ``mic:status`` (TED-24)."""
        state = EnablementState.resolve_cwd()
        if state is None:
            return {
                "enabled": None,
                "enablement_status": "no_repo",
                "deciding_file": None,
                "shared_guide_loads": False,
            }
        return {
            "enabled": state.on,
            "enablement_status": state.status,
            "deciding_file": (
                str(state.deciding_file) if state.deciding_file is not None else None
            ),
            "shared_guide_loads": state.shared_guide_loads,
        }

    @staticmethod
    def _error(message: str) -> str:
        """Return a JSON error object -- the tool never raises across its boundary."""
        return json.dumps({"error": message})
