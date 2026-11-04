# `vox daemon stop` and `vox daemon start` — Daemon Lifecycle Design

Bead: vox-9354. Status: design, revision 2 after djb's round-1 review, plus
a leader amendment for djb's round-2 finding (no `sudo -n -l` probe, §3.3)
and its Low items; ready for implementation. No code changes.

## 1. Problem

`vox daemon` exposes `install`, `uninstall`, `restart` and `status`. Nothing
brings voxd down and leaves it down, and nothing brings it up without first
bringing it down. Verifying a fresh build needs `restart`, which runs both
halves and reports one outcome for both. A maintenance window, a port
conflict on 8421, or freeing the audio device needs `kill <pid>`, which
systemd (`Restart=on-failure`) and launchd (`KeepAlive`) may undo.

## 2. The system today

These facts come from reading the worktree at `feat/vox-9354-daemon-stop-start`
and running read-only commands on this Linux host (`pembroke`):

```text
$ systemctl show voxd -p LoadState -p ActiveState -p SubState \
    -p UnitFileState -p MainPID -p Restart -p FragmentPath
Restart=on-failure
MainPID=1471111
LoadState=loaded
ActiveState=active
SubState=running
FragmentPath=/etc/systemd/system/voxd.service
UnitFileState=enabled

$ vox daemon status --json
{"status": "ok", "port": 8421, "pid": 1471111, "daemon_version": "5.0.5", ...}

$ systemctl show nonexistent-voxd-xyz -p LoadState -p ActiveState
LoadState=not-found
ActiveState=inactive
```

`systemctl stop` is not a failure, so `Restart=on-failure` leaves a stopped
unit stopped. On macOS the plist carries `RunAtLoad` and `KeepAlive`, so only
`launchctl bootout` keeps voxd down. Killing the pid does not.

The design assumes nothing about any host's sudo policy: it must work for
scoped NOPASSWD, mixed NOPASSWD, and password-only sudoers alike (§3.3).

### 2.1 Where the lifecycle code lives

| Module | What it does today | Defect relevant to this bead |
|---|---|---|
| `service/systemd.py` `SystemdBackend.stop()` (lines 125–133) | `sudo systemctl stop voxd`, `check=False`, logs "Stopped" | **Silent failure.** A wrong password, a sudoers refusal, or a systemctl error all log "Stopped" and return. |
| `service/launchd.py` `LaunchdBackend.stop()` | `LaunchctlAgent.bootout()` when the plist exists | bootout's non-zero exit is logged at debug and ignored, even for a loaded job. |
| `service/process.py` `find_pid_on_port` (lines 70–75) | `fuser`/`lsof` | **Fails open.** A missing tool or a timeout returns `[]`, which reads as "port empty". |
| `service/process.py` `is_vox_daemon_process` | one `ps` per pid | **Fails open.** A `ps` error returns `False`, which reads as "not voxd". |
| `service/process.py` `kill_pid` (lines 108–166) | checks identity once, then signals the bare pid for up to ~7 s | **Pid-reuse window.** The pid can be recycled between the identity check and SIGTERM or SIGKILL. |
| `service/health.py` `HealthTarget` (lines 77, 125–129) | the bind comes from the shell's `VOXD_BIND`; the token comes from `read_token_file()` | **Trust leak.** When `serve.token` is absent, the token is `None`, so `VoxClient._resolve_token` (`client.py` 243–250) falls back to the shell's `VOXD_TOKEN` and puts it in the URL sent to whatever listens on 8421. |
| `service/__init__.py` `stop_daemon(plat)` | free-function platform ladder | one of five copies of the ladder (§7). |
| `daemon_restarter.py` `DaemonRestarter` | the whole restart, including `typer` output | Outside `service/`. Imports `typer`. Reaches into private `_LABEL`/`_LAUNCHD_PLIST`. Duplicates the start argv and the health poll. |
| `cli_daemon.py` `DaemonCli` | `install`/`uninstall`/`restart`/`status` | no `stop` or `start`. |

## 3. Design

### 3.1 Shape

```text
 vox daemon stop|start|restart        (cli_daemon.py — parse flags, emit, exit)
              │  DaemonLifecycle().stop() / .start() / .restart()
              ▼
 ┌──────────────────────────── service/ ─────────────────────────────┐
 │ DaemonLifecycle  (lifecycle.py)                                   │
 │   preconditions → observe → act → verify → LifecycleReport        │
 │        │                    │                │                    │
 │        ▼                    ▼                ▼                    │
 │  ServiceBackend       DaemonProbe        LifecycleReport          │
 │  (backend.py)         (daemon_probe.py)  (lifecycle_report.py)    │
 │   ├ SystemdBackend     ├ ProcessManager (pids on 8421, raises     │
 │   │  └ PrivilegedRun   │   PortProbeError on tool failure)        │
 │   └ LaunchdBackend     ├ DaemonProcess (pidfd / start-time handle)│
 │      └ LaunchctlAgent  └ HealthTarget (bind from installed unit,  │
 │                          token from serve.token or no connect)    │
 └───────────────────────────────────────────────────────────────────┘
        │ sudo [-n] systemctl …             │ WebSocket health()
        ▼ launchctl bootout/bootstrap       ▼
     systemd / launchd  ───── runs ─────▶ voxd :8421
```

