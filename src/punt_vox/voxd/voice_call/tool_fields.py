"""Validate the fields of a worker tool request the way the operation will."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist

__all__ = ["NO_REPOS", "ToolFields"]

NO_REPOS: Final = (
    "no repository is allowlisted for workers; "
    "run `vox worker allow <path>` on the daemon host"
)


@final
class ToolFields:
    """Read the required fields of a request, or raise why it cannot run."""

    __slots__ = ()

    @staticmethod
    def text(arguments: dict[str, object], field: str) -> str:
        """Return a non-empty string field."""
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def start(
        arguments: dict[str, object], projects: ProjectAllowlist
    ) -> tuple[str, str]:
        """Return the validated task and project, or raise why nothing can start."""
        task = ToolFields.text(arguments, "task")
        allowlisted = list(projects.repos())
        if not allowlisted:  # Whatever was named: nothing can start.
            raise ValueError(NO_REPOS)
        project = arguments.get("project")
        if project is None:  # Several repos, so no default was filled in.
            raise ValueError(f"name a project; allowlisted: {allowlisted}")
        if not isinstance(project, str):
            raise ValueError("project must be a string")
        return task, project

    @staticmethod
    def validate(
        name: str, arguments: dict[str, object], projects: ProjectAllowlist
    ) -> None:
        """Raise what the tool ``name`` would raise for these arguments."""
        if name == "start_worker":
            ToolFields.start(arguments, projects)
        elif name == "stop_worker":
            ToolFields.text(arguments, "worker_id")
