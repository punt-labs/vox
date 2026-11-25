"""Whether systemd counts a process in the installed ``voxd.service`` cgroup.

systemd puts every process the system unit vox installs in
``/system.slice/voxd.service`` (or a child of it). A user-scope
``voxd.service`` lives under ``user.slice`` and is not the installed service.
macOS has no ``/proc`` and launchd keeps no cgroup, so there the answer is
always "not counted".
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, final

from punt_vox.service.backend import PortProbeError

__all__ = ["ServiceCgroup"]


@final
class ServiceCgroup:
    """The system unit's cgroup, read from ``/proc/<pid>/cgroup``."""

    __slots__ = ()

    _PROC: ClassVar[Path] = Path("/proc")
    _UNIT: ClassVar[str] = "/system.slice/voxd.service"

    @classmethod
    def counts(cls, pid: int) -> bool:
        """Return True when systemd counts *pid* in voxd.service's cgroup.

        ``False`` when the process is gone or the host has no ``/proc``.
        Any other read failure raises.

        Raises:
            PortProbeError: If the cgroup file is there but cannot be read.
        """
        cgroup = cls._PROC / str(pid) / "cgroup"
        try:
            text = cgroup.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        except OSError as exc:
            msg = f"cannot read {cgroup}: {exc}"
            raise PortProbeError(msg) from exc
        return any(cls._in_unit(line) for line in text.splitlines())

    @classmethod
    def _in_unit(cls, line: str) -> bool:
        """Return True when one ``/proc/<pid>/cgroup`` line names the unit.

        The path is the field after the last ``:`` (cgroup v1 lines carry a
        controller list before it).
        """
        path = line.rsplit(":", 1)[-1].strip()
        return path == cls._UNIT or path.startswith(cls._UNIT + "/")
