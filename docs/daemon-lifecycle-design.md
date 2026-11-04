# `vox daemon stop` and `vox daemon start` — Daemon Lifecycle Design

Bead: vox-9354. Status: design, awaiting leader review and operator
ratification. No code changes in this document's commit.

## 1. Problem

`vox daemon` exposes `install`, `uninstall`, `restart` and `status`. Nothing
brings voxd down and leaves it down, and nothing brings it up without first
bringing it down. Verifying a fresh build needs `restart`, which runs both
halves and reports one outcome for both. A maintenance window, a port
conflict on 8421, or freeing the audio device needs `kill <pid>`, which
systemd (`Restart=on-failure`) and launchd (`KeepAlive`) may undo.

## 2. The system today

These facts come from reading the worktree at `feat/vox-9354-daemon-stop-start`
and running read-only commands on this Linux host (okinos):

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

### 2.1 Where the lifecycle code lives

| Module | What it does today | Defect relevant to this bead |
|---|---|---|
| `src/punt_vox/service/systemd.py` `SystemdBackend.stop()` | `sudo systemctl stop voxd`, `check=False`, logs "Stopped" | **Silent failure.** A wrong sudo password, a sudoers refusal or a systemctl error all log "Stopped" and return. |
| `src/punt_vox/service/launchd.py` `LaunchdBackend.stop()` | `LaunchctlAgent.bootout()` when the plist exists | bootout's non-zero exit is logged at debug and ignored even when the job was loaded. |
| `src/punt_vox/service/__init__.py` `stop_daemon(plat)` | free-function `if macos / elif linux / assert_never` ladder | one of five copies of the platform ladder (see §6). |
| `src/punt_vox/daemon_restarter.py` `DaemonRestarter` | the whole restart: refuse root/Windows, stop, `ensure_port_free`, start, poll health, check version, print | **Outside `service/`.** It imports `typer`, so service orchestration depends on the presentation layer. It builds a second `LaunchctlAgent` from the private `_LABEL`/`_LAUNCHD_PLIST` (two `reportPrivateUsage` ignores). It shells out to `sudo systemctl start voxd` itself. It runs a 5 s health poll that `service/health_verify.py` also implements. |
| `src/punt_vox/service/process.py` `ProcessManager` | finds pids on the port, checks they are voxd, SIGTERM then SIGKILL, port file | reusable as-is. |
| `src/punt_vox/service/health_verify.py` `HealthVerifier` | post-install health poll pinned to the installed host/port/token | returns nothing, so a caller cannot read the pid or version it saw. |
| `src/punt_vox/cli_daemon.py` `DaemonCli` | typer verbs `install`/`uninstall`/`restart`/`status` | no `stop`/`start`. |

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
 │  (backend.py,         (daemon_probe.py)  (lifecycle_report.py)    │
 │   Protocol)            ├ ProcessManager   outcome, pid, port,     │
 │   ├ SystemdBackend     │  (pids on port)  version, detail,        │
 │   └ LaunchdBackend     └ HealthTarget     exit_code, to_json()    │
 │        └ LaunchctlAgent   (health on the                          │
 │                            installed port)                        │
 └───────────────────────────────────────────────────────────────────┘
        │ sudo systemctl …                  │ WebSocket health()
        ▼ launchctl bootout/bootstrap       ▼
     systemd / launchd  ───── runs ─────▶ voxd :8421
```

Lifecycle logic lives in `service/`, behind the existing backend seam. The
CLI holds none of it: each verb calls one `DaemonLifecycle` method, emits the
returned report through the shared `OutputFormatter`, and exits with the
report's code. The daemon verbs have one surface (CLI). There is no `mic`
tool for daemon lifecycle, because an MCP client that stops voxd cuts off its
own daemon. So the commands layer (python.md rule 5) does not apply, matching
`install`/`uninstall` today.

### 3.2 The backend seam: `ServiceBackend`

New `src/punt_vox/service/backend.py` holds a `runtime_checkable` `Protocol`
and the one error type the lifecycle catches. It lives in its own types
module per PY-IC-9.

```python
class ServiceBackend(Protocol):
    def is_installed(self) -> bool: ...      # unit file / plist present
    def is_active(self) -> bool: ...         # systemd ActiveState=active|activating|reloading
                                             # launchd: job loaded (launchctl list <label>)
    def main_pid(self) -> int | None: ...    # None: the manager reports no process
    def ensure_privilege(self) -> None: ...  # raise PrivilegeDenied
    def request_stop(self) -> None: ...      # raise BackendCommandError
    def request_start(self) -> None: ...     # raise BackendCommandError

