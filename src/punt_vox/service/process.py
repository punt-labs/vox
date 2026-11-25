"""Process management for the voxd daemon."""

from __future__ import annotations

import ipaddress
import logging
import os
import platform
import re
import socket
import struct
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.paths import run_dir as _user_run_dir
from punt_vox.service.backend import PortProbeError
from punt_vox.service.daemon_process import DaemonProcess
from punt_vox.service.deadline import Deadline
from punt_vox.service.process_identity import ProcessIdentity
from punt_vox.service.stale_port_file import StalePortFile
from punt_vox.service.termination import Termination

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle
    from punt_vox.service.port_file_cleanup import PortFileCleanup

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS = 5

DEFAULT_PORT = 8421


@final
class ListenAddress:
    """The local addresses a listener on *port* may have to serve *host*.

    A health request goes to the unit's polled host, so a LISTEN socket on the
    port at another address is not the one it reaches: voxd on 127.0.0.1 and a
    foreign process on [::1] are different listeners. The exact address, the
    family's wildcard (which serves it), and, for an IPv4 host, the v4-mapped
    form a dual-stack socket shows, are the forms that count.
    """

    __slots__ = ("_host", "_port")

    _host: ipaddress.IPv4Address | ipaddress.IPv6Address
    _port: int

    def __new__(cls, host: str, port: int) -> Self:
        """Create the address for *host* (an IP literal) on *port*."""
        self = super().__new__(cls)
        self._host = ipaddress.ip_address(host)
        self._port = port
        return self

    @property
    def port(self) -> int:
        """Return the port."""
        return self._port

    def lsof_names(self) -> frozenset[str]:
        """Return the ``lsof -Fn`` names a matching LISTEN socket shows."""
        port = self._port
        if self._host.version == 6:
            return frozenset({f"[{self._host}]:{port}", f"*:{port}"})
        return frozenset(
            {f"{self._host}:{port}", f"[::ffff:{self._host}]:{port}", f"*:{port}"}
        )

    def proc_forms(self, table: str) -> frozenset[str]:
        """Return the ``local_address`` hex forms that match in ``/proc/net/<table>``.

        The wildcard is written as the integer zero address, never as a
        literal: nothing here binds.
        """
        hexport = f"{self._port:04X}"
        if table == "tcp":
            if self._host.version == 6:
                return frozenset()
            hosts = (self._host, ipaddress.IPv4Address(0))
            return frozenset(f"{self._hex4(h)}:{hexport}" for h in hosts)
        mapped = (
            self._host
            if self._host.version == 6
            else ipaddress.IPv6Address(f"::ffff:{self._host}")
        )
        hosts6 = (mapped, ipaddress.IPv6Address(0))
        return frozenset(f"{self._hex6(h)}:{hexport}" for h in hosts6)

    @staticmethod
    def _hex4(host: ipaddress.IPv4Address) -> str:
        """Return *host* as ``/proc/net/tcp`` prints it: one little-endian word."""
        (word,) = struct.unpack("<I", socket.inet_pton(socket.AF_INET, str(host)))
        return f"{word:08X}"

    @staticmethod
    def _hex6(host: ipaddress.IPv6Address) -> str:
        """Return *host* as ``/proc/net/tcp6`` prints it: four little-endian words."""
        words = struct.unpack("<IIII", socket.inet_pton(socket.AF_INET6, str(host)))
        return "".join(f"{word:08X}" for word in words)


