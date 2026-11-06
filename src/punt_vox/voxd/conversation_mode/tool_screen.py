"""Defence in depth: end the call if a denied read ever actually executed.

The pi-side guard extension blocks a denied tool call before it runs; this is
the layer behind it. :class:`ToolScreen` watches the RPC stream for tool calls
that *executed* -- a ``tool_execution_end`` that is not an error -- and checks
what they touched against :class:`~.secret_path_policy.SecretPathPolicy`:
the call's path and glob arguments (remembered from its
``tool_execution_start``, since the end record does not repeat them), and, for
grep, every file its output names, resolved against grep's own search path
the way pi formats them. A blocked or failed call read nothing, so it is not a
breach: the guard's refusal goes back to the model and the call carries on.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.pi_path import PiPath

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord
    from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy

__all__ = ["ToolScreen"]

logger = logging.getLogger(__name__)

# How the pi-side guard's refusals begin (``REASON_PREFIX`` in
# ``pi_extension/secret_guard.js``).
GUARD_REASON_PREFIX = "vox secret guard:"


@final
class ToolScreen:
    """Recognise an executed tool call that reached a denied path."""

    __slots__ = ("_cwd", "_policy", "_started")
    _policy: SecretPathPolicy
    _cwd: Path
    _started: dict[str, tuple[str, dict[str, object]]]

    def __new__(cls, policy: SecretPathPolicy, cwd: Path) -> Self:
        self = super().__new__(cls)
        self._policy = policy
        self._cwd = cwd
        self._started = {}
        return self

    def breached(self, record: PiRpcRecord) -> bool:
        """Return whether *record* shows a denied read that really ran."""
        if record.kind == "tool_execution_start":
            self._started[record.tool_call_id] = (record.tool_name, record.tool_args())
            return False
        if record.kind != "tool_execution_end":
            return False
        # Absent exactly when no start was seen for this call.
        started = self._started.pop(record.tool_call_id, None)
        if record.is_error:
            return False
        if started is None:
            # What it read is unknown, so it may have been anything: fail closed.
            logger.warning(
                "call agent tool call %r ended with no start; treated as a breach",
                record.tool_call_id,
            )
            return True
        name, args = started
        return self._arguments_denied(name, args) or (
            name == "grep" and self._grep_output_denied(args, record.result_text())
        )

    @staticmethod
    def guard_refusal(record: PiRpcRecord) -> str:
        """Return the pi-side guard's reason when *record* is a call it blocked."""
        error = record.error_text()
        return error if error.startswith(GUARD_REASON_PREFIX) else ""

    def _arguments_denied(self, name: str, args: dict[str, object]) -> bool:
        if self._policy.denies(self._path_arg(args), cwd=self._cwd):
            return True
        glob_keys = ("glob", "pattern") if name == "find" else ("glob",)
        return any(
            isinstance(value := args.get(key), str) and self._policy.denies_glob(value)
            for key in glob_keys
        )

    def _grep_output_denied(self, args: dict[str, object], output: str) -> bool:
        """Check each file grep's output names, relative to grep's search path.

        pi prints a match as ``<path>:<line>: <text>``, the path relative to
        the directory searched -- or only the basename when a single file was
        searched, a file already judged as grep's own path argument.
        """
        base = PiPath.resolve(self._path_arg(args), self._cwd)
        if not base.is_dir():
            return False
        return any(
            self._policy.denies(line.split(":", 1)[0], cwd=base)
            for line in output.splitlines()
            if ":" in line
        )

    @staticmethod
    def _path_arg(args: dict[str, object]) -> str:
        path = args.get("path")
        return path if isinstance(path, str) and path else "."