All lifecycle logic lives in `service/`, behind the existing backend seam.
The CLI holds none of it: each verb calls one `DaemonLifecycle` method,
emits the report it gets back through the shared `OutputFormatter`, and
exits with the report's code. The daemon verbs have one surface (CLI). There
is no `mic` tool, because an MCP client that stops voxd cuts off its own
daemon. So the commands layer (python.md rule 5) does not apply, as for
`install` and `uninstall` today.

### 3.2 The backend seam: `ServiceBackend`

New `src/punt_vox/service/backend.py` holds the Protocol and the errors the
lifecycle catches. It is a types module, per PY-IC-9.

```python
class ServiceBackend(Protocol):
    def is_installed(self) -> bool: ...      # unit file / plist present
    def is_active(self) -> bool: ...         # systemd: active|activating|reloading
                                             # launchd: job loaded
    def main_pid(self) -> int | None: ...    # None: the manager reports no process
    def installed_bind(self) -> str: ...     # VOXD_BIND written into the unit/plist;
                                             # "" when the unit sets none
    def request_stop(self) -> None: ...      # raises BackendCommandError | PrivilegeDenied
    def request_start(self) -> None: ...     # raises BackendCommandError | PrivilegeDenied

@final
class BackendCommandError(RuntimeError):     # argv, exit code, stderr tail
@final
class PrivilegeDenied(PermissionError):      # which command, and how to run it
@final
class PortProbeError(RuntimeError):          # fuser/lsof/ps missing, failed or timed out
```

| Method | `SystemdBackend` | `LaunchdBackend` |
|---|---|---|
| `is_installed` | `/etc/systemd/system/voxd.service` exists | `~/Library/LaunchAgents/com.punt-labs.voxd.plist` exists |
| `is_active` | `systemctl is-active voxd` ∈ {`active`, `activating`, `reloading`} | `LaunchctlAgent.is_loaded()` |
| `main_pid` | `systemctl show -p MainPID --value voxd`; `0` → `None` | `LaunchctlAgent.pid()` (new): `pid = N` from `launchctl print gui/<uid>/<label>`; absent → `None`; malformed → raise |
| `installed_bind` | parse `Environment="VOXD_BIND=…"` from the unit file (mode 644, world-readable), undoing `%%` | `plistlib` read of `EnvironmentVariables.VOXD_BIND` from the plist |
| `request_stop` | `PrivilegedRun(["systemctl", "stop", "voxd"])` (§3.3) | `LaunchctlAgent.bootout()`, which now raises `LaunchctlError` when the job was loaded and bootout exits non-zero; translated to `BackendCommandError` |
| `request_start` | `PrivilegedRun(["systemctl", "start", "voxd"])` | `LaunchctlAgent.start()`; `LaunchctlError` → `BackendCommandError` |

A backend command's exit code never decides the outcome by itself. After
any non-zero exit, `DaemonLifecycle` observes the state again. If the
postcondition already holds (for example, the job exited between observe
and bootout), the verb carries on. Otherwise the outcome is the error's
classification. This keeps the verbs idempotent when two actors race.

`ServiceInstaller.backend_for(plat) -> ServiceBackend` is the only place
that picks a backend for a platform (§7).

### 3.3 Privilege on Linux: `PrivilegedRun`

There is no `sudo -v` gate. A scoped NOPASSWD rule (for example
`jfreeman ALL=(root) NOPASSWD: /usr/bin/systemctl stop voxd, /usr/bin/systemctl start voxd`)
lets the real command run with no password, but `sudo -v` would still ask
for one. A cached timestamp cannot be relied on either: `timestamp_timeout`
may be 0, and on this host a root-only `/etc/sudoers.d/timeout` sets it to
something the agent cannot read. Today's code runs plain `sudo systemctl …`
(`systemd.py` 128–131, `daemon_restarter.py` 123–126).

`PrivilegedRun` (in `service/systemd.py`, private to that backend) runs one
`systemctl` verb:

1. Run `sudo -n systemctl <verb> voxd` with stderr captured. `-n` never
   prompts. Exit 0 → return. This is the whole path on a host with scoped
   NOPASSWD, and on a host whose timestamp is still cached.
2. Exit 4 → `PrivilegeDenied` (systemctl/polkit access denied). Exit 5 →
   `BackendCommandError` with "voxd.service not loaded; run
   `vox daemon install`". Neither status can come from sudo itself.
3. Exit 1 is ambiguous: sudo returns 1 when it needs a password or refuses
   the command, and systemctl returns 1 for a generic failure. There is no
   probe that separates the two. `sudo -n -l <cmd>` reports whether the
   command is *permitted*, not whether it runs without a password: under the
   default `listpw=any`, one NOPASSWD entry anywhere in the user's sudoers
   lets `sudo -l` answer without authenticating, so it exits 0 for a command
   that still needs a password (sudoers(5), "listpw"). So no `-l` probe is
   used:
   - No controlling terminal (`os.open("/dev/tty")` fails) →
     `PrivilegeDenied`, with the captured stderr tail verbatim and the text
     "sudo could not run `systemctl <verb> voxd` non-interactively, or
     systemctl failed (see detail); run `vox daemon <verb>` from a terminal,
     or grant NOPASSWD for `systemctl <verb> voxd`".
   - A terminal is present → log the sudo notice, then run the interactive
     `sudo systemctl <verb> voxd` once, with stderr going to the terminal so
     the operator sees sudo's own messages. Exit 0 → return. Exit 4 →
     `PrivilegeDenied`. Exit 5 → `BackendCommandError` (not loaded). Any
     other non-zero → the lifecycle observes again (§3.2): if the
     postcondition holds, the verb continues; if not, the result is
     `backend_failed` with the exit code, and the detail says sudo or
     systemctl refused (the operator saw which on the terminal).