@final
class BackendCommandError(RuntimeError):     # argv, exit code, stderr tail
@final
class PrivilegeDenied(PermissionError):      # what was refused and how to fix it
```

| Method | `SystemdBackend` | `LaunchdBackend` |
|---|---|---|
| `is_installed` | `/etc/systemd/system/voxd.service` exists | `~/Library/LaunchAgents/com.punt-labs.voxd.plist` exists |
| `is_active` | `systemctl is-active voxd` is `active`, `activating` or `reloading` | `LaunchctlAgent.is_loaded()` |
| `main_pid` | `systemctl show -p MainPID --value voxd`; `0` → `None` | `LaunchctlAgent.pid()` (new): parse `pid = N` from `launchctl print gui/<uid>/<label>`; absent → `None` |
| `ensure_privilege` | log the sudo notice, run `sudo -v`; non-zero → `PrivilegeDenied` | no-op (the GUI domain needs no sudo) |
| `request_stop` | `sudo systemctl stop voxd`, `check=True` → `BackendCommandError` | `LaunchctlAgent.bootout()`, now raising `LaunchctlError` when the job was loaded and bootout exits non-zero; translated to `BackendCommandError` |
| `request_start` | `sudo systemctl start voxd` → `BackendCommandError` | `LaunchctlAgent.start()` (bootstrap race-free, then kickstart); `LaunchctlError` → `BackendCommandError` |

`sudo -v` runs as its own step so permission failures stay separate from
service-manager failures. sudo exits 1 for a wrong password, for no tty
("a terminal is required"), and for a sudoers refusal. systemctl also exits
1 for some errors, so after the fact the two cannot be told apart. Once
`sudo -v` succeeds, the timestamp is cached and the `sudo systemctl` that
follows does not prompt again.

A backend command that exits non-zero is not a failure by itself.
`DaemonLifecycle` re-observes the state first. If the postcondition already
holds (for example, the job exited on its own between observe and bootout),
the verb continues. If not, the outcome is `backend_failed` with the
command's stderr. This is what keeps the verbs idempotent under races.

`ServiceInstaller` gets `backend_for(plat) -> ServiceBackend` as the single
place that picks a backend for a platform. Every `if plat == "macos" / elif
"linux" / assert_never` ladder in `installer.py`, `service/__init__.py` and
`daemon_restarter.py` collapses into that one dispatch (§6).

### 3.3 The probe: `DaemonProbe`

New `src/punt_vox/service/daemon_probe.py`. `DaemonProbe` composes
`ProcessManager` and `HealthTarget` and takes a `Clock` (a small Protocol
with `monotonic()` and `sleep()`), so tests poll on fake time with no real
sleeping.

- `voxd_pids(port) -> frozenset[int]`: pids on the port that pass
  `ProcessManager.is_vox_daemon_process`. Clients connected to the port are
  filtered out.
- `health() -> HealthStatus | None`: one attempt against `HealthTarget`,
  the installed host, `DEFAULT_PORT` and the `serve.token` run file. It never
  uses the shell's `VOXD_HOST`/`VOXD_PORT`/`VOXD_TOKEN`, so stop/start verify
  the daemon the service manager runs and not a remote one the shell points
  at. `None` means no answer, which is the probe's documented contract.
- `wait_down(backend, port, old_pids, deadline) -> DownResult` and
  `wait_up(backend, deadline) -> HealthStatus`: bounded polls (0.2 s
  interval) that return the moment the condition holds.

`HealthVerifier.verify()` keeps its `ServiceHealthError` contract for
`vox daemon install` and the marketplace `vox install`. Its poll loop moves
onto `DaemonProbe.wait_up`, and `verify()` now returns the `HealthStatus` it
saw. After the move, one poll implementation serves install, start and
restart. Today there are two.

### 3.4 Outcomes: `LifecycleReport`

New `src/punt_vox/service/lifecycle_report.py`:

- `LifecycleOutcome(StrEnum)`: the closed set in §4. Each member carries
  `ok: bool`. `exit_code` is `0` when `ok` and `1` otherwise.
- `LifecycleReport`, a frozen dataclass with `verb`, `outcome`, `pid`
  (current or new), `previous_pid`, `port`, `daemon_version`, `detail`, and
  `log_path`. Methods: `exit_code`, `to_json()` (a `TypedDict`) and `text()`
  (the one-line human summary).
- `LifecycleRefused(ValueError)`: precondition refusals (root/sudo, an
  unsupported OS). The CLI maps it to `typer.BadParameter`, exit 2, as
  `restart` does today. These are usage errors, not outcomes.

Expected failures are returned as reports, not raised. The CLI maps every
report the same way, with no `try/except SystemExit` per verb. Programmer
errors and broken invariants still raise.

JSON shape, the same for all three verbs:

```json
{"verb": "stop", "outcome": "stopped", "ok": true, "pid": null,
 "previous_pid": 1471111, "port": 8421, "daemon_version": null,
 "detail": "voxd.service stopped; port 8421 free",
 "log_path": "/home/jfreeman/.punt-labs/vox/logs/vox.log"}
