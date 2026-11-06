"""Find the ripgrep the call agent's secret guard will run, the way the guard does.

The pi-side guard (``pi_extension/secret_guard.js``, ``ripgrep()``) lists the
files a directory grep would read with ``rg`` -- pi's own copy under
``<agent dir>/bin`` first, then whatever ``rg`` is on ``PATH`` -- and refuses
the grep when it cannot. pi ships no rg; its grep downloads one on first use,
which a refused grep never reaches. So with no rg, every directory grep of
the call is refused: :class:`Ripgrep` makes that a failure at call start, with
the fix in the message, instead of a call that cannot search.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, final

from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentSpawnError
from punt_vox.voxd.conversation_mode.pi_path import PiPath

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["Ripgrep"]

_MISSING = (
    "ripgrep (rg) is not installed: the call agent's secret guard runs it "
    "before every directory grep and refuses the grep without it. Install it "
    "(brew install ripgrep, or sudo apt install ripgrep) and start the call again"
)


@final
class Ripgrep:
    """Where the guard will find ``rg`` in the call agent's environment."""

    __slots__ = ()

    @staticmethod
    def locate(env: Mapping[str, str]) -> Path:
        """Return the ``rg`` the guard will run under *env*, the child's environment.

        Raises :class:`~.call_agent_errors.CallAgentSpawnError`, naming
        ripgrep and how to install it, when there is none.
        """
        configured = env.get("PI_CODING_AGENT_DIR")
        home = Path(env.get("HOME") or Path.home())
        agent_dir = (
            Path(PiPath.normalize(configured)) if configured else home / ".pi" / "agent"
        )
        if (own := agent_dir / "bin" / "rg").exists():
            return own
        if on_path := shutil.which("rg", path=env.get("PATH", "")):
            return Path(on_path)
        raise CallAgentSpawnError(_MISSING)