Classification uses exit codes and observed state only. It never matches
sudo's or systemctl's stderr text, which is localised and changes between
versions. Known limit: without a terminal, an exit-1 systemctl failure and a
sudo password requirement both report `permission_denied`; the stderr tail
in `detail` shows the operator which one it was. This is stated in the
report text, not hidden.

macOS needs none of this. The LaunchAgent lives in the user's `gui/<uid>`
domain, and `launchctl` needs no sudo.

### 3.4 The probe: `DaemonProbe` and `DaemonProcess`

New `src/punt_vox/service/daemon_probe.py`. `DaemonProbe` composes
`ProcessManager`, `HealthTarget` and a `Clock` Protocol (`monotonic()`,
`sleep()`), so tests poll on fake time with no real sleep.

**Ports.** The unit and plist always run `voxd --port 8421`
(`installer.py` 89), and health always targets `DEFAULT_PORT`. The probe
watches `{8421} ∪ {serve.port}` when a `serve.port` file exists, but only
8421 counts toward the lifecycle. A voxd on any other port, for example a
foreground `voxd --port 9000` that left a stale `serve.port`, is named in
`detail` and never signalled.

**Fail closed.** `ProcessManager.find_pid_on_port` and
`is_vox_daemon_process` change to raise `PortProbeError` when `fuser`,
`lsof` or `ps` is missing, exits with an error status, or times out. The
"ran cleanly, found nothing" result (`fuser` exit 1 with empty output)
still means empty. `DaemonProbe` maps `PortProbeError` to `backend_failed`,
with "cannot inspect port 8421: fuser not found (install psmisc)". The
existing callers (`ensure_port_free`, `kill_stale_daemon`,
`ServiceInstaller._stale_daemon_survives`) inherit the change, so
`install` and `uninstall` also fail loudly instead of reading a broken
probe as an empty port.

**Observation methods.**

- `voxd_processes(8421) -> frozenset[DaemonProcess]`: the visible voxd
  processes on the port.
- `port_listening(8421) -> bool`: a plain TCP connect to the health host,
  with no payload and no token. It sees a listener even when its owner
  belongs to another user. A non-root `fuser`/`lsof` cannot see such an
  owner, which is why there is no cross-user kill row (§4).
- `health(gate) -> HealthStatus | None`: a token-bearing health request,
  made only when `gate` holds (§3.5). `None` means no answer, which is the
  documented contract.
- `wait_down(...)` and `wait_up(...)`: bounded polls (0.2 s interval) that
  return the moment their condition holds.

**`DaemonProcess`.** New `src/punt_vox/service/daemon_process.py`. This is
a handle to one voxd process, so neither identity nor exit is ever checked
by bare pid.

- *Linux.* At observe time, open `os.pidfd_open(pid)`, then read
  `/proc/<pid>/cmdline` and the start time (field 22 of `/proc/<pid>/stat`)
  and check that the cmdline is voxd. The pidfd pins that exact process, so
  a recycled pid cannot be reached through it. `terminate()` uses
  `signal.pidfd_send_signal(fd, SIGTERM)`, waits on the pidfd (it becomes
  readable when the process exits) for 5 s, then sends `SIGKILL` the same
  way and waits 2 s. `exited()` returns true when the pidfd is readable.
  If `os.pidfd_open` is unavailable (`AttributeError`, or `OSError` with
  `ENOSYS` on a kernel older than 5.3), the handle uses the start-time
  re-check described for macOS below.
- *macOS* (no pidfd). Record the pid, its start time (`ps -o lstart= -p`)
  and its command line. Immediately before SIGTERM and again before
  SIGKILL, re-read both and send the signal only if both still match.
  `exited()` returns true when the pid is gone, or when its start time or
  command line differs, because a reused pid is a different process and the
  old one has exited. A residual window of a few milliseconds remains
  between the re-check and `os.kill`; the platform offers no way to close
  it.
- **Why the blast radius is bounded.** `DaemonLifecycle` refuses to run as
  root (§4), so `EPERM` stops any signal to another user's process. The
  loose `\bvoxd\b` command-line match only ever runs on processes that
  `fuser`/`lsof` reported on port 8421, never on the whole process table.

`ProcessManager.kill_pid` is rewritten on top of `DaemonProcess`, so the
install and uninstall kill paths get the same protection. There is one
kill implementation, not two.

`HealthVerifier.verify()` keeps its `ServiceHealthError` contract. It polls
through `DaemonProbe.wait_up` and returns the `HealthStatus` it saw, so
install, start and restart share one poll implementation.

### 3.5 Trust boundary: what the health request sends, and to whom

The health request carries the daemon's token in its URL, so the design
pins every connection value to a trusted source:

- **Host:** from `backend.installed_bind()`, the bind written into the
  installed unit or plist, mapped by `HealthTarget._resolve_host` as today
  (wildcard or empty → `127.0.0.1`; a concrete address → that address). The
  shell's `VOXD_BIND` is never read. `HealthTarget.__new__` takes the
  backend's bind in place of reading the environment.
