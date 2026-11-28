"""The ``vox enable`` / ``vox disable`` / ``vox notify`` CLI verbs.

``enable`` / ``disable`` turn vox on and off in the repo the CLI runs from --
pure file operations over :class:`~punt_vox.enablement.RepoEnablement`, never a
daemon round-trip. Both default to ``--scope local`` (personal, gitignored);
``--scope project`` writes the shared committed marker, guide, import, and
grants. ``notify`` sets the personal notification level (``normal`` or
``continuous``) in gitignored ``vox.local.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ClassVar, Self, cast, final

import typer

from punt_vox.config import ConfigStore
from punt_vox.dirs import writable_config_dir
from punt_vox.enablement import RepoEnablement
from punt_vox.enablement_state import EffectiveEnablement, EnablementScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.cli_io import OutputFlags
    from punt_vox.output_formatter import OutputFormatter

__all__ = ["EnablementCli", "build_enablement_commands"]

_JsonOutput = Annotated[bool, typer.Option("--json", help="Output JSON.")]
_Verbose = Annotated[
    bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
]
_Quiet = Annotated[
    bool, typer.Option("--quiet", "-q", help="Suppress non-JSON output.")
]

_ScopeOption = Annotated[
    str,
    typer.Option(
        "--scope",
        help=(
            "Target scope: 'local' (personal, gitignored; default) "
            "or 'project' (shared, committed)."
        ),
    ),
]
_RefreshFlag = Annotated[
    bool,
    typer.Option(
        "--refresh",
        help="Rewrite the deposited guide from the installed package.",
    ),
]
_ClearOptOutFlag = Annotated[
    bool,
    typer.Option(
        "--clear-opt-out",
        help="Remove .punt-labs/vox/opt-out and enable (requires --scope project).",
    ),
]
_OptOutFlag = Annotated[
    bool,
    typer.Option(
        "--opt-out",
        help=(
            "Write committed .punt-labs/vox/opt-out to refuse enablement "
            "(requires --scope project)."
        ),
    ),
]
_PurgeFlag = Annotated[
    bool,
    typer.Option("--purge", help="Also remove the scope's vox subtree."),
]
_NotifyMode = Annotated[
    str,
    typer.Argument(
        help=(
            "'normal' fires on task completion + permission prompts; "
            "'continuous' also announces real-time signals."
        ),
    ),
]


@final
class EnablementCli:
    """The enable/disable/notify verbs, bound onto the top-level ``vox`` app."""

    __slots__ = ("_flags", "_formatter")

    _formatter: OutputFormatter
    _flags: OutputFlags

    _NOTIFY_VALUES: ClassVar[dict[str, str]] = {"normal": "y", "continuous": "c"}
    _VALID_SCOPES: ClassVar[frozenset[str]] = frozenset({"local", "project"})

    def __new__(cls, formatter: OutputFormatter, flags: OutputFlags) -> Self:
        """Create an enablement cli."""
        self = super().__new__(cls)
        self._formatter = formatter
        self._flags = flags
        return self

    @classmethod
    def _parse_scope(cls, scope: str) -> EnablementScope:
        """Validate *scope* and narrow it to :data:`EnablementScope`."""
        if scope not in cls._VALID_SCOPES:
            raise typer.BadParameter("scope must be 'local' or 'project'")
        return cast("EnablementScope", scope)

    def enable(
        self,
        *,
        scope: _ScopeOption = "local",
        refresh: _RefreshFlag = False,
        clear_opt_out: _ClearOptOutFlag = False,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Turn vox on in this repo for the chosen scope (default: local).

        Plain ``enable`` deposits the guide only when absent; pass ``--refresh``
        to update an existing deposited guide. Writes no personal preferences to
        tracked ``vox.md``. Never runs git.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        parsed_scope = self._parse_scope(scope)
        enablement, state = self._transition(
            parsed_scope,
            lambda e: e.enable(refresh=refresh, clear_opt_out=clear_opt_out),
        )
        rules = enablement.permission_rules
        settings_rel = enablement.settings_path.relative_to(enablement.root)
        payload: dict[str, object] = {
            "enabled": state.on,
            "status": state.status,
            "scope": parsed_scope,
            "repo": str(enablement.root),
            "marker": str(enablement.marker_path),
            "deciding_file": (
                str(state.deciding_file) if state.deciding_file is not None else None
            ),
            "allowed": list(rules.get("allow", ())),
            "ask": list(rules.get("ask", ())),
        }
        text = (
            f"vox enabled ({parsed_scope}) in {enablement.root}\n"
            f"allowed in {settings_rel}: {', '.join(rules.get('allow', ()))}\n"
            f"still prompts: {', '.join(rules.get('ask', ()))}"
        )
        self._formatter.emit(payload, text)

    def disable(
        self,
        *,
        scope: _ScopeOption = "local",
        purge: _PurgeFlag = False,
        opt_out: _OptOutFlag = False,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Turn vox off in this repo for the chosen scope (default: local).

        In ``--scope local`` when the repo is project-enabled, writes
        ``.punt-labs/local/vox/disabled`` so vox is off for this checkout while
        leaving tracked files untouched. Pass ``--scope project --opt-out`` to
        commit ``.punt-labs/vox/opt-out``.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        parsed_scope = self._parse_scope(scope)
        action: Callable[[RepoEnablement], EffectiveEnablement] = (
            (lambda e: e.purge()) if purge else (lambda e: e.disable(opt_out=opt_out))
        )
        enablement, state = self._transition(parsed_scope, action)
        verb = "purged" if purge else ("opted out" if opt_out else "disabled")
        removed = [r for tier in enablement.permission_rules.values() for r in tier]
        settings_rel = enablement.settings_path.relative_to(enablement.root)
        lines = [
            f"vox {verb} ({parsed_scope}) in {enablement.root}",
            f"removed from {settings_rel}: {', '.join(removed)}",
        ]
        if state.shared_guide_loads:
            lines.append(
                "note: shared .punt-labs/vox/CLAUDE.md still loads in Claude Code"
            )
        self._formatter.emit(
            {
                "enabled": state.on,
                "status": state.status,
                "scope": parsed_scope,
                "purged": purge,
                "opt_out": opt_out,
                "repo": str(enablement.root),
                "deciding_file": (
                    str(state.deciding_file)
                    if state.deciding_file is not None
                    else None
                ),
                "shared_guide_loads": state.shared_guide_loads,
                "removed": removed,
            },
            "\n".join(lines),
        )

    def notify(
        self,
        mode: _NotifyMode,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Set the personal notification level within "on" (normal or continuous).

        Writes to gitignored ``vox.local.md``. Distinct from the enablement
        marker: ``off`` is not a level; use ``vox disable`` to turn vox off.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        if mode not in self._NOTIFY_VALUES:
            raise typer.BadParameter("mode must be normal or continuous")
        value = self._NOTIFY_VALUES[mode]

        store = ConfigStore(writable_config_dir())
        first_init = store.read_field("notify") is None
        updates: dict[str, str] = {"notify": value}
        if value == "c" or (first_init and value == "y"):
            updates["speak"] = "y"
        store.write_fields(updates)
        self._formatter.emit(updates, f"Notify: {mode}.")

    @staticmethod
    def _transition(
        scope: EnablementScope,
        action: Callable[[RepoEnablement], EffectiveEnablement],
    ) -> tuple[RepoEnablement, EffectiveEnablement]:
        """Wire *scope*, run *action*, and map boundary faults to exit 1."""
        try:
            enablement = RepoEnablement.for_cwd(scope=scope)
            state = action(enablement)
        except (OSError, ValueError) as exc:
            typer.echo(f"vox: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        return enablement, state


def build_enablement_commands(
    app: typer.Typer, formatter: OutputFormatter, flags: OutputFlags
) -> None:
    """Register ``enable`` / ``disable`` / ``notify`` as top-level ``vox`` commands."""
    cli = EnablementCli(formatter, flags)
    app.command("enable")(cli.enable)
    app.command("disable")(cli.disable)
    app.command("notify")(cli.notify)