class ProcessManager:
    """Manage voxd daemon processes: find, verify, kill, and port-file I/O."""

    __slots__ = ("_deadline", "_note")

    # SIGTERM grace plus the SIGKILL wait for an install/uninstall kill.
    _KILL_BUDGET_S: ClassVar[float] = 7.0
    _PROC: ClassVar[Path] = Path("/proc")
    # /proc/net/tcp: the state column reads 0A for a LISTEN socket.
    _LISTEN: ClassVar[str] = "0A"

    _deadline: Deadline
    _note: str  # "" unless the last kill left serve.port behind

    def __new__(cls) -> Self:
        """Create a process manager."""
        self = super().__new__(cls)
        self._deadline = Deadline.never()
        self._note = ""
        return self

    @property
    def port_file_note(self) -> str:
        """Return why the last confirmed kill left ``serve.port`` behind; ``""``."""
        return self._note

    def within(self, deadline: Deadline) -> ProcessManager:
        """Return a manager whose probe commands all finish by *deadline*.

        Each ``fuser``/``lsof``/``ps`` run is cut to what the deadline leaves,
        and one started after it raises ``PortProbeError`` without running.
        """
        bounded = ProcessManager()
        bounded._deadline = deadline
        return bounded

    @staticmethod
    def _run_dir() -> Path:
        return _user_run_dir()

    @property
    def port_file(self) -> Path:
        """Return the path of ``serve.port``, the port hint voxd publishes."""
        return self._run_dir() / "serve.port"

    def read_port_file(self) -> int | None:
        """Read the daemon port from ``serve.port``.

        ``None`` is the documented answer when the file does not exist, and
        when its content is not a valid TCP port: voxd publishes it by
        atomic rename, so a malformed hint is stale, warned about and
        ignored. A read error other than not-found, and bytes that are not
        UTF-8, raise ``PortProbeError`` without quoting the file's content.

        Raises:
            PortProbeError: If process or port ownership cannot be determined safely.
        """
        return StalePortFile.read_hint(self.port_file)

    def discard_port_file(self, port: int) -> PortFileCleanup:
        """Remove ``serve.port`` when it names *port* or does not parse.

        Runs under the lock voxd publishes the file with (see
        :class:`StalePortFile`). Returns what it did; a lock, read or removal
        failure raises ``PortProbeError`` so the caller can report it.

        Raises:
            PortProbeError: If the port file cannot be locked, read, or removed safely.
        """
        return StalePortFile.read(self.port_file).discard(port)

    def find_pid_on_port(self, port: int) -> list[int]:
        """Return all PIDs with connections to *port*.

        On macOS ``lsof`` lists only the TCP listener on *port* (a client
        whose peer port is *port* would otherwise match). On Linux ``fuser
        <port>/tcp`` returns space-separated PIDs, the daemon and connected
        clients alike. Returns **all** of them so the caller can check each
        for voxd identity.

        Fails closed: a missing tool, a timeout, or an error exit raises
        ``PortProbeError``. Only a silent "found nothing" (exit 1, no stdout
        and no stderr) reads as an empty port.

        Raises:
            PortProbeError: If process or port ownership cannot be determined safely.
        """
        if platform.system() == "Darwin":
            # -w: lsof's warnings (an unreadable mount) are not errors and
            # must not fail the probe through a non-empty stderr.
            cmd = ["lsof", "-w", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"]
        else:
            cmd = ["fuser", f"{port}/tcp"]
        result = self._probe(cmd)
        out = result.stdout.strip()
        if self._found_nothing(result):
            return []
        if result.returncode != 0:
            msg = (
                f"cannot inspect port {port}: {cmd[0]} exited "
                f"{result.returncode}: {result.stderr.strip()}"
            )
            raise PortProbeError(msg)
        # isdecimal (not isdigit) is the strict subset int() always accepts:
        # it rejects superscripts like "²" that isdigit would pass.
        return [int(token) for token in re.split(r"[\s:]+", out) if token.isdecimal()]

    def holds_port(self, pid: int, host: str, port: int) -> bool:
        """Return True when *pid* itself holds the listener on *host*:*port*.

        Asked of one process, so it never walks every open file the way the
        port-wide ``lsof`` does: on macOS ``lsof -a -p <pid>`` is limited to
        that process; on Linux the LISTEN rows for the address in
        ``/proc/net/tcp`` and ``tcp6`` are matched against the socket inodes
        in ``/proc/<pid>/fd``. The listener must serve *host*
        (:class:`ListenAddress`): one on the port at another address is not
        it. Cut to this manager's deadline. Fails closed: a check that cannot
        be made raises ``PortProbeError``.

        Raises:
            PortProbeError: If the process or the port table cannot be inspected.
        """
        address = ListenAddress(host, port)
        if platform.system() == "Darwin":
            return self._lsof_holds_port(pid, address)
        self._deadline.cap(
            _SUBPROCESS_TIMEOUT_SECONDS, f"cannot inspect pid {pid} on port {port}"
        )
        return self._proc_holds_port(pid, address)

    def _lsof_holds_port(self, pid: int, address: ListenAddress) -> bool:
        """``lsof`` limited to *pid*: True when it lists a listener at *address*.

        ``-Fn`` prints one ``n<name>`` line per socket, so the local address
        is matched, not only the port.
        """
        port = address.port
        cmd = [
            "lsof", "-w", "-nP", "-Fn", "-a", "-p", str(pid),
            f"-iTCP:{port}", "-sTCP:LISTEN",
        ]  # fmt: skip
        result = self._probe(cmd)
        if self._found_nothing(result):
            return False
        if result.returncode != 0:
            msg = (
                f"cannot inspect pid {pid} on port {port}: lsof exited "
                f"{result.returncode}: {result.stderr.strip()}"
            )
            raise PortProbeError(msg)
        names = {line[1:] for line in result.stdout.splitlines() if line[:1] == "n"}
        return not names.isdisjoint(address.lsof_names())

    @classmethod
    def _proc_holds_port(cls, pid: int, address: ListenAddress) -> bool:
        """Match *pid*'s socket inodes against the LISTEN sockets at *address*."""
        listening = cls._listening_sockets(address)
        fd_dir = cls._PROC / str(pid) / "fd"
        try:
            links = {cls._link(entry) for entry in fd_dir.iterdir()}
        except OSError as exc:
            msg = f"cannot read {fd_dir}: {exc}"
            raise PortProbeError(msg) from exc
        return not listening.isdisjoint(links)

    @staticmethod
    def _link(entry: Path) -> str:
        """Return what the fd symlink *entry* points at; ``""`` once it is closed."""
        try:
            return str(entry.readlink())
        except FileNotFoundError:
            return ""

    @classmethod
    def _listening_sockets(cls, address: ListenAddress) -> frozenset[str]:
        """Return the ``socket:[inode]`` links of every LISTEN socket at *address*."""
        found: set[str] = set()
        for table in ("tcp", "tcp6"):
            path = cls._PROC / "net" / table
            try:
                rows = path.read_text(encoding="ascii").splitlines()[1:]
            except FileNotFoundError:
                continue  # no IPv6 on this host
            except (OSError, UnicodeDecodeError) as exc:
                msg = f"cannot read {path}: {exc}"
                raise PortProbeError(msg) from exc
            forms = address.proc_forms(table)
            found.update(cls._listen_link(path, row, forms) for row in rows)
        found.discard("")
        return frozenset(found)

    @classmethod
    def _listen_link(cls, path: Path, row: str, forms: frozenset[str]) -> str:
        """Return one *row* of *path*'s socket link if it listens at one of *forms*.

        Raises:
            PortProbeError: If the row is not in the kernel's table format.
        """
        fields = row.split()
        # local_address is HEXIP:HEXPORT; st is the state; inode is the 10th field.
        try:
            listens = fields[3] == cls._LISTEN
            local = fields[1]
            ip_hex, port_hex = local.rsplit(":", 1)
            well_formed = int(ip_hex, 16) >= 0 and int(port_hex, 16) >= 0
            inode = fields[9]
        except (IndexError, ValueError) as exc:
            msg = f"cannot parse {path} row {row.strip()[:24]!r}: {exc}"
            raise PortProbeError(msg) from exc
        if not (well_formed and listens) or local not in forms:
            return ""
        return f"socket:[{inode}]"

    def identity(self, pid: int) -> ProcessIdentity | None:
        """Return *pid*'s start time and command line; ``None`` once it is gone.

        ``ps`` exits 1 and says nothing for a pid that does not exist; any
        other failure, or a line that does not parse, raises ``PortProbeError``
        rather than reading as "gone".

        Raises:
            PortProbeError: If process or port ownership cannot be determined safely.
        """
        result = self._probe(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="])
        if self._found_nothing(result):
            return None
        if result.returncode != 0:
            msg = (
                f"cannot inspect pid {pid}: ps exited {result.returncode}: "
                f"{result.stderr.strip()}"
            )
            raise PortProbeError(msg)
        try:
            return ProcessIdentity.from_ps(result.stdout.strip())
        except ValueError as exc:
            raise PortProbeError(f"cannot inspect pid {pid}: {exc}") from exc

    def is_vox_daemon_process(self, pid: int) -> bool:
        """Return True when *pid* is alive and its command line runs voxd."""
        seen = self.identity(pid)
        if seen is not None and seen.is_voxd():
            return True
        logger.warning(
            "PID %d is not a voxd process (command: %s)",
            pid,
            seen.command if seen is not None else "<gone>",
        )
        return False

    def open_voxd(self, pid: int) -> ProcessHandle | None:
        """Return a handle pinned to *pid*; ``None`` when it is gone or not voxd."""
        return DaemonProcess.on_this_host(self.identity).open(pid)

    def kill_pid(self, pid: int) -> bool:
        """Terminate the voxd process *pid* through a pinned handle.

        Return True when no voxd process remains under that handle (killed, or
        already gone), False when it survived (e.g. permission denied).
        """
        handle = self.open_voxd(pid)
        if handle is None:
            logger.info("PID %d is gone or is not voxd; nothing to kill", pid)
            return True
        logger.info("Terminating voxd PID %d", pid)
        return handle.terminate(self._KILL_BUDGET_S) is Termination.EXITED

    @staticmethod
    def _found_nothing(result: subprocess.CompletedProcess[str]) -> bool:
        """Return True for the tools' silent "no match": exit 1, no output.

        An emptiness check, not a text match: any stderr at all means the
        tool failed, whatever its locale said.
        """
        silence = (result.returncode, result.stdout.strip(), result.stderr.strip())
        return silence == (1, "", "")

    def _probe(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run one probe command; raise ``PortProbeError`` if it cannot run.

        The C locale fixes ``ps -o lstart`` at its 24-character form. The run
        is cut to what this manager's deadline leaves.
        """
        timeout = self._deadline.cap(
            _SUBPROCESS_TIMEOUT_SECONDS, f"cannot run {cmd[0]}"
        )
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError as exc:
            hint = " (install psmisc)" if cmd[0] == "fuser" else ""
            msg = f"cannot run {cmd[0]}: not found{hint}"
            raise PortProbeError(msg) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f"cannot run {cmd[0]}: {exc}"
            raise PortProbeError(msg) from exc

    def kill_stale_daemon(self) -> bool:
        """Kill a daemon process occupying the port.  Return True if killed.

        ``serve.port`` is read once, before the port is probed: the port acted
        on and the file pinned for removal come from that one open file. It
        is cleared after the kill only if it is still that file, so a voxd
        that binds and publishes at any point after the read -- the service
        manager respawning it while the old one dies -- keeps its own.
        """
        self._note = ""
        stale = StalePortFile.read(self.port_file)
        port = stale.port if stale.port is not None else DEFAULT_PORT
        pid = self._stale_voxd(port)
        if pid is None:
            return False
        if not self.kill_pid(pid):
            logger.warning("Failed to kill PID %d — leaving state files intact", pid)
            return False
        self._clear_after_kill(stale, port)
        return True

    def _stale_voxd(self, port: int) -> int | None:
        """Return the first voxd pid on *port*; ``None`` when no holder is voxd."""
        pids = self.find_pid_on_port(port)
        for pid in pids:
            if self.is_vox_daemon_process(pid):
                logger.info("Found stale daemon PID %d on port %d", pid, port)
                return pid
            logger.debug(
                "PID %d on port %d is not a voxd process — skipping", pid, port
            )
        if pids:
            logger.warning("No voxd process found among PIDs %s on port %d", pids, port)
        return None

    def _clear_after_kill(self, stale: StalePortFile, port: int) -> None:
        """Clear ``serve.port`` once the daemon is confirmed gone.

        The kill already happened, so a file that cannot be cleared must not
        fail the caller -- install would abort with no daemon and no unit. It
        is logged and kept as :attr:`port_file_note` for the caller to report.
        """
        try:
            stale.discard(port)
        except PortProbeError as exc:
            logger.warning("voxd stopped, but serve.port was not cleared: %s", exc)
            self._note = f"daemon stopped; could not clear serve.port: {exc}"

    def ensure_port_free(self) -> None:
        """Kill stale daemon if present; abort if port remains occupied.

        The post-kill re-check must target the ACTUAL port the daemon was
        last known to bind, not ``DEFAULT_PORT``.  The port file read
        MUST happen before ``kill_stale_daemon`` runs, because a
        successful kill clears ``serve.port`` -- which drops the very
        value we need for the post-kill re-check.

        Raises:
            SystemExit: If a process still owns the required port.
        """
        target_port = self.read_port_file() or DEFAULT_PORT
        self.kill_stale_daemon()
        pids = self.find_pid_on_port(target_port)
        if pids:
            msg = (
                f"Port {target_port} is still in use (PIDs: {pids})."
                " Stop the process and retry."
            )
            raise SystemExit(msg)