- **Port:** always `DEFAULT_PORT` (8421).
- **Token:** only from `serve.token` in the user's run directory. If the
  file is absent, `HealthTarget.client()` raises `DaemonNotUp` and no
  connection is made. `VoxClientSync` is never built with `token=None`,
  because that is what lets `_resolve_token` fall back to the shell's
  `VOXD_TOKEN`. The probe treats `DaemonNotUp` as "not up yet" and keeps
  polling until its deadline.
- **Gate:** a token-bearing request goes out only when a visible voxd
  process (owned by this user, cmdline matches) holds 8421. A listener vox
  cannot see, owned by another user, gets at most the token-free TCP
  connect, and the outcome is `port_conflict` or `timeout` (§4).

With these four rules in place, the sentence the round-1 draft got wrong is
now true: stop and start verify the daemon the service manager runs, and
neither `VOXD_HOST`/`VOXD_PORT`/`VOXD_TOKEN` nor `VOXD_BIND` from the
shell can redirect them or leak the token. `vox daemon install`'s
post-install check gets the same guarantees, because it goes through the
same `HealthTarget`.

### 3.6 Outcomes: `LifecycleReport`

New `src/punt_vox/service/lifecycle_report.py`:

- `LifecycleOutcome(StrEnum)`: the closed set in §4.4. Each member has
  `ok: bool`. `exit_code` is `0` when `ok` and `1` otherwise.
- `LifecycleReport`, a frozen dataclass with `verb`, `outcome`, `pid`,
  `previous_pid`, `port`, `daemon_version`, `detail` and `log_path`, and
  methods `exit_code`, `to_json()` (a `TypedDict`) and `text()`.
- `LifecycleRefused(ValueError)`: precondition refusals (running as root or
  under sudo, unsupported OS). The CLI maps it to `typer.BadParameter`,
  exit 2, as `restart` does today.

Expected failures come back as reports. Programmer errors still raise.

```json
{"verb": "stop", "outcome": "stopped", "ok": true, "pid": null,
 "previous_pid": 1471111, "port": 8421, "daemon_version": null,
 "detail": "voxd.service stopped; port 8421 free",
 "log_path": "/home/jfreeman/.punt-labs/vox/logs/vox.log"}
```

`restart --json` today emits `{"restarted": true, "pid", "port",
"daemon_version"}`. That shape is replaced, not aliased. No consumer reads
it.

## 4. Outcomes, enumerated

The observation `DaemonLifecycle` takes before acting:

```text
installed : bool                      backend.is_installed()
active    : bool                      backend.is_active()
mainpid   : int | None                backend.main_pid()
procs     : frozenset[DaemonProcess]  visible voxd processes on 8421
listening : bool                      token-free TCP connect to host:8421
serving   : HealthStatus | None       token-bearing health, gated (§3.5)
fresh     : bool                      serving.pid = mainpid
                                        ∧ serving.daemon_version = installed_version()
```

If the probe raises `PortProbeError`, every verb returns `backend_failed`
before acting. Preconditions run first for every verb: not root (otherwise
`LifecycleRefused`, exit 2) and macOS or Linux (otherwise
`LifecycleRefused`, exit 2).

### 4.1 `stop`