```

`restart --json` today emits `{"restarted": true, "pid", "port",
"daemon_version"}`. That shape is replaced, not aliased. No consumer reads it
(`grep -rn '"restarted"'` over `src/`, `tests/`, `plugin/` and `README.md`
finds only the producer).

## 4. Outcomes, enumerated

The observation `DaemonLifecycle` takes before acting:

```text
installed : bool              backend.is_installed()
active    : bool              backend.is_active()
pids      : frozenset[int]    probe.voxd_pids(port)   port = serve.port or 8421,
                                                       read BEFORE any action
serving   : HealthStatus|None probe.health()
```

Preconditions run first, for every verb: the user is not root (otherwise
`LifecycleRefused`, exit 2) and the platform is macOS or Linux (otherwise
`LifecycleRefused`, exit 2).

### 4.1 `stop`

| # | Observation / event | Outcome | Exit |
|---|---|---|---|
| S1 | not `installed` | `not_installed`; the detail names any voxd pids on the port and says they were not touched (open question Q1) | 1 |
| S2 | `installed`, not `active`, `pids` empty | `already_stopped` | 0 |
| S3 | `ensure_privilege()` raises `PrivilegeDenied` (Linux only) | `permission_denied`; the detail gives the sudo reason and "run from a terminal" | 1 |
| S4 | `request_stop()` raises and the re-observation still shows `active` | `backend_failed`; the detail holds argv, exit code and stderr | 1 |
| S5 | backend goes inactive and `pids` drains within `STOP_DEADLINE_S` | `stopped`, with `previous_pid` | 0 |
| S6 | backend inactive but voxd pids remain on the port (an unmanaged voxd, for example a foreground `voxd --port 8421`) | escalate: `ProcessManager.kill_pid` on each voxd pid (SIGTERM, then SIGKILL after 5 s), remove the leftover `serve.port`, re-confirm. Success → `stopped`, with the detail noting "killed unmanaged voxd pid N" | 0 |
| S7 | S6's kill hits `PermissionError` (a voxd owned by another user) | `permission_denied`, naming the pid | 1 |
| S8 | backend still `active`, or a voxd pid survives escalation, at `STOP_DEADLINE_S` | `timeout`; the detail names what is still up | 1 |

### 4.2 `start`

| # | Observation / event | Outcome | Exit |
|---|---|---|---|
| T1 | not `installed` | `not_installed`; the detail says to run `vox daemon install` | 1 |
| T2 | `active` and `serving` | `already_running`, with pid and version. If the running version differs from the installed wheel, the detail says so and suggests `vox daemon restart`. Still exit 0. | 0 |
| T3 | `active`, not yet `serving` (still activating) | `wait_up`: answers within the deadline → `already_running`; no answer → `timeout` | 0 / 1 |
| T4 | not `active`, but `pids` non-empty or `serving` (an unmanaged voxd holds 8421) | `port_conflict`, naming the pid; the detail suggests `vox daemon stop`, which clears it via S6 | 1 |
| T5 | `ensure_privilege()` raises | `permission_denied` | 1 |
| T6 | `request_start()` raises and the re-observation shows not `active` | `backend_failed` | 1 |
| T7 | no health answer within `START_DEADLINE_S` | `timeout`, pointing at `vox.log`. A unit that crashes on startup (systemd `Type=simple` returns 0, then `Restart=on-failure` loops) lands here. | 1 |
| T8 | health answers, but `health.pid != backend.main_pid()` when the backend reports a pid | `backend_failed`: "port 8421 answered by pid X, service manager runs pid Y" | 1 |
| T9 | health answers, `daemon_version != installed_version()` | `version_mismatch`: the unit runs a different distribution than the `vox` on PATH. Same check `restart` makes today. | 1 |
| T10 | health answers, pid and version agree | `started`, with pid, port and version | 0 |

### 4.3 `restart` = `stop` then `start`

```text
restart():
    s = stop()
    if s.outcome not in {stopped, already_stopped}:
        return s.as_verb("restart")          # start never runs
    t = start()
    if t.outcome is already_running and t.pid != s.previous_pid:
        t = t.as_outcome(started)            # third-party start after our
                                             # confirmed stop; still a new
                                             # process, version checked by T9
    if t.outcome is started:
        return Report(restart, restarted, pid=t.pid,
                      previous_pid=s.previous_pid, ...)
    return t.as_verb("restart")