| # | Observation / event | Outcome | Exit |
|---|---|---|---|
| S1 | not `installed` | `not_installed`; the detail names any voxd on 8421 and says it was not touched (Q1) | 1 |
| S2 | `installed`, not `active`, `procs` empty, not `listening` | `already_stopped`; no sudo call is made | 0 |
| S3 | `request_stop()` raises `PrivilegeDenied` | `permission_denied` | 1 |
| S4 | `request_stop()` raises `BackendCommandError`, and the re-observation still shows `active` | `backend_failed`, with argv, exit code and stderr tail | 1 |
| S5 | backend inactive, every previous `DaemonProcess.exited()`, `procs` empty and not `listening`, all within `STOP_DEADLINE_S` | `stopped`, with `previous_pid` | 0 |
| S6 | backend inactive, but visible voxd processes remain on 8421 (an unmanaged voxd) | escalate: `DaemonProcess.terminate()` each one (pidfd on Linux; start-time re-check on macOS), remove `serve.port` if it names 8421, then confirm S5. Success → `stopped`, with "killed unmanaged voxd pid N" | 0 |
| S7 | backend inactive, `procs` empty, but `listening` (a holder vox cannot see, likely another user's) | `timeout`: "port 8421 still accepts connections but no visible voxd process holds it". Nothing is signalled. | 1 |
| S8 | backend still `active`, or a process survives `terminate()`, at `STOP_DEADLINE_S` | `timeout`; the detail names what is still up | 1 |

The round-1 S7 row (a kill hitting `PermissionError`) is deleted. It could
never happen: a non-root `fuser`/`lsof` cannot see another user's voxd, so
that process never reaches the kill path. The new S7 detects the case from
the port instead.

### 4.2 `start`

| # | Observation / event | Outcome | Exit |
|---|---|---|---|
| T1 | not `installed` | `not_installed`; the detail says to run `vox daemon install` | 1 |
| T4 | not `active`, and `procs` non-empty or `listening` | `port_conflict`, naming the visible pid or "a process vox cannot see"; the detail suggests `vox daemon stop` (which clears a visible one via S6) | 1 |
| T2 | `active`, a visible voxd process holds 8421, and `serving` | apply T8 and T9 to the serving process first (below). If `fresh` → `already_running`. If its version is stale → `already_running` with a stale-version detail suggesting `vox daemon restart`, exit 0 for a standalone `start`, but `version_mismatch` when reached from `restart` (§4.3). If the pid differs from `mainpid` → `backend_failed` (T8). | 0 / 1 |
| T3 | `active`, not yet `serving` (activating, or `serve.token` not yet written) | `wait_up`, then decide as T2; no answer by the deadline → `timeout` | 0 / 1 |
| T5 | `request_start()` raises `PrivilegeDenied` | `permission_denied` | 1 |
| T6 | `request_start()` raises `BackendCommandError`, and the re-observation shows not `active` | `backend_failed` | 1 |
| T7 | no health answer within `START_DEADLINE_S` (including a unit that crashes on startup and loops on `Restart=on-failure`) | `timeout`, pointing at `vox.log` | 1 |
| T8 | health answers, `serving.pid ≠ mainpid` (when the backend reports a pid) | `backend_failed`: "port 8421 answered by pid X, service manager runs pid Y" | 1 |
| T9 | health answers, `serving.daemon_version ≠ installed_version()` after a fresh start | `version_mismatch` | 1 |
| T10 | health answers and `fresh` | `started`, with pid, port and version | 0 |

Rows are checked in the order T1, T4, T2/T3, then T5–T10 for a cold start.
T4 comes before T2 so that a foreign or unmanaged listener is reported
before any token goes out (§3.5).

### 4.3 `restart` = `stop` then `start`

```text
restart():
    s = stop()
    if s.outcome not in {stopped, already_stopped}:
        return s.as_verb("restart")          # start never runs
    t = start(require_fresh=True)            # T2/T3 apply T8 and T9 and
                                             # report a stale or foreign
                                             # running daemon as
                                             # version_mismatch / backend_failed,
                                             # never already_running
    if t.outcome is already_running and t.pid != s.previous_pid:
        t = t.as_outcome(started)            # a third party started voxd
                                             # after our confirmed stop; T8
                                             # and T9 already passed inside
                                             # T2, so this process is the
                                             # manager's and runs the wheel
    if t.outcome is already_running:         # pid == previous_pid: the stop
        t = t.as_outcome(backend_failed,     # did not take effect; never ok
                         detail="stop did not take effect")
    if t.outcome is started:
        return Report(restart, restarted, pid=t.pid,
                      previous_pid=s.previous_pid, ...)
    return t.as_verb("restart")
```

`require_fresh` is the only difference between `start` and the start phase
of `restart`. Standalone, a stale running version is a warning (exit 0),
because `start`'s job, voxd being up, is done. Inside `restart`, the job is
"the wheel's code is serving", so the same observation is `version_mismatch`.
`restart` adds no stop or start logic of its own.

### 4.4 The closed outcome set

| Outcome | Verbs | `ok` | Exit |
|---|---|---|---|
| `stopped` | stop | yes | 0 |
| `already_stopped` | stop | yes | 0 |
| `started` | start | yes | 0 |
| `already_running` | start | yes | 0 |
| `restarted` | restart | yes | 0 |
| `not_installed` | all | no | 1 |
| `permission_denied` | all | no | 1 |
| `backend_failed` | all | no | 1 |
| `timeout` | all | no | 1 |
| `port_conflict` | start, restart | no | 1 |
| `version_mismatch` | start, restart | no | 1 |

`port_conflict` and `version_mismatch` go beyond the contract's list. Both
are reachable today, and each needs a different fix from the operator.

## 5. Verification against the real process

A backend command's exit code is never trusted on its own.

- **stop** succeeds only when all four hold at once: (a) the backend
  reports not active; (b) no visible voxd process holds 8421; (c) every
  `DaemonProcess` captured at observe time reports `exited()`; (d) 8421
  does not accept connections. Condition (c) uses the handle, not
  `os.kill(pid, 0)`. On Linux the pidfd identifies the original process
  exactly. On macOS a reused pid has a different start time, so it counts as
  exited rather than turning a good stop into a `timeout`. Neither path can
  raise `PermissionError`, because `ps` and the pidfd need no permission on
  the target. Condition (d) catches a holder vox cannot see.
- **start** succeeds only when a gated health request (§3.5) answers, its
  pid is the backend's main pid, and its version is the installed wheel's.
- **restart** runs `start` only after `stop` has returned `stopped` or
  `already_stopped`. Both mean (a)–(d) held, so no new process is launched
  while the old one is alive.

Deadlines are `ClassVar`s on `DaemonLifecycle`: `STOP_DEADLINE_S = 10.0`
and `START_DEADLINE_S = 10.0`, polling every 0.2 s. A poll returns the
moment its condition holds. The escalation keeps the existing 5 s SIGTERM
grace, then a 2 s SIGKILL wait.

## 6. Security properties, stated

- Root and sudo are refused, so no signal can reach another user's
  process, and no state lands under `/root`.
- A signal only goes to a process that (1) `fuser`/`lsof` reported on 8421,
  (2) has a command line that matches voxd, and (3) is the same process that
  was identified: same pidfd on Linux; same pid, start time and command line
  re-checked just before each signal on macOS.
- The daemon token goes only to the installed host on 8421, only from
  `serve.token`, and only when a visible voxd process owned by this user
  holds the port.
- sudo is invoked as `sudo -n` first, and there is no privilege probe
  (`sudo -v` and `sudo -n -l` both misreport scoped or mixed NOPASSWD
  rules, §3.3). An interactive prompt happens only when a terminal is
  present, at most once per verb.
- A tool failure (`fuser`, `lsof`, `ps`) fails the verb. It is never read
  as "nothing there".

## 7. How this leaves `service/` better

- **Cohesion.** All daemon lifecycle code (install, uninstall, stop, start,
  restart) lives in `service/`. `daemon_restarter.py` is deleted. It sat at
  package top level, mixed orchestration with `typer`, and was the only
  service code that depended on the presentation layer (PL-MD-1).
- **Coupling.** The two `reportPrivateUsage` imports of
  `_LABEL`/`_LAUNCHD_PLIST` go away, along with the second
  `LaunchctlAgent` and the duplicated `sudo systemctl start` argv.
  `HealthTarget` stops reading the environment. The bind comes from its
  owner, the backend that wrote the unit.
- **Polymorphism over conditionals.** Five platform ladders
  (`ServiceInstaller.install`, `.uninstall`, `.is_running`,
  `service.stop_daemon`, `DaemonRestarter._start`) become one
  `backend_for(plat)` dispatch plus `ServiceBackend` calls. The pidfd versus
  start-time difference is likewise one `DaemonProcess` per platform, not
  an `if` in the kill loop.
- **Silent failures removed.** `SystemdBackend.stop()` with `check=False`,
  `bootout` ignoring a failed loaded job, `find_pid_on_port` returning `[]`
  on a missing tool, and `is_vox_daemon_process` returning `False` on a
  `ps` error all become typed errors.
- **Naming.** The backend `stop()` becomes `request_stop()`, because it only
  asks the service manager. The confirmed stop is `DaemonLifecycle.stop()`.
  `ServiceInstaller.is_running()` becomes `ServiceBackend.is_active()`.

OO-ratchet effect, measured against the baselines in `.oo-baseline.json` on
this branch:

| File | Metric | Baseline | Expected |
|---|---|---|---|
| `service/__init__.py` | `max_complexity` / `avg_complexity` | 3.0 / 1.29 | `stop_daemon`, `ensure_port_free` and `is_running` wrappers removed → lower |
| `service/installer.py` | `max_complexity` / `avg_complexity` | 6.0 / 2.70 | ladders collapse onto `backend_for` → lower |
| `service/process.py` | `max_complexity` / `avg_complexity` | 9.0 / 4.33 | `kill_pid`'s two polling loops (the complexity-9 method) move into `DaemonProcess.terminate` → lower |
| `daemon_restarter.py` | (file removed) | `max_complexity` 6.0 | retired |
| `cli_daemon.py` | `max_complexity` | 3.0 | the new verbs are three-line delegations; must not rise |

`systemd.py`, `launchd.py` and `health.py` grow by real feature code
(`PrivilegedRun`, `installed_bind`, `pid()`). If a rebaseline is needed, it
covers only those `module_size` entries, each with its reason.
`.oo-coupling-baseline.json` is checked in the same commit.

There is no migration, compatibility or shim element. `stop_daemon`, the
`ensure_port_free`/`is_running` wrappers in `service/__init__`,
`DaemonRestarter`, the bare-pid `kill_pid` body, `HealthTarget`'s
environment read, and the old restart JSON keys are all deleted in the
change that replaces them.

## 8. Formal model

Decided: **yes, write a small Z model before implementation**, under
vox `CLAUDE.md` § Formal Modeling.

The lifecycle has more than three modes: not-installed, stopped, running,
activating, stopping, a port held by an unmanaged visible voxd, and a port
held by a holder vox cannot see. Vox and external actors both drive the
transitions. Invariants must hold across those transitions, and a wrong
transition has already shipped once: the launchd exit-5 race left voxd down
after a "restarted".

`docs/daemon-lifecycle.tex` would contain:

- A state schema over the §4 observation (`installed`, `active`, `mainpid`,
  `procs`, `listening`, `serving`, `fresh`), with the invariant that at most
  one voxd process serves 8421.
- `Stop`, `Start` and `Restart ≙ Stop ⨾ Start[require_fresh]`, the last
  guarded on the stop outcome.
- ProB checks, on a bounded set of 2–3 pids and two versions:
  1. Every reachable observation yields exactly one outcome per verb (§4
     is total and deterministic).
  2. `Start` is never enabled while a captured previous process has not
     exited.
  3. **The T2-inside-restart path:** `Restart` never yields `restarted`
     when the serving process is the previous process, is not the
     manager's main pid, or runs a version other than the installed wheel.
  4. No operation sends the token when `procs = ∅` (the §3.5 gate).
  5. No signal goes to a process outside `procs` on 8421.

## 9. Implementation write-set

Create:

- `src/punt_vox/service/backend.py`: `ServiceBackend`,
  `BackendCommandError`, `PrivilegeDenied`, `PortProbeError`
- `src/punt_vox/service/daemon_process.py`: `DaemonProcess` (Linux pidfd
  handle, macOS start-time handle)
- `src/punt_vox/service/daemon_probe.py`: `DaemonProbe`, `Clock`,
  `SystemClock`
- `src/punt_vox/service/lifecycle_report.py`: `LifecycleOutcome`,
  `LifecycleReport`, `LifecycleRefused`
- `src/punt_vox/service/lifecycle.py`: `DaemonLifecycle`
- `tests/_lifecycle_fakes.py`: `FakeBackend`, `FakeProbe`, `FakeClock`,
  `FakeSudo` (scripted exit codes per argv)
- `tests/test_service_backend.py`, `tests/test_service_daemon_process.py`,
  `tests/test_service_daemon_probe.py`,
  `tests/test_service_lifecycle_report.py`,
  `tests/test_service_lifecycle.py`
- `docs/daemon-lifecycle.tex` (§8, if ratified; written before the code
  mission)

Change:

- `src/punt_vox/service/systemd.py`: implement `ServiceBackend`; add
  `PrivilegedRun`; `stop()` → `request_stop()`
- `src/punt_vox/service/launchd.py`: implement `ServiceBackend`; translate
  `LaunchctlError`
- `src/punt_vox/service/launchctl.py`: `bootout()` raises on a failed
  loaded job; add `pid()`
- `src/punt_vox/service/process.py`: probes raise `PortProbeError`;
  `kill_pid` goes through `DaemonProcess`
- `src/punt_vox/service/health.py`: `HealthTarget` takes the backend bind;
  no token file → `DaemonNotUp`, no connect
- `src/punt_vox/service/health_verify.py`: poll via `DaemonProbe.wait_up`;
  return `HealthStatus`
- `src/punt_vox/service/installer.py`: `backend_for`; ladders and
  `is_running` removed; pre-flight stop through `request_stop`
- `src/punt_vox/service/__init__.py`: remove `stop_daemon`,
  `ensure_port_free`, `is_running`; export `DaemonLifecycle`
- `src/punt_vox/cli_daemon.py`: add the `stop`/`start` verbs; rewire
  `restart`
- `tests/test_service_systemd.py`, `tests/test_service_launchd.py`,
  `tests/test_service_launchctl.py`, `tests/test_service_process.py`,
  `tests/test_service_health.py`, `tests/test_service_health_verify.py`,
  `tests/test_service_installer.py`, and `tests/test_cli.py` (the restart
  tests around lines 2640–3020)
- `.oo-baseline.json`, `.oo-audit.jsonl`, `.oo-coupling-baseline.json`
- `docs/testing/manual-tests.md` line 206: add `stop`/`start`

Delete: `src/punt_vox/daemon_restarter.py`, `tests/test_daemon_restarter.py`.

Leader-authored, outside the mission: `README.md` (the `vox daemon` section
near lines 130–160) and `CHANGELOG.md`.

## 10. Test plan

### 10.1 Unit tests (tier 1: fakes, fake clock, no subprocess)

`tests/test_service_lifecycle.py` has one test per row, named by row:

- **stop:** `S1_not_installed`, `S2_already_stopped_makes_no_sudo_call`,
  `S3_permission_denied`, `S4_backend_failed`,
  `S4b_nonzero_exit_but_down_is_stopped`, `S5_stopped`,
  `S5b_previous_process_alive_after_unbind_waits`,
  `S5c_reused_pid_counts_as_exited`,
  `S6_unmanaged_voxd_terminated_then_stopped`,
  `S6b_voxd_on_other_port_named_not_signalled`,
  `S7_invisible_holder_timeout_no_signal`,
  `S8_timeout_backend_stays_active`, `S8b_timeout_process_survives`.
- **start:** `T1` through `T10`, plus `T2b_stale_version_exit_0_standalone`,
  `T2c_pid_not_mainpid_backend_failed`, `T3b_no_token_file_no_connect`,
  `T4b_invisible_holder_port_conflict_no_token_sent`.
- **restart:** `restart_never_starts_after_failed_stop`,
  `restart_starts_only_after_previous_exited`,
  `restart_from_stopped_reports_restarted`,
  `restart_third_party_start_requires_fresh`,
  `restart_running_stale_version_is_version_mismatch`,
  `restart_same_pid_after_stop_is_failure`,
  `restart_start_failure_prefixed_start`.
- **probe failure:** `probe_tool_missing_is_backend_failed`, for each verb,
  with no backend call made.
- **preconditions:** root refused and unsupported platform refused, neither
  touching the backend.

`tests/test_service_systemd.py`, `PrivilegedRun` with `FakeSudo`:

- `scoped_nopasswd_runs_once_no_probe_no_prompt`: `sudo -n systemctl stop
  voxd` exits 0. The call log has exactly that one call: no `sudo -v`, no
  `-l` probe, no interactive call.
- `exit4_permission_denied`, `exit5_not_loaded_backend_failed`.
- `exit1_no_tty_permission_denied_no_prompt`: the detail carries the stderr
  tail verbatim; the call log has no `-l` probe and no interactive call.
- `exit1_tty_interactive_once`: exactly one interactive call follows the
  `-n` call; its exit 0 returns, its exit 4 is `PrivilegeDenied`, its other
  non-zero exits re-observe state.
- `mixed_nopasswd_still_prompts`: a fake sudoers with one unrelated NOPASSWD
  entry plus a password `ALL` entry; `sudo -n` exits 1, a terminal is
  present, and the interactive call is made (the case a `-l` probe got
  wrong).
- `never_reads_stderr_text`: the same exit code with different stderr gives
  the same classification.

`tests/test_service_process.py`: `fuser` missing raises `PortProbeError`;
a timeout raises; exit 1 with empty output is empty; `ps` failure raises.

`tests/test_service_daemon_process.py`: the Linux handle signals through
the pidfd (monkeypatched `os.pidfd_open`/`signal.pidfd_send_signal`); a
cmdline mismatch at open refuses; the macOS handle skips SIGKILL when the
start time changed; `exited()` is true for a reused pid.

`tests/test_service_health.py`: the bind comes from the backend while the
shell has `VOXD_BIND=10.0.0.9` set; no token file → `DaemonNotUp` with no
`VoxClientSync` constructed while the shell has `VOXD_TOKEN` set.

`tests/test_service_lifecycle_report.py`: the outcome ↔ `ok` ↔ exit table
matches §4.4 by iterating the enum.

`tests/test_cli.py`: each verb emits `to_json()` under `--json`, exits with
`exit_code`, and maps `LifecycleRefused` to exit 2. The verb set is
`{install, uninstall, start, stop, restart, status}`.

### 10.2 Demo on pembroke (Linux, systemd), through the real entry point

After `make install`, the leader writes down each expected result, runs the
command, and pastes the output.

0. No host-specific precondition. The design is correct for any sudoers
   policy; the unit tests in §10.1 cover each path (scoped NOPASSWD, mixed
   NOPASSWD, password with a terminal, password without one). The demo
   records whichever path this host takes, read from the verb's own output. The demo does not edit sudoers. Exercising scoped
   NOPASSWD on a host that lacks it is the operator's call: a temporary
   `/etc/sudoers.d/` drop-in, added and removed by them. The unit test
   `scoped_nopasswd_runs_once_no_probe_no_prompt` covers the path either
   way.
1. `vox daemon status --json`. Expect `"status": "ok"`, pid P.
2. `vox daemon stop`. Expect `voxd stopped (was pid P)`, exit 0. Then
   `systemctl show voxd -p ActiveState -p UnitFileState` →
   `ActiveState=inactive`, `UnitFileState=enabled`; `fuser 8421/tcp` → no
   pids; `vox daemon status` → `not running`, exit 1.
3. `vox daemon stop` again. Expect `already stopped`, exit 0, no sudo
   prompt.
4. `vox daemon start --json`. Expect `"outcome": "started"`, a new pid Q,
   and `daemon_version` equal to `vox --version`.
5. `vox daemon start` again. Expect `already running (pid Q)`, exit 0.
6. `vox daemon restart --json`. Expect `"outcome": "restarted"`,
   `"previous_pid": Q`, and a `pid` R ≠ Q.
7. Invalid input: `sudo vox daemon stop`. Expect refused-as-root, exit 2,
   voxd untouched (still R).
8. Privilege from a tty-less shell: `sudo -k`, then `vox daemon stop` from
   the agent's Bash. Expect one of the two no-terminal outcomes, whichever
   this host's policy produces: `permission_denied` naming sudo, exit 1, no
   prompt, voxd still R; or, under NOPASSWD, `stopped`, followed by
   `vox daemon start`.
9. Missing dependency: `PATH=/usr/bin/nonexistent:/bin vox daemon stop`
   only if `fuser` lives outside `/bin` on pembroke (check with
   `command -v fuser` first). Expect `backend_failed` naming `fuser`. If
   `fuser` is in `/bin`, this step is covered by the unit test and the
   leader says so.
10. Boundary, unmanaged voxd: `vox daemon stop`, then a foreground
    `voxd --port 8421` in a second terminal, then `vox daemon start`.
    Expect `port_conflict` naming that pid. Then `vox daemon stop`. Expect
    `stopped` with "killed unmanaged voxd pid N". Then `vox daemon start`.
    Expect `started`.
11. Audio (the WORKFLOW demo gate): `vox say "daemon lifecycle check"`. The
    operator confirms by ear.

The cross-user holder (S7, T4 invisible) and `not_installed` are not demoed
on pembroke, because they need a second account or an uninstall of the live
service. Unit tests cover both. macOS is unit-tested; a demo there needs the
operator's Mac.

## 11. Decisions

- **Q1. `stop` when the service is not installed.** Decided:
  `not_installed`, exit 1, with no kill; the detail names any voxd on 8421.
  These are service verbs. A script running `stop` where nothing is
  installed should be told so rather than read success.
- **Q2. Should `stop` disable the unit?** Decided: **no.** `stop`
  affects the running daemon only. The unit stays `enabled` (Linux), and
  the plist stays with `RunAtLoad` (macOS). `vox daemon uninstall` is how
  you keep it down across reboots.
- **Q3. Should `stop` terminate an unmanaged voxd on 8421 (S6)?**
  Decided: **yes,** only after the backend reports down, only for
  visible processes on 8421 whose command line is voxd, and only through
  the pidfd or start-time-checked `DaemonProcess` handle (§3.4). `restart`
  does this today via `ensure_port_free`; without it `restart` regresses on
  a host with a stray foreground voxd. Processes on other ports and holders
  vox cannot see are never signalled.
- **Q4. Z model before implementation (§8), including the
  T2-inside-restart property.** Decided: yes.
- **Q5. Deadlines.** Decided: 10 s for stop and 10 s for start, up
  from today's 5 s restart poll.