```

`restart` adds no stop or start logic of its own. Its only code is the
composition and the mapping into the `restarted` outcome. `restarted` exits
0. Every failure outcome above can appear on `restart`, reported from the
phase that failed (the `detail` is prefixed `stop:` or `start:`).

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

`port_conflict` and `version_mismatch` are two outcomes beyond the
contract's list. Both are reachable today and need different operator
action, so neither is folded into `backend_failed`.

## 5. Verification against the real process

The backend command's exit code is never trusted on its own. Each verb's
success condition is observed directly:

- **stop** succeeds only when all three hold at once: (a) the backend
  reports not active; (b) `voxd_pids(port)` is empty, where `port` is the
  port read from `serve.port` *before* acting, because voxd removes that
  file on shutdown; (c) each `previous_pid` fails `os.kill(pid, 0)` with
  `ProcessLookupError`. Condition (c) catches a voxd that has unbound the
  port but has not exited yet.
- **start** succeeds only when a health request on the installed
  host/port/token answers, its pid is the backend's main pid, and its
  version is the installed wheel's.
- **restart** runs `start` only after `stop` has returned `stopped` or
  `already_stopped`. Both outcomes mean (a)–(c) held, so no new process is
  launched while the old one is still alive. This is the ordering the macOS
  `LaunchctlAgent` serialises for registrations (the exit-5 bootstrap race),
  now applied to the process and the port as well.

Deadlines are `ClassVar`s on `DaemonLifecycle`: `STOP_DEADLINE_S = 10.0`
and `START_DEADLINE_S = 10.0`, polling every 0.2 s. Today's 5 s restart poll
is tight on a cold start that loads provider SDKs. 10 s bounds a hung
daemon without making a healthy one wait, because the poll returns the
moment its condition holds. The SIGKILL escalation keeps
`ProcessManager`'s existing 5 s SIGTERM grace.

## 6. How this leaves `service/` better

- **Cohesion.** All daemon lifecycle logic (install, uninstall, stop, start,
  restart) lives in `service/`. `daemon_restarter.py` is deleted. It sat at
  package top level, mixed orchestration with `typer.echo`/`typer.Exit`, and
  was the only service code that depended on the presentation layer
  (PL-MD-1).
- **Coupling.** The two `pyright: ignore[reportPrivateUsage]` imports of
  `_LABEL`/`_LAUNCHD_PLIST` go away, and so does the second `LaunchctlAgent`
  built outside `LaunchdBackend`. The duplicated `sudo systemctl start`
  argv goes too. `cli_daemon.py` loses its import of `daemon_restarter` and
  imports `service.lifecycle`.
- **Polymorphism over conditionals.** Five platform ladders
  (`ServiceInstaller.install`, `.uninstall`, `.is_running`,
  `service.stop_daemon`, `DaemonRestarter._start`) become one
  `backend_for(plat)` dispatch plus calls on `ServiceBackend`. Adding a
  third service manager means writing one new backend class, not editing
  five ladders.
- **Silent failures removed.** `SystemdBackend.stop()` with `check=False`,
  and `bootout` ignoring a non-zero exit on a loaded job, both become typed
  errors. `vox daemon install`'s pre-flight stop calls `request_stop()` only
  when `is_active()` is true, so install fails loudly when it cannot stop
  the old daemon. Today it proceeds into `ensure_port_free`.
- **Naming.** `stop()` on the backends becomes `request_stop()`, because the
  backend method only asks the manager. The confirmed stop is
  `DaemonLifecycle.stop()`. `is_running()` on `ServiceInstaller` (which
  really means "the manager reports active") is replaced by
  `ServiceBackend.is_active()`.

OO-ratchet effect. Baselines are from `.oo-baseline.json` on this branch.

| File | Metric | Baseline | Expected |
|---|---|---|---|
| `service/__init__.py` | `max_complexity` / `avg_complexity` | 3.0 / 1.29 | `stop_daemon` and `ensure_port_free` (free-function wrappers with the ladder) removed → lower |
| `service/installer.py` | `max_complexity` / `avg_complexity` | 6.0 / 2.70 | the ladders in `uninstall`/`is_running` collapse onto `backend_for` → lower |
| `daemon_restarter.py` | (file removed) | `max_complexity` 6.0 | retired debt; `_verify_health`'s 6-branch method is split across `DaemonProbe.wait_up` and the T8/T9 checks |
| `cli_daemon.py` | `max_complexity` | 3.0 | `restart` and the new verbs are three-line delegations with no `try/except SystemExit`; must not rise, and `install`/`uninstall` may adopt the same report mapping later |

`systemd.py` and `launchd.py` grow by the new protocol methods. That growth
is scoped feature substance, and each module stays far under 300 lines. A
rebaseline, if one is needed, covers only those `module_size` entries and
gives the reason. `.oo-coupling-baseline.json` is checked in the same
commit.

There is no migration, compatibility or shim element. `stop_daemon`,
`ensure_port_free` (the `service/__init__` wrapper; the `ProcessManager`
method stays), `DaemonRestarter`, `ServiceInstaller.is_running` and the old
restart JSON keys are deleted in the change that replaces them. They are
not aliased.

## 7. Formal model

Recommendation: **yes, a small Z model is needed before implementation**,
under vox `CLAUDE.md` § Formal Modeling.

The lifecycle is a stateful subsystem: not-installed, stopped, running,
plus the transient activating and stopping states and an
unmanaged-voxd-holds-the-port state. That is more than three modes, with
transitions driven by vox and by external actors (systemd restarts, launchd
`KeepAlive`, a foreground `voxd`). It meets two clauses of the trigger:

- An invariant must hold across transitions: start never acts while a
  previous voxd is alive, and at most one voxd serves 8421.
- A wrong transition silently leaves voxd down. vox has already shipped this
  once: the launchd bootstrap raced an async bootout (exit 5), and the
  "restarted" daemon was down.

The model also checks the claim this document leans on most: that §4's
tables are a **total and deterministic** function from the observed state
to exactly one outcome per verb. `docs/daemon-lifecycle.tex` would hold a
state schema (installed, active, pids, serving, plus the invariant), one
operation per row group (`Stop`, `Start`, `Restart ≙ Stop ⨾ Start` guarded
on the stop outcome), and a ProB check that every reachable observation
yields exactly one outcome and that `Start` is never enabled with a live
previous pid. It needs `fuzz -t` clean plus ProB on a bounded pid set
(2–3 pids).

## 8. Implementation write-set

Create:

- `src/punt_vox/service/backend.py`: `ServiceBackend`, `BackendCommandError`,
  `PrivilegeDenied`
- `src/punt_vox/service/daemon_probe.py`: `DaemonProbe`, `Clock`,
  `SystemClock`
- `src/punt_vox/service/lifecycle_report.py`: `LifecycleOutcome`,
  `LifecycleReport`, `LifecycleRefused`
- `src/punt_vox/service/lifecycle.py`: `DaemonLifecycle`
- `tests/_lifecycle_fakes.py`: `FakeBackend`, `FakeProbe`, `FakeClock`
- `tests/test_service_backend.py`, `tests/test_service_daemon_probe.py`,
  `tests/test_service_lifecycle_report.py`, `tests/test_service_lifecycle.py`
- `docs/daemon-lifecycle.tex` (§7, if ratified; produced before the code
  mission)

Change:

- `src/punt_vox/service/systemd.py`: implement `ServiceBackend`;
  `stop()` → `request_stop()` with `check=True`
- `src/punt_vox/service/launchd.py`: implement `ServiceBackend`; translate
  `LaunchctlError`
- `src/punt_vox/service/launchctl.py`: `bootout()` raises on a loaded-job
  failure; add `pid()`
- `src/punt_vox/service/installer.py`: `backend_for`; drop the ladders and
  `is_running`; the pre-flight stop goes through `request_stop`
- `src/punt_vox/service/health_verify.py`: poll via `DaemonProbe.wait_up`;
  return `HealthStatus`
- `src/punt_vox/service/__init__.py`: remove `stop_daemon`,
  `ensure_port_free`, `is_running`; export `DaemonLifecycle`
- `src/punt_vox/cli_daemon.py`: add `stop`/`start` verbs; rewire `restart`;
  register both in `build_daemon_app`
- `tests/test_service_systemd.py`, `tests/test_service_launchd.py`,
  `tests/test_service_launchctl.py`, `tests/test_service_installer.py`,
  `tests/test_service_health_verify.py`, and `tests/test_cli.py` (the restart
  tests around lines 2640–3020 patch `service.stop_daemon` and
  `service.ensure_port_free`; they are rewritten against the lifecycle)
- `.oo-baseline.json`, `.oo-audit.jsonl`, `.oo-coupling-baseline.json`
- `docs/testing/manual-tests.md`: line 206 names `vox daemon restart` as
  the only service control; add `stop`/`start`

Delete: `src/punt_vox/daemon_restarter.py`, `tests/test_daemon_restarter.py`.

Leader-authored, outside the mission: `README.md` (the `vox daemon` section
near lines 130–160) and `CHANGELOG.md`.

## 9. Test plan

### 9.1 Unit tests (tier 1, fake backends, fake clock, no subprocess)

`tests/test_service_lifecycle.py` drives `DaemonLifecycle` with
`FakeBackend` (scripted `is_installed`/`is_active`/`main_pid`, commands that
succeed, raise, or succeed with no effect, and a recorded call log),
`FakeProbe` (scripted pid sets and health replies per poll tick) and
`FakeClock`. There is one test per table row, named for its outcome:

- stop: `S1_not_installed`, `S2_already_stopped`, `S3_permission_denied`,
  `S4_backend_failed`, `S4b_nonzero_exit_but_down_is_stopped`,
  `S5_stopped`, `S6_unmanaged_voxd_killed_then_stopped`,
  `S7_unmanaged_voxd_permission_denied`, `S8_timeout_backend_stays_active`,
  `S8b_timeout_pid_survives_kill`, `S5b_previous_pid_alive_after_unbind_waits`
- start: `T1` through `T10` as named in §4.2, plus
  `T2b_already_running_stale_version_exit_0`
- restart: `restart_never_starts_after_failed_stop` (call log has no
  `request_start`), `restart_starts_only_when_pids_empty` (the fake asserts
  `pids == ∅` at the moment `request_start` is called),
  `restart_from_stopped_reports_restarted`,
  `restart_third_party_start_counts_when_pid_new`,
  `restart_start_failure_prefixed_start`
- preconditions: root refused, unsupported platform refused, neither
  touches the backend

`tests/test_service_lifecycle_report.py` checks that the outcome ↔ `ok` ↔
exit-code table matches §4.4 exactly (an iteration over the enum, so a new
member without a row fails), and checks `to_json()` keys and `text()`.

Backend tests patch `subprocess.run`: the systemd argv for stop, start,
`is-active`, `show -p MainPID`, `sudo -v`; non-zero exit → typed error;
`MainPID=0` → `None`. launchd: `bootout` raising on a loaded-job failure,
`pid()` parsing (present, absent, malformed raises), and `LaunchctlError` →
`BackendCommandError`.

`tests/test_cli.py`: each of `stop`/`start`/`restart` with the lifecycle
stubbed to every outcome emits `to_json()` under `--json`, exits with
`exit_code`, and turns `LifecycleRefused` into exit 2. The verb set of
`build_daemon_app` is `{install, uninstall, start, stop, restart, status}`.

### 9.2 Demo on okinos (Linux, systemd), through the real entry point

After `make install`, the leader writes down each expected result, runs the
command, and pastes the output.

1. `vox daemon status --json`. Expect `"status": "ok"`, pid P.
2. `vox daemon stop`. Expect `voxd stopped (was pid P)`, exit 0. Then
   `systemctl show voxd -p ActiveState -p UnitFileState` →
   `ActiveState=inactive`, `UnitFileState=enabled` (stop does not disable);
   `fuser 8421/tcp` → no pids, exit 1; `vox daemon status` → `not running`,
   exit 1.
3. `vox daemon stop` again. Expect `already stopped`, exit 0, and no sudo
   prompt (S2 decides before the privilege step).
4. `vox daemon start --json`. Expect `"outcome": "started"`, a new pid Q,
   and `daemon_version` equal to `vox --version`.
5. `vox daemon start` again. Expect `already running (pid Q)`, exit 0.
6. `vox daemon restart --json`. Expect `"outcome": "restarted"`,
   `"previous_pid": Q`, and a `pid` R ≠ Q.
7. Invalid input: `sudo vox daemon stop`. Expect refused-as-root, exit 2,
   voxd untouched (status still R).
8. Missing privilege: `sudo -k`, then `vox daemon stop` from the agent's
   tty-less Bash. Expect `permission_denied` naming sudo, exit 1, and voxd
   still running (status still R).
9. Boundary, unmanaged voxd: `vox daemon stop`, then launch a foreground
   `voxd --port 8421` in a second terminal, then `vox daemon start`. Expect
   `port_conflict` naming that pid, exit 1. Then `vox daemon stop`. Expect
   `stopped` with "killed unmanaged voxd pid N". Then `vox daemon start`.
   Expect `started`.
10. Audio (the WORKFLOW demo gate): `vox say "daemon lifecycle check"`. The
    operator confirms by ear that the restarted daemon speaks.

`not_installed` is not demoed on okinos, because it would need
`vox daemon uninstall` on the operator's live service. T1/S1 unit tests
cover it. The operator may choose to run it as step 11 on a scratch host.
macOS (launchd) is unit-tested; a demo there needs the operator's Mac.

## 10. Open questions for the operator

- **Q1. `stop` when the service is not installed.** Recommendation: outcome
  `not_installed`, exit 1. Do not kill any voxd found on the port; the
  detail names it. Reason: these are service verbs, and killing an
  unmanaged process on a host with no service is outside their scope. A
  script that runs `stop` where nothing is installed should learn that
  rather than read success. Alternative: exit 0, treating "not installed
  and nothing running" as already stopped.
- **Q2. Should `stop` disable the unit?** Recommendation: **no.** `stop` is
  runtime-only. On Linux the unit stays `enabled` and comes back at boot. On
  macOS the plist stays in `~/Library/LaunchAgents` and `RunAtLoad` brings
  it back at the next login. Staying down across reboots is what
  `vox daemon uninstall` is for. This matches `systemctl stop` and
  `launchctl bootout`, which is what operators expect.
- **Q3. Should `stop` kill an unmanaged voxd on the port (S6)?**
  Recommendation: **yes, only after the backend reports down**, and only
  pids that pass the voxd identity check. Reason: `restart` does this today
  via `ensure_port_free`. Without it, `restart = stop ⨾ start` would regress
  on a host with a stray foreground voxd. This is also the only way
  `port_conflict`'s suggested fix works.
- **Q4. Z model before implementation (§7).** Recommendation: yes. A short
  design follow-up (or the leader) writes `docs/daemon-lifecycle.tex`,
  fuzz-clean and ProB-checked, before the implementation mission dispatches.
- **Q5. Deadlines.** Recommendation: 10 s for stop and 10 s for start (§5),
  up from today's 5 s restart poll.
