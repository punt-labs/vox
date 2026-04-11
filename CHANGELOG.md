# Changelog

All notable changes to punt-vox will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **`vox doctor --json` rows now include `status_kind` field (vox-kl7)**: each check row carries `status_kind` with values `"pass"`, `"warn"`, `"fail"`, or `"skip"` so machine consumers can distinguish warnings from hard failures. The existing `passed` boolean is unchanged.

### Fixed

- **Qualified "world-readable" and "/proc" references in comments (vox-t2f)**: replaced bare "world-readable" with "others-readable (mode & 0o004)" in `service.py` keys.env race comments, and added "(Linux-specific; macOS has no /proc)" to the `/proc` reference in `voxd.py`, for cross-location consistency with PR #175's broader phrasing.

## [4.3.2] - 2026-04-11

### Fixed

- **voxd `_ws_route` logged a full ERROR traceback on every chime (vox-ewh)**: after the vox-ehf fix in 4.3.0, chime/unmute clients return on the `"playing"` ack and close the WebSocket while voxd's receive loop is still awaiting the next `receive_text()`. The trailing `contextlib.suppress(WebSocketDisconnect, RuntimeError)` sends of the stale `"done"` message inside `_handle_synthesize` and `_handle_chime` land on the peer-closed socket, transition Starlette's `application_state` to `DISCONNECTED`, and swallow the resulting `WebSocketDisconnect(1006)`. The next `receive_text()` in the outer loop then observes `application_state != CONNECTED` and raises `RuntimeError('WebSocket is not connected. Need to call "accept" first.')` — not `WebSocketDisconnect` — so the narrow `except WebSocketDisconnect:` branch missed it and the loop fell through to `except Exception: logger.exception("WebSocket error")`, emitting a multi-line traceback on every `/recap`, every stop-hook chime, and every prompt chime. On the reporter's box this was filling the journal with hundreds of spurious error entries and burying real failures in the same unit slot. Fix preempts the RuntimeError at its source: `_ws_route` now checks `websocket.application_state` at the top of the receive loop and `break`s cleanly when it is no longer `WebSocketState.CONNECTED`, so `receive_text()` is never called against a disconnected socket. The outer `except WebSocketDisconnect:` clause stays exactly as narrow as it was pre-fix — the `except Exception` branch still catches any genuine unexpected `RuntimeError` from `receive_text`, `json.loads`, or a handler, preserving real error visibility. Two regression tests in `tests/test_voxd.py::TestWsRoutePeerClose` lock in the narrowing: one drives `_ws_route` with a fake WebSocket whose `application_state == DISCONNECTED` and asserts the loop breaks without calling `receive_text` and without logging a `"WebSocket error"` record; the complement drives a fake WebSocket whose `application_state == CONNECTED` but whose `receive_text` raises an unrelated `RuntimeError`, and asserts exactly one `"WebSocket error"` record is logged — documenting that the fix catches only the peer-closed-state case and nothing else. Audio playback, client acks, and dedup were all unaffected — this is purely log-spam cleanup. Closes vox-ewh.

## [4.3.1] - 2026-04-11

### Fixed

- **Stale user-level `vox.service` crash-looping on legacy `vox serve` entrypoint (vox-45r)**: an earlier install layout registered `~/.config/systemd/user/vox.service` with `ExecStart=.../vox serve --port 8421`. The `serve` subcommand was removed during the voxd/voxd.service split — later installs did not clean up the user-level file, and systemd's `Restart=on-failure` respawned the unit every 5 seconds against a CLI that exits with `No such command 'serve'`. Observed in the field at restart counter 107,069 over a 9-day boot window (~12 restarts/minute), filling the journal with hundreds of thousands of spurious lines and obscuring real failures in the same unit slot. The currently-running daemon is the system-level `voxd.service` at `/etc/systemd/system/voxd.service`; the user-level file is pure legacy. Fix is three parts: (1) `vox daemon install` (and `vox install`, which wraps it) now detects the stale user unit on Linux and removes it via `systemctl --user disable --now vox.service`, unlink, `systemctl --user daemon-reload` — idempotent, user-writable, no sudo, no-op on machines that never had the legacy unit. The cleanup runs before `_systemd_stop` / `_ensure_port_free` so a recovering install clears the crash-looping unit first. Scope is strictly `~/.config/systemd/user/vox.service`; the system-level `voxd.service` is never touched. (2) `vox doctor` gains a regression check that parses `ExecStart=` in the user unit (if present) and fails loudly when the referenced subcommand is not in the current CLI command set. Remediation hint points at `vox install` (which now cleans up automatically) and a manual `systemctl --user disable --now vox.service && rm ~/.config/systemd/user/vox.service && systemctl --user daemon-reload` recipe. Linux-only; macOS (no `systemctl --user`) is gated out. (3) Unit tests lock in the cleanup sequence (exact subprocess argv, `check=False` on both `systemctl` calls, file removal, return value), the platform gate, the scope guard against touching `voxd`/`sudo`/`/etc/systemd`, and the install-time ordering (`cleanup_stale_user_unit → systemd_stop → ensure_port_free`). Doctor-side tests cover stale-subcommand-fails, current-subcommand-passes, file-absent-passes, non-Linux-skipped, unparseable-ExecStart-fails. Closes vox-45r.

## [4.3.0] - 2026-04-09

### Added

- **`vox daemon restart` subcommand**: cycle the running `voxd` daemon via the service manager without hand-running `systemctl` or `launchctl`. Refuses to run as root (sudo is invoked internally for the two service-manager calls only), detects macOS vs Linux, drives `_launchd_stop`/`_systemd_stop` + `_ensure_port_free` for a clean shutdown, starts the daemon via `sudo systemctl start voxd` or `sudo launchctl load -w ... && sudo launchctl kickstart -k system/com.punt-labs.voxd`, and polls the authenticated health endpoint (5s window, 200ms interval) until the new process is confirmed up. On success, prints the new pid and port. On failure, exits 1 and points at `~/.punt-labs/vox/logs/voxd.log`. This is the intended command after `uv tool upgrade punt-vox` — a plain upgrade replaces the wheel but leaves the long-running daemon untouched, so changes to daemon behavior do not take effect until the service is cycled.
- **Per-call provider API key on `vox unmute`, four input paths**: scope a single synthesis call to a specific provider API key, forwarded to `voxd` over the local WebSocket and injected into the provider's environment for the duration of one synthesize request. Motivation is single-user multi-key billing attribution — one user holding multiple ElevenLabs or OpenAI keys for separate billing projects, not multi-tenant isolation (vox remains a single-user tool). The key is never persisted to `keys.env`, never written to logs, never echoed to stdout (including `--json` mode), and never visible to concurrent requests on the same daemon. Four input paths are supported and are mutually exclusive:
  1. **`VOX_API_KEY` env var** (recommended for scripting): typer reads it natively via `envvar="VOX_API_KEY"`. On Linux, `/proc/<pid>/environ` is owner-only; macOS differs (see README), but env vars are still materially harder to snoop than argv.
  2. **`--api-key-file <path>`** (recommended for stored keys): reads the key from a file, strips trailing whitespace/newlines. Rejects missing paths and empty files via `typer.BadParameter`. Warns (but does not fail) when the file is world-readable (mode `& 0o004`), suggesting `chmod 600`. Intended layout: `~/.config/vox/key_<project>.txt` at mode 0600.
  3. **`--api-key-stdin`** (recommended for password managers): reads one line from stdin, strips whitespace. Refuses to read from a tty so a forgotten pipe fails loudly. Intended usage: `pass show vox/proj | vox unmute ... --api-key-stdin`.
  4. **`--api-key <value>`** (kept for back-compat and demo): direct CLI flag. Still accepted, but now emits a stderr warning whenever the value came from argv (distinguished from `VOX_API_KEY` via `ctx.get_parameter_source("api_key") == ParameterSource.COMMANDLINE`). The warning text points at the three safer paths. The env-var path does not warn. Empty strings are rejected via `typer.BadParameter` rather than silently falling back to the `keys.env` default.

  Prompted by Cursor Automation security review of PR #175, which flagged `--api-key` on the command line as a practical credential disclosure path via `ps`, `/proc/*/cmdline`, shell history, and terminal recordings — a real concern for the exact billing-isolation scripting scenario the feature targets. Closes vox-a3e (`voxd` already parsed `api_key` from the WebSocket message; this adds the CLI surface, safer input paths, and the end-to-end integration test that exercises the full chain).

### Changed

- **`vox doctor` reports daemon version and warns on wheel/daemon mismatch**: doctor now reads the running daemon's version from the authenticated WebSocket health payload and compares it against `importlib.metadata.version("punt-vox")` — the wheel installed on disk. Matching versions produce the existing green checkmark with the version appended (`✓ Daemon: running on port 8421 (provider: elevenlabs, version 4.2.0)`). Mismatched versions produce a yellow `⚠ Daemon: running on port 8421 (version 4.1.1 — wheel has 4.2.0, run 'vox daemon restart' to refresh)`. The refresh hint intentionally omits `sudo`: `vox daemon restart` refuses to run as root and invokes sudo internally only for the service-manager calls that require it, so a literal copy-paste of the hint works as your normal user. Exit code stays 0 — the daemon is still functional, just out of date — but the warning counter increments and the `--json` payload carries a `warned` field for machine consumption. Pre-version daemons (pre-commit 2 builds that lack `daemon_version` in their health response) fall back to the existing PASS message so older daemons do not falsely trip the warning.
- **voxd health WebSocket response includes `daemon_version` and `pid`**: the authenticated full health payload (WebSocket handler, not the unauthenticated HTTP `/health` route) now carries `daemon_version` from `importlib.metadata.version("punt-vox")` and `pid` from `os.getpid()`. Both are cached or computed at startup — no per-request metadata scan. The unauthenticated HTTP `/health` route is deliberately unchanged: leaking a running version to anonymous callers is a fingerprinting aid for targeted exploitation, and `pid` is a diagnostic-only detail. `vox doctor` uses `daemon_version` for the mismatch warning above; `vox daemon restart` uses `pid` to confirm the daemon came back up as a fresh process.

### Fixed

- **`unmute` and `chime` now return after enqueueing for playback, not after playback (vox-ehf)**: `VoxClient.synthesize()` and `VoxClient.chime()` previously waited for voxd's `"done"` message, which arrives only after the audio finishes playing. On long texts with slow ElevenLabs synthesis the combined synthesis + playback duration exceeded the 30-second `_TIMEOUT_SYNTHESIS` budget, causing `/recap` timeouts. Both methods now return when voxd sends `"playing"` (audio synthesized and queued), letting playback continue independently in voxd's queue worker. Dedup short-circuits (which send `"done"` directly with no `"playing"`) continue to terminate correctly. voxd's `_handle_synthesize` and `_handle_chime` now suppress the stale `"done"` send that fires after a client has already closed the connection on `"playing"`. Closes vox-ehf.

- **Stale voxd survived release-day smoke tests (vox-nmb)**: during the v4.2.0 end-to-end verification of the `--once` flag, a stale voxd daemon that had been running since 2026-04-07 — 20 hours before the new code was merged — silently accepted every new `once` field in synthesize messages, ignored it, and played every request. `vox doctor` reported "Daemon: running" because it only checked reachability, not version alignment between the wheel on disk and the running process. The stale daemon was caught only when a human noticed dedup was not working at all. Fix has three parts: (1) `vox daemon restart` subcommand so the correct upgrade flow is discoverable, (2) `daemon_version` in the health payload so doctor has something to compare against, (3) doctor warning on wheel/daemon mismatch so smoke tests fail loudly instead of producing a false-positive pass. Closes vox-nmb.
- **Per-call API key passthrough had no CLI surface and no integration test (vox-a3e)**: voxd has known how to parse `api_key` from the synthesize WebSocket message since PR #152 and already knew how to inject it into `os.environ[ELEVENLABS_API_KEY]` / `os.environ[OPENAI_API_KEY]` under `_env_lock` for the duration of one request. But no CLI flag exposed the capability, and no integration test verified the end-to-end flow, so the feature was effectively dead code. Adds `vox unmute --api-key` (see Added above) and a new `TestApiKeyPassthroughIntegration` suite in `tests/test_voxd.py` that drives the real Starlette app via `starlette.testclient.TestClient`, opens a real WebSocket, sends record messages with different keys, and verifies that a stub provider sees exactly the key the caller sent, with no cross-call leakage, and that the ambient environment is restored after each call. Four scenarios: single-call key, two sequential calls with different keys (the billing-isolation invariant), `api_key=None` fallback to the ambient environment, and ambient-key restoration after a per-call override. Closes vox-a3e.
- **Per-call `api_key` now bypasses the synthesis cache (CodeQL `py/weak-sensitive-data-hashing`)**: an earlier draft of this PR added `api_key` to the cache-key digest (first as MD5, then as SHA-256) to prevent billing-scope collisions on cache hits. CodeQL correctly flagged the SHA-256 variant under `py/weak-sensitive-data-hashing`: the taint analysis classifies `api_key` as password-class material, and any regular cryptographic hash (MD5, SHA-1, SHA-256, SHA-384, SHA-512, SHA-3, even HMAC variants) is inappropriate for hashing that material. The rule wants a password KDF (Argon2, scrypt, bcrypt, PBKDF2 with high iteration counts), none of which are acceptable for a cache-filename computation — Argon2 alone would add >100 ms per call. Arguing with the linter is a losing battle. The principled fix is **cache bypass**: the `api_key` parameter is removed from `cache.py` entirely, and `voxd._synthesize_to_file` now gates both the `cache_get` lookup and the `cache_put` store on `api_key is None`. Per-call billing scopes synthesize every time; anonymous calls keep the unchanged MD5 cache that is byte-identical to pre-v4.2.1 so existing on-disk entries remain reachable after upgrade. The mixed-scope caching the earlier draft allowed was a latent correctness hazard regardless: a per-call billing scope that accepts cached bytes from another scope is violating the whole point of the isolation. Scripts that want cache hits for repeated quips should use `keys.env` (the anonymous path); scripts that want billing attribution should accept that every call re-synthesizes. As a byproduct of adding the anti-poison test, a latent bug in `_handle_record` was also fixed — the record handler unconditionally `unlink`ed the path returned from `_synthesize_to_file`, which silently deleted the cache entry on every anonymous cache hit. The handler now only unlinks tempfiles (paths that are NOT inside `CACHE_DIR`).

## [4.2.0] - 2026-04-08

### Added

- **`vox unmute --once <seconds>`**: new CLI flag that forwards a per-call dedup TTL to voxd. When set, voxd will skip a synthesize+play of the same text if an identical text was already played within the window. The motivating use case is `biff wall` broadcasts: N Claude Code sessions in the same repo independently shell out to `vox unmute` on the same broadcast text, and the user should hear the announcement exactly once. Without the flag, identical requests play every time — there is no default dedup for speech. The flag takes a positive integer (seconds); the biff wall integration passes `--once 600` for a 10-minute window that comfortably covers cross-session delivery jitter. Closes vox-0e9 on the vox side. The biff side follows in a separate PR against `punt-labs/biff` coordinated on the biff message channel.

### Changed

- **Speech dedup is now opt-in via `once`**: the legacy `AudioDedup` class unconditionally deduped every synthesize request within a 5-second window, keyed on `(text, voice, provider)`. The always-on behavior was removed — callers that want dedup must set the `once: <ttl_seconds>` field on the synthesize/direct_play WebSocket message (or pass `--once <seconds>` on the CLI). Without `once`, every request plays. The new `OnceDedup` class replaces the old one with a per-call TTL, a hash keyed on `md5(text)` only (so identical text with different voices or providers collapses), and an observable `DedupHit` result so callers can log "wall skipped, already played 53s ago". `ChimeDedup` (renamed from the old `AudioDedup` for the chime path) keeps the existing 5-second always-on behavior — chimes are event markers and always deduped, speech is not. Breaking change for any caller that relied on the silent always-on dedup for identical speech; in practice the hook handlers (`vox hook signal/notification/stop`) do not produce cross-session or rapid intra-session duplicates, so this change is safe for them.
- **`VoxClient.synthesize()` returns `SynthesizeResult`**: the async and sync client `synthesize()` methods previously returned a bare `str` request id. They now return a `SynthesizeResult` dataclass carrying `request_id: str`, `deduped: bool`, `original_played_at: float | None`, and `ttl_seconds_remaining: float | None`. Existing callers that only read the request id update to `.request_id`; new callers can check `.deduped` to surface observable dedup status. Internal callers (`server.py`, `__main__.py`, `hooks.py`) updated.

- **ElevenLabs default model reverted to `eleven_v3`**: The previous default `eleven_flash_v2_5` was chosen for low latency and lower cost, but `eleven_v3` is the only ElevenLabs model that interprets bracket-style expressive tags (`[excited]`, `[weary]`, `[sighs]`) — which the `/vibe` feature is built around. Using a non-expressive default silently broke `/vibe` for every user who never set `TTS_MODEL` explicitly: tags were prepended to the synthesis text and rendered as the literal words "excited", "weary", "sighs" instead of as performance cues. Reverting to `eleven_v3` makes the headline `/vibe` feature work out of the box. Users who want the lower cost or latency of `eleven_flash_v2_5` can still override via `TTS_MODEL=eleven_flash_v2_5`. The deeper fix (vibe tag stripping for non-expressive providers/models) is documented under Fixed below.

### Fixed

- **Vibe tags spoken as literal words on non-expressive models (vox-fhl)**: when `voxd` synthesized text with `vibe_tags` set, it unconditionally prepended the tag string to the normalized text on both the synthesize and direct-play paths without checking whether the active provider+model interprets bracket-style tags as performance cues. Any provider or model that does not interpret tags (Polly, OpenAI, macOS `say`, Linux `espeak-ng`, and every ElevenLabs model except `eleven_v3`) spoke the literal words — `[serious]` was read aloud as "serious", `[weary]` as "weary", and so on. The capability information already existed on the `TTSProvider` protocol as `supports_expressive_tags`; the gating just was not consulted at the prepend site, and `apply_vibe` in `resolve.py` had the right shape but was dead code in production (defined and tested, never imported by any synthesis path). A new `ElevenLabsProvider.model_supports_expressive_tags` classmethod does a pure lookup against `_EXPRESSIVE_MODELS = {"eleven_v3"}` without instantiating the SDK. A new `split_leading_expressive_tags(text) -> (tags, body)` helper in `resolve.py` pulls leading bracket tags off the raw input before normalization — crucial because `normalize_for_speech` strips brackets as part of its punctuation pass, so any tag fed in after normalization has already been converted to a literal word. Both voxd synthesis paths now call a shared `_apply_vibe_for_synthesis(raw_text, vibe_tags, provider, model)` helper that (a) splits leading tags from raw text, (b) normalizes only the body, (c) re-attaches vibe tags only when the active provider+model supports them, dropping them entirely otherwise. Lazy ElevenLabs import inside the helper keeps espeak-only and say-only systems from pulling the ElevenLabs SDK at voxd module load. Regression tests exercise the full production call path (`split → normalize → gate`) instead of the helper in isolation, so future changes to the order of operations fail loudly. Closes vox-fhl. (#170)
- **Watcher notification consumer throttled on first event on fresh systems (vox-2sj)**: the consumer closure returned by `make_notification_consumer()` in `src/punt_vox/watcher.py` used a throttle check of the form `last = last_fired.get(event.signal, 0.0); if now - last < throttle_seconds: return` with `now = time.monotonic()`. On Linux, `time.monotonic()` returns `CLOCK_MONOTONIC` — seconds since boot. On freshly-booted CI runners, that value is small (typically 5-30 seconds at test start). With the default sentinel of `0.0` and a throttle window of 100 s, the first event for any signal computed `now - 0.0 ≈ 10`, `10 < 100`, throttle fired, and the consumer returned without calling `_announce_voice`. Mock call counts ended up at 0 instead of 1 and the test failed. The bug was invisible on macOS and persistent Linux dev boxes because their `monotonic()` values are in the thousands or millions (uptime in hours, days, or months), so the throttle never fired on the first call — it only manifested on GitHub Actions ephemeral Ubuntu runners where uptime at test start is smaller than the throttle window. A 2026-02-28 workaround in PR #45 had added `pytest.mark.skipif(CI=true)` decorators on the two failing tests; the decorators hid the tests from CI for five weeks on the incorrect hypothesis that Ubuntu CI read `config.notify` differently (the regex in `config.py` is platform-independent, so that hypothesis was provably false). Fixed by changing the sentinel from `0.0` to `None` and gating the throttle check on `last is not None`. New regression test `test_first_call_fires_when_monotonic_below_throttle_window` mocks `time.monotonic` to return `5.0` and asserts the first event still fires, reproducing the CI condition deterministically on any host. The `skipif` decorators were removed in the same PR so both tests now run on every platform. Closes vox-2sj. (#168)

## [4.1.1] - 2026-04-07

### Documentation

- **README setup walkthrough for cloud providers**: added a `Configure providers` section between Quick Start and Features. Covers acquiring API keys (ElevenLabs, OpenAI, AWS Polly) with signup and free-tier details, editing `~/.punt-labs/vox/keys.env` with a normal editor (no sudo), restarting the daemon via `systemctl`/`launchctl` to apply changes, and verifying with `vox doctor` + `vox unmute`. AWS Polly section documents both the `AWS_PROFILE` path (recommended for users who already use the AWS CLI) and raw `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` credentials. Slimmed the Environment Variables section to cross-reference Configure providers instead of duplicating the edit/restart instructions.

### Fixed

- **`vox daemon install` no longer requires `sudo` for everything**: install runs as the user. Per-user state under `~/.punt-labs/vox/` is created with normal user permissions — no chown, no fchown, no symlink defenses. Sudo escalation is scoped to three subprocess calls per platform on a fresh install (place the unit/plist via `install(1)`, register with the service manager, start the daemon), growing to four on macOS and five on Linux when upgrading a previously-installed service (the extra calls stop the old daemon via the service manager so launchd's `KeepAlive=true` and systemd's `Restart=on-failure` cannot respawn it mid-upgrade). Eliminates the entire class of symlink-attack and chown-ordering bugs that existed when the entire install ran as root inside a user-controlled directory. See DES-029 for the full rationale.
- **`vox daemon install` upgrades left the old voxd running with stale ExecStart**: the first iteration of the new install path used `systemctl enable --now voxd` on Linux and `launchctl load` on macOS. Both are no-ops when the service is already running, so on upgrade from an older install the previous voxd kept its stale binary/args baked in until the next reboot. Fixed by switching Linux to `systemctl enable` + `systemctl restart` (unconditional cycle) and adding `launchctl kickstart -k system/com.punt-labs.voxd` after `load` on macOS. Cursor Bugbot 3048294138 / Copilot 3048295072 on PR #162.
- **`vox daemon install` upgrade raced against the service manager**: `install()` called `_ensure_port_free` (which issues a direct `os.kill(SIGTERM)` to the stale voxd PID) before running the platform-specific install path. On macOS, launchd's `KeepAlive=true` immediately respawned the killed daemon with the OLD plist; on Linux, systemd's `Restart=on-failure` treated the kill as a failure exit and restarted the process under the old unit. By the time the new unit write + restart sequence ran, the service manager had already resurrected the old binary. Fixed by adding `_launchd_stop()` and `_systemd_stop()` pre-flight helpers that tell the service manager to stop the daemon (via `sudo launchctl unload -w` / `sudo systemctl stop voxd`) BEFORE `_ensure_port_free` runs. Both are idempotent — fresh installs with no prior unit file skip the sudo call entirely. The Linux sudo count goes from 4 to 5; the macOS sudo count stays at 4 because the redundant inline `unload -w` inside `_launchd_install` was removed (the pre-flight stop now owns the unload). Cursor Bugbot 3048416720 on PR #162.
- **`vox daemon install` silently broke when run under `sudo`**: the new install path runs as the invoking user, but offered no guard against a user who ran `sudo vox daemon install` out of habit. In that case `getpass.getuser()` returned `root`, `Path.home()` resolved to `/root`, all state landed under `/root/.punt-labs/vox/`, and the generated systemd unit had `User=root` so the daemon lost audio device access. `install()` now refuses to run with `os.geteuid() == 0` and emits a clear error directing the user to re-run without sudo. Copilot 3048295090 on PR #162.
- **`vox daemon install` crashed when an existing `keys.env` was unreadable**: `_write_keys_env` called `keys_path.read_text()` with no error handling, so a corrupted file (non-UTF-8 bytes, permission denied, not-a-regular-file) would abort the whole install with a stack trace. The read is now wrapped in a `try`/`except (OSError, UnicodeDecodeError)` that logs a warning and overwrites the file fresh from env values at install time. Copilot 3048295101 on PR #162.
- **`keys.env` was world-readable for a brief window during install**: `_write_keys_env` used `Path.write_text` + `Path.chmod(0o600)`, which creates the file via `open(..., "w")` — that produces mode `0o666 & ~umask` (typically `0o644` on a `0022` umask) and only chmods afterward. The file was world-readable for the few instructions between create and chmod, a real API-key exposure window. Fixed by opening the file with `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` so the mode is set at create time. A post-write `os.chmod(0o600)` remains as belt-and-suspenders for unusual umasks. Copilot 3048402515 on PR #162.
- **`_write_keys_env` did not tighten the parent state dir permissions**: if the state dir was pre-created at an umask-widened mode (for example 0755 from an older version of vox, or by hand), a secrets file inside a world-traversable directory let other local users read the dir listing and mount further attacks. `_write_keys_env` now calls `parent.mkdir(mode=0o700)` and `parent.chmod(0o700)` on every invocation so the helper is self-contained regardless of whether `install()` has already run `ensure_user_dirs`. Copilot 3048402424 on PR #162.
- **`_voxd_exec_args` accepted non-executable files and directories**: `Path.exists()` returns True for directories, symlink loops, and non-executable regular files, so a broken `voxd` at `sys.executable.parent` would pass validation and get baked into `ExecStart=` — the service would then fail at runtime with an opaque systemd error. Fixed by probing `Path.is_file()` + `os.access(..., os.X_OK)` and raising `SystemExit` with a clear message before the install proceeds. Copilot 3048402463 on PR #162.
- **README contradicted itself on sudo-free key management**: the doc said "you never need sudo again to manage your keys" but then instructed users to run `sudo systemctl restart voxd` / `sudo launchctl kickstart` to apply key changes. Both statements were true in different senses (editing `keys.env` is sudo-free; restarting the daemon requires sudo for `systemctl`/`launchctl`), but the phrasing blurred them together. Rewrote the section to separate "edit the file — no sudo" from "restart the daemon to apply — requires sudo" with an explicit heading on the restart step. Copilot 3048402487 on PR #162.
- **voxd state dirs left at loose permissions on upgrade**: `_configure_logging` and `_read_or_create_token` created `~/.punt-labs/vox/{logs,run}` with `Path.mkdir(exist_ok=True)`, which respects the process umask — on most shells (umask `0022`) pre-existing directories stayed at `0755` and could leak spoken-text logs, auth tokens, and cached synthesis output to other local users. `voxd.main()` now calls `paths.ensure_user_dirs()` at startup, which chmods every subdir (`logs`, `run`, `cache`, root) to `0700` regardless of prior mode. Copilot finding 3048101870 on PR #162.
- **voxd state location regression**: PR #130 (the v3 architecture rewrite) moved per-user voxd state from `~/.punt-labs/vox/` to FHS system paths (`/etc/vox/`, `/var/log/vox/`, `/var/run/vox/`). This stranded existing users' API keys silently on upgrade and required sudo to edit personal API tokens. State is now back in `~/.punt-labs/vox/`. Users who had cloud provider keys configured before v3 (commit 49879af) will see them work again automatically — the keys were never deleted, voxd just stopped reading from the right location.
- **Stale `voxd` binary in systemd unit**: `daemon install` resolved `voxd` via `shutil.which()` and could bake a stale binary from an earlier `uv tool install` into the systemd `ExecStart=`. Now resolves from `Path(sys.executable).parent / "voxd"` so the unit always runs the same distribution that provides `vox`.

### Changed

- **Dead cross-user path helpers removed from `punt_vox.paths`**: `user_state_dir_for(user)` and `installing_user()` existed to support the old sudo-aware resolution (mapping `$SUDO_USER` to the target home dir at install time). Now that install runs as the invoking user, both helpers are dead code and have been deleted along with their tests. Cursor Bugbot 3048294140 on PR #162.
- **Path helpers extracted to `punt_vox.paths`**: voxd, service, and client all share one source of truth for per-user state paths. Removed the duplicated `_data_root()`/`_config_dir()`/`_log_dir()`/`_run_dir()` helpers from those modules. The new module is stdlib-only so both the heavy voxd import chain and the minimal client can depend on it.
- **systemd unit no longer sets `RuntimeDirectory=vox`**: runtime state lives in `$HOME/.punt-labs/vox/run/` now, so systemd does not need to create `/run/vox`.
- **State directories tightened to mode 0700**: `~/.punt-labs/vox/` and all four subdirectories (`logs`, `run`, `cache`, root) now use mode 0700, same policy as `~/.ssh` and `~/.gnupg`. Previously `logs/` and `cache/` inherited the process umask and could be world-readable on systems with a permissive default, which would leak spoken text, operational details, and cached synthesis output to other local users.

### Security

- **Smaller privileged surface in `vox daemon install`**: the install command no longer escalates to root for any per-user file operation. By running the entire per-user portion as the invoking user, the attacker-controlled `~/.punt-labs/` tree is written under normal kernel permission checks and the privileged code path shrinks to three `sudo` subprocess calls per platform on a fresh install (place the service file via `install(1)`, register with the service manager, start the daemon), growing to four on macOS and five on Linux when upgrading a previously-installed service. The extra upgrade calls are a pre-flight stop through the service manager so launchd's `KeepAlive=true` and systemd's `Restart=on-failure` cannot respawn the old daemon mid-upgrade. Eliminates the whole class of symlink/TOCTOU/chown-ordering attacks that required defensive code (`O_NOFOLLOW`, `O_EXCL`, `lchown`, `fchown`, parent-symlink rejection, fd-based fstat verification) in the old root-inside-$HOME design. `install()` also refuses to run with `os.geteuid() == 0` so the sudo-habit user gets an explicit error instead of silently landing state under `/root/`. Keys.env still rejects control characters (`\n`, `\r`, `\x00`) in provider values to prevent env-var smuggling — that's input sanitization, not a privilege defense, and still applies.

## [4.1.0] - 2026-04-06

### Added

- **Direct-play path for local TTS providers**: `espeak-ng` and macOS `say` now implement an optional `DirectPlayProvider` protocol, spawning their binary without the `-w`/`-o` flag and playing straight to the default audio device -- the same syscall and audio session a user's shell would use. Cloud providers (ElevenLabs, OpenAI, Polly) keep using the existing synthesize-cache-enqueue pipeline, so MP3 caching and dedup replay still work. This eliminates the WAV -> ffmpeg -> MP3 -> ffplay round-trip for local synthesis and removes an entire class of audio-session negotiation bugs on Linux. Direct-play and queued playback share a single `_playback_mutex`, so two concurrent clients can never produce overlapping audio.

### Fixed

- **voxd playback observability**: playback was fire-and-forget with player stderr piped to `DEVNULL`, making silent failures impossible to diagnose remotely. `_play_audio` now captures the spawn command, audio env vars at call time (`XDG_RUNTIME_DIR`, `PULSE_SERVER`, `DBUS_SESSION_BUS_ADDRESS`, etc.), exit code, elapsed wall time, file size, and full stderr (truncated to 2 KB with head + tail kept). Logs `ERROR` on non-zero exit or spawn failure, `WARNING` on suspicious sub-50ms "success", `INFO` with stderr summary on normal success. Voxd startup logs its full process environment (pid, uid, gid, cwd, binary, audio env) so operators can verify systemd env injection without poking at `/proc`. Synthesis now fails fast on 0-byte output -- the broken file is deleted, the cache is not poisoned, and the client gets an error response. The token-authenticated `health` WebSocket message exposes `audio_env`, `player_binary`, and `last_playback` so `vox doctor` surfaces playback state; the unauthenticated HTTP `/health` route returns only the minimal public status fields and never leaks environment variables or player stderr.
- **Silent playback on Linux**: voxd systemd unit lacked `XDG_RUNTIME_DIR`, so ffplay couldn't reach PulseAudio/PipeWire for audio output. Now captures audio session env vars at install time. Also adds `RuntimeDirectory=vox` so `/var/run/vox` is created automatically at service start.

## [4.0.3] - 2026-04-06

### Fixed

- **espeak-ng VoiceNotFoundError on Linux**: espeak provider crashed with `VoiceNotFoundError: en` on systems where espeak-ng only has qualified voice variants (`en-us`, `en-gb`) but no bare `en`. Voice resolution now registers bare ISO 639-1 fallback keys and `default_voice` discovers what's actually installed instead of assuming a hardcoded voice exists. Same fix applied to the macOS `say` provider for `samantha`.

## [4.0.2] - 2026-04-02

### Fixed

- **Symbol mispronunciation**: parentheses, brackets, and other non-speech symbols are now stripped before TTS synthesis — only prosody punctuation (`.` `,` `?` `!` `:` `;`) is preserved (#150)
- **Linux install failure**: `sudo vox daemon install` wrote root-owned `__pycache__` into user's uv tools directory, causing subsequent `uv tool install` to fail with Permission denied. Fixed with `PYTHONDONTWRITEBYTECODE=1` and cleanup step (#149)

### Changed

- Add `punt-labs/team` git submodule at `.punt-labs/ethos/` for agent definitions and identity data (#149)

## [4.0.1] - 2026-04-01

### Fixed

- **Stop hook hang**: fire-and-forget chime in Stop hook prevents 5s+ hang when voxd is slow or unreachable (#143)
- **Acronym mispronunciation**: TTS engines no longer pronounce OCR as "ocker" or MCP as "mick-pee" — ALL_CAPS acronyms (2-5 chars) are letter-spaced unless in a ~280-entry pronounceable-words allowlist (#144)
- **State persistence**: notify/speak/vibe session state now persists to disk, surviving MCP server restart (#142)

### Security

- Bump Pygments to 2.20.0 in lockfile — ReDoS CVE (#141)
- Bump punt-lux to 0.15.1, fastmcp to 3.2.0+ in lockfile — CVE-2026-32871, CVE-2026-27124 (#139)
- Bump PyJWT in lockfile — security fix (#138)

### Changed

- Track `.envrc` in version control; user overrides go in `.envrc.local` (gitignored) (#140)
- Add Skill() allow entries via punt auto settings (#137)

## [4.0.0] - 2026-03-29

## [3.0.0] - 2026-03-29

### Changed

- **BREAKING:** New `voxd` audio server daemon replaces the old `daemon.py`. Pure audio server — synthesizes text and plays through speakers. Knows nothing about MCP, hooks, projects, or Claude Code.
- **BREAKING:** System-level service install. macOS: `/Library/LaunchDaemons/` (sudo required). Linux: `/etc/systemd/system/` (sudo required). Daemon data in Homebrew prefix (macOS) or FHS paths (Linux), not `~/.punt-labs/vox/`.
- **BREAKING:** `mcp-proxy` eliminated. MCP server runs as direct stdio process (`vox mcp`). Plugin.json simplified.
- MCP server is now a thin client of `voxd`. Session state in memory, not `.vox/config.md`. No provider imports — cold start target < 500ms.
- Hook handlers call `voxd` via WebSocket client for audio instead of in-process synthesis.
- WebSocket protocol between clients and `voxd` — streaming-capable for future real-time voice.

### Added

- `voxd` binary entry point (`punt_vox.voxd:main`) — audio daemon with playback queue, dedup, caching.
- `punt_vox.client` — lightweight WebSocket client library (`VoxClient` async, `VoxClientSync` sync wrapper).

### Removed

- `daemon.py` — replaced by `voxd.py`
- `proxy.py` — mcp-proxy eliminated
- `ephemeral.py` — no project-directory writes from daemon
- `_config_path_override` ContextVar — daemon has no session/config concept
- PID-based CWD resolution via `lsof` / `/proc` — eliminated
- `playback.py` flock/pending/subprocess queue — daemon owns playback

## [2.0.0] - 2026-03-29

### Changed

- **BREAKING:** Data directory migrated from `~/.punt-vox/` to `~/.punt-labs/vox/` per org filesystem standard. Clean break — old directory is not read or migrated. Re-run `vox daemon install` after upgrade.
- Auth token is now stable across daemon restarts. Generated once at install time, persisted to `serve.token`, reused on daemon startup. Enables mcp-proxy reconnection without session restart.
- Daemon service config uses `vox` shim path (via `shutil.which`) instead of `sys.executable`. Survives venv recreation by uv.
- `TTS_MODEL` now persisted to `keys.env` alongside provider API keys.
- AWS credential check (`_has_aws_credentials()`) no longer cached with `lru_cache` — expired session tokens are detected correctly in long-running daemon.
- Hook scripts log errors to `~/.punt-labs/vox/logs/hook-errors.log` instead of `/dev/null`.
- `install.sh` now installs `mcp-proxy` after daemon setup for fast hook relay.
- Plugin MCP server command uses `-s` (non-empty) file checks instead of `-f` for token/port files.

### Fixed

- `vox daemon install` now unloads the existing launchd plist before loading the new one, preventing `launchctl load` I/O errors on upgrades. Same fix for systemd: stops the service before writing the new unit.
- `vox daemon install` now creates parent directory before writing token file, fixing `FileNotFoundError` on fresh installs.
- `vox daemon install` reuses existing auth token instead of always generating a new one, preventing session breakage during upgrades.
- Daemon validates auth token on startup — empty or unreadable token files produce actionable `SystemExit` messages instead of silent auth bypass.
- Hook logging initialized via `configure_logging()` in CLI hook entry point.
- `httpx` logger suppressed (noisy at INFO from OpenAI SDK).
- `install.sh` mcp-proxy step uses `python` instead of `python3` for `uv tool run` (python3 not guaranteed by `uv python install`).

## [1.11.0] - 2026-03-28

### Added

- Daemon provider key resolution via `~/.punt-vox/keys.env` — `vox daemon install` snapshots API keys (ELEVENLABS_API_KEY, OPENAI_API_KEY, AWS_*) from the caller's environment into a chmod 0600 config file; the daemon loads it at startup before provider auto-detection
- `install.sh` now runs `vox daemon install` as part of installation, with graceful fallback

## [1.10.3] - 2026-03-28

### Fixed

- Daemon identity check (`_is_vox_daemon_process`) now matches all invocation patterns: `punt_vox` (underscore), `punt-vox` (hyphen in uv tool path), and bare `vox serve` binary
- Daemon launchd plist and systemd unit now embed the user's `PATH` at install time so ffmpeg and other tools in `/opt/homebrew/bin` or `/usr/local/bin` are found
- `python -m punt_vox` now works — added missing `if __name__ == "__main__"` guard to `__main__.py`, which caused the launchd-launched daemon to exit silently

## [1.10.2] - 2026-03-28

### Fixed

- Chime audio now plays in daemon and installed modes — assets are bundled inside the Python package (`src/punt_vox/assets/`) so `_resolve_assets_dir()` resolves correctly when `CLAUDE_PLUGIN_ROOT` is not set
- `vox daemon uninstall` now kills the running daemon process instead of only removing the launchd/systemd config
- `vox daemon install` detects and kills stale daemon processes occupying the port before installing

## [1.10.1] - 2026-03-20

### Changed

- Session-start hook now auto-allows Skill permissions (`Skill(unmute)`, `Skill(mute)`, `Skill(recap)`, `Skill(vibe)`, `Skill(vox)`) alongside MCP tool globs, matching the beadle PLUGIN_RULES pattern
- Session-start hook JSON output uses `jq -n --arg` instead of raw heredoc interpolation, preventing malformed JSON from special characters in action messages
- Legacy MCP pattern removal now cleans up temp files on failure

### Removed

- `commands/ask-test-dev.md` — dev test artifact for AskUserQuestion; finding documented in DES-022

## [1.10.0] - 2026-03-14

### Added

- Daemon mode (`vox serve`): single long-running process serving MCP-over-WebSocket and hook relay, fronted by mcp-proxy for sub-10ms session startup and hook dispatch
- Audio deduplication: prevents duplicate playback when multiple sessions receive the same notification (e.g. biff wall)
- Service management (`vox daemon install/uninstall/status`): registers launchd (macOS) or systemd (Linux) service for auto-start at login
- mcp-proxy integration: plugin.json falls back to `vox mcp` (stdio) when mcp-proxy is unavailable
- Hook scripts use daemon relay (~15ms) with subprocess fallback (~500ms)
- `vox install` now installs mcp-proxy binary and registers daemon service
- `vox doctor` checks mcp-proxy and daemon status

## [1.9.1] - 2026-03-13

### Fixed

- Flaky hook tests: tests that mock `subprocess.run` now also mock `cache_get` to prevent cache hits from bypassing the mocked call path
- README install.sh SHA checksum was stale after v1.9.0 release (57334a4→40c3769)

## [1.9.0] - 2026-03-13

### Added

- MP3 caching for quip phrases: hook speech is cached by (text, voice, provider) in `~/.punt-vox/cache/`, eliminating redundant TTS API calls and reducing latency on repeated quips
- `vox cache status` and `vox cache clear` CLI commands for cache management
- Text normalization for natural speech: snake_case, camelCase, and programmer abbreviations (stderr, stdout, eof, etc.) are converted to spoken English before synthesis

## [1.8.0] - 2026-03-12

### Added

- `/unmute` voice picker: shows AskUserQuestion dialog with featured voices when no argument is given (providers with 2+ voices only)
- `/ask-test-dev` command for testing AskUserQuestion inside command execution

### Changed

- Hook output: `speak` with voice shows "sarah at the mic" instead of generic "voice on"
- Hook output: `who` shows "25 agents standing by" instead of "25 voices checked in"

### Fixed

- Strip leading expressive tags (e.g. `[serious]`) from text when the provider does not support them, preventing literal bracket words in speech
- Prune `vibe_signals` to the most recent 20 entries, preventing unbounded growth in long sessions

## [1.7.0] - 2026-03-12

### Added

- `show_vox` MCP tool to display status widget in Lux display window (notifications, voice, vibe, engine)
- `applet.py` module for Lux element tree construction and display server connection
- `punt-lux` as optional dependency (`uv add punt-vox[lux]`)

## [1.6.0] - 2026-03-10

## [1.5.0] - 2026-03-10

### Added

- Technical architecture specification (`docs/architecture.tex`) — 15-page
  LaTeX document covering provider architecture, audio pipeline, hook
  integration, security model, and known limitations
- Chime mappings and voice phrases for `git-commit` and `pr-created` signals
- `All checks passed` pattern to lint-pass signal detection

### Fixed

- MCP server now uses worktree-safe `resolve_config_path()` instead of
  hardcoded CWD-relative path — voice mode no longer silently fails in
  git worktrees
- Signal classification unified: watcher delegates to
  `hooks.classify_signal()` instead of maintaining a separate pattern table
- `tests-pass` pattern tightened from bare `passed` to `[0-9]+ passed` to
  prevent false positives on prose text

### Removed

- `remove_ephemeral_dir()` from `ephemeral.py` — dead code that would have
  destroyed session config via `shutil.rmtree(.vox/)`
- Dead `voice_enabled` field from `VoxConfig` and `ALLOWED_CONFIG_KEYS`

## [1.4.1] - 2026-03-10

### Fixed

- **Hook stdin hang** — `_read_hook_input()` used blocking
  `sys.stdin.read()` which hangs when Claude Code does not close the
  pipe. Replaced with non-blocking `os.read()` in a `select` loop.
  Also removed unnecessary stdin drain calls from 5 handlers that
  never used the data. See DES-027.

## [1.4.0] - 2026-03-09

### Added

- **Continuous mode hooks**: UserPromptSubmit acknowledgment, SubagentStart/Stop announcements, SessionEnd farewell speech — all fire only in continuous mode (`/vox c`) except SessionEnd which fires whenever notify != off
- Centralized quip registry (`quips.py`) for all hook speech phrases — localization and theming ready
- Shared `_speak_phrase()` helper eliminates duplication across continuous-mode hook handlers

## [1.3.0] - 2026-03-09

## [1.2.4] - 2026-03-08

### Changed

- Switch default ElevenLabs model from `eleven_v3` to `eleven_flash_v2_5` (~75ms latency, 40k char limit)
- Correct `eleven_v3` per-request character limit to 5,000

### Added

- Mid-session model switching via `/vox model <name>` (shorthands: `v3`, `flash`, `turbo`, `multilingual`)
- Mid-session provider switching via `/vox provider <name>` (`elevenlabs`, `openai`, `polly`, `say`, `espeak`)
- `provider` and `model` fields in `.vox/config.md` session config
- Expressive tags (`[excited]`, `[warm]`, etc.) are now model-aware — only applied on `eleven_v3`

### Fixed

- Config model no longer leaks across incompatible providers (e.g. ElevenLabs model passed to OpenAI)

## [1.2.3] - 2026-03-08

### Fixed

- `vox install` used wrong plugin ID (`tts@punt-labs` instead of `vox@punt-labs`)
- `install.sh` pinned to stale version 1.2.0 instead of current release

## [1.2.2] - 2026-03-08

### Added

- `/mute` replies with a random personality phrase instead of silent confirmation (#83)
- PreCompact hook plays a playful "be right back" message before context compaction in continuous mode (#83)
- Distinct `"compact"` chime signal for PreCompact (distinguishable from task-complete `"done"`)

### Fixed

- Global `--json` flag now placed before subcommand for correct typer parsing in hook subprocess calls
- PreCompact CLI command drains stdin to prevent pipe backpressure
- Z spec partition test coverage for notify/speak state machine (#82)

## [1.2.1] - 2026-03-06

## [1.2.1] - 2026-03-07

### Fixed

- Clean stop hook reason — no internal data leak (#75)
- Align CLI with punt-kit standards (#76)
- Address Copilot review feedback from PR #76 (#77)
- Remove `[skip ci]` from release-plugin.sh (suppressed tag-triggered releases)

### Changed

- Add Makefile per makefile.md standard (#78)

## [1.2.0] - 2026-03-05

### Added

- Per-segment `vibe_tags` in `unmute` and `record` — each segment can now specify its own expressive tags, overriding the session default (same pattern as `voice` and `language`)

### Changed

- `unmute` MCP tool is now non-blocking — synthesis and playback run in a background thread; tool returns predicted metadata immediately
- `record` MCP tool is now non-blocking — returns the predicted file path immediately; synthesis completes in background
- CLI `status` now includes `vibe_tags` and `vibe_signals` in both text and `--json` output (parity with MCP `status` tool)

## [1.1.1] - 2026-03-05

### Fixed

- suppress-output hook now formats `notify`, `speak`, and `status` MCP tool output as compact panel lines instead of dumping raw JSON

## [1.1.0] - 2026-03-05

### Added

- `notify` MCP tool: set notification mode (y/n/c) and session voice without Bash
- `speak` MCP tool: toggle spoken notifications (y/n) without Bash
- `status` MCP tool: query current vox state (provider, voice, notify, vibe) without Bash

### Changed

- All slash commands (`/vox`, `/unmute`, `/mute`, `/vibe`) now use MCP tools exclusively — no Bash, Read, or Write tool permissions needed (DES-017)
- Stop hook block reason now embeds vibe context (mode, mood, signals) so the model never needs to Read `.vox/config.md`

## [1.0.3] - 2026-03-05

### Fixed

- SessionStart hook now updates stale commands on plugin upgrade — previously deployed commands in `~/.claude/commands/` were never refreshed, leaving users with old allowed-tools, old MCP tool names, and prompt-driven logic instead of CLI calls

## [1.0.2] - 2026-03-05

### Fixed

- Provider auto-detection now prefers AWS Polly over macOS `say` and Linux `espeak` when AWS credentials are valid — Polly produces significantly better audio than local fallbacks
- Session voice gracefully falls back to provider default when the configured voice isn't available in the current provider (e.g. ElevenLabs voice "sarah" stored in config but Polly is the active provider)
- Suppressed elevenlabs SDK pydantic v1 `UserWarning` on Python 3.14+ — their upstream issue, filtered at runtime and in test config

## [1.0.1] - 2026-03-05

### Changed

- CLI: replaced `vox on`/`vox off`/`vox mute` with `vox notify y|n|c`, `vox speak y|n`, and `vox voice <name>` — aligns CLI with the standard that every slash command has a corresponding CLI command
- Slash commands `/vox`, `/unmute`, `/mute` now delegate to CLI via Bash instead of manually editing `.vox/config.md` with Read/Edit tools
- Slash commands no longer request `Edit`, `Read`, or `Write` tool permissions — only `Bash` and MCP tools

## [1.0.0] - 2026-03-05

### Fixed

- `/unmute` now sets `notify: "c"` (continuous mode) so spoken notifications actually fire — previously only set `speak: "y"` while `notify` defaulted to `"n"`, silently disabling all notifications
- `/mute` now specifies exact config file location and create-if-missing behavior, matching other config-writing commands
- Hooks resolve `.vox/config.md` via `git rev-parse --git-common-dir` so config is found from worktrees
- All config-writing commands now specify exact `.vox/config.md` location and create-if-missing behavior, preventing agents from searching other directories
- Config path resolution now catches `subprocess.TimeoutExpired` (was `TimeoutError`, which subprocess.run never raises)
- Hook chime resolution uses `CLAUDE_PLUGIN_ROOT` env var for asset paths, fixing chime playback for pip-installed packages where `__file__` resolves into site-packages
- `notify-permission.sh` called non-existent `vox synthesize` — now uses `vox unmute` via the Python hook dispatcher
- Signal classifier now checks lint patterns before test patterns, fixing false matches where "Found N errors" was classified as `tests-fail` instead of `lint-fail`
- Signal classifier uses `re.MULTILINE` so `^` anchors match line starts in multi-line bash output (matches original bash `grep` behavior)
- Chime filename resolution normalizes signal hyphens to underscores (`tests-pass` → `chime_tests_pass.mp3`)
- Checkmark pattern in signal classifier uses literal `✓` instead of raw `\u2713` which was never interpreted as Unicode

### Changed

- Merged `/vox-on` and `/vox-off` into a single `/vox` slash command with `y` (enable), `n` (disable), or `c` (continuous) argument
- `/vox y` and `/vox c` now preserve existing `speak` setting on subsequent calls; only first init defaults `speak` to `"y"`
- Migrated hook business logic from bash to Python via `vox hook <event>` CLI dispatcher — stop, post-bash, and notification hooks are now thin shell gates delegating to testable pure functions in `hooks.py`
- Deleted `hooks/state.sh` — all config reading, mood classification, chime resolution, and audio helpers now use their Python equivalents

## [0.11.0] - 2026-03-04

### Added

- **Mic API**: new MCP server key `mic` with four tools: `unmute` (synthesize + play), `record` (synthesize + save), `vibe` (session mood), `who` (voice roster)
- Both `unmute` and `record` accept a uniform `segments` list — callers no longer need different tools for different cardinalities
- CLI product commands: `vox unmute`, `vox record`, `vox vibe`, `vox on`/`off`, `vox mute`, `vox version`, `vox status`
- Slash commands: `/unmute [@voice]`, `/mute`, `/vox on`, `/vox off`
- Shared modules: `resolve.py` (voice/language/output resolution, vibe application), `voices.py` (blurbs, excuses), `config.py` write API
- Vibe-driven chime notifications: chimes now reflect session mood (bright/neutral/dark) via pitch-shifted variants (±3 semitones)
- Mood classification module (`mood.py`): maps free-form vibe strings to bright/neutral/dark tonal families
- Mood-aware chime resolution: `resolve_chime_path(signal, mood=)` with four-level fallback chain
- Per-signal chime assets: distinct sounds for tests-pass, tests-fail, lint-pass, lint-fail, git-push-ok, and merge-conflict (chime mode only)
- Signal-aware `resolve_chime_path(signal)` with automatic fallback to `chime_done.mp3`
- Generation script `scripts/generate_chimes.py` for reproducible chime synthesis with mood variants

### Changed

- MCP server key from `vox` to `mic` (`plugin:vox:mic`)
- Hook matchers updated from `_vox__` to `_mic__` patterns
- Session-start hook auto-migrates old permission patterns and cleans retired commands

### Removed

- MCP tools: `speak`, `chorus`, `duet`, `ensemble`, `set_config`, `list_voices`
- CLI commands: `synthesize`, `synthesize-batch`, `synthesize-pair`, `synthesize-pair-batch`
- Slash commands: `/say`, `/speak`, `/notify`, `/voice`

## [0.10.1] - 2026-03-03

### Fixed

- Plugin name on release tags is now `vox` (was `vox-dev` — release script was not run before v0.10.0 tag)

## [0.10.0] - 2026-03-03

First release as **punt-vox**. The PyPI package name changed from `punt-tts` to `punt-vox`, the CLI binary changed from `tts` to `vox`, and all internal paths and namespaces follow suit. No functional changes — this is a pure rename release.

### Changed

- Rename env var `TTS_OUTPUT_DIR` → `VOX_OUTPUT_DIR`
- Rename default output dir `~/tts-output` → `~/vox-output`
- Rename ephemeral dir `.tts/` → `.vox/` (config, audio)
- Rename log/state dir `~/.punt-tts/` → `~/.punt-vox/` (logs, playback lock, pending queue)
- Release workflow installs `punt-vox` and verifies `vox --help` (was `punt-tts`/`tts`)
- `install.sh` installs `punt-vox` package with `vox` binary (was `punt-tts`/`tts`)
- Rename plugin name `tts-dev`/`tts` → `vox-dev`/`vox` (plugin.json, hooks, commands, settings)
- Plugin MCP tool namespace `mcp__plugin_tts_vox__*` → `mcp__plugin_vox_vox__*`
- Session-start hook cleans up all legacy `mcp__plugin_tts*` permission patterns
- Hook scripts use `vox` CLI binary (was `tts`)
- All documentation updated: README.md, CLAUDE.md, DESIGN.md, prfaq.tex — `punt-tts`/`tts` → `punt-vox`/`vox`

### Fixed

- `release-plugin.sh` no longer fails when no `-dev` commands exist — name swap proceeds with a warning instead of aborting
- `restore-dev-plugin.sh` no longer fails when `.claude/commands/` directory doesn't exist at the release commit
- PostToolUse hook matcher now fires for standalone MCP server registrations (`mcp__vox__*`) in addition to plugin-namespaced names

## [0.9.0] - 2026-02-28

### Added

- `list_voices` MCP tool: browse available voices for the current provider with curated personality blurbs, shuffled featured list (capped at 6), and full voice roster
- `/voice` bare invocation: displays featured voices with blurbs and prompts user to pick with `/voice <name>`
- `list_voices` panel handler in suppress-output hook: displays voice count or "here's who's around"

### Fixed

- Permission notification hook now uses the active session voice instead of always defaulting to matilda

## [0.8.1] - 2026-02-28

### Fixed

- eSpeak provider now passes language codes (e.g. `en-us`) to `espeak-ng -v` instead of display names (e.g. `English_(America)`) which espeak-ng rejects
- Installer uses `doctor || true` so diagnostic failures don't abort the install script under `set -eu`

## [0.8.0] - 2026-02-28

### Added

- Per-session voice selection: `/voice <name>` sets a default voice for all speak/chorus/duet/ensemble calls. Stored in `.tts/config.md` as `voice` field. Use `/voice clear` to revert to provider default.
- Session event watcher: daemon thread in MCP server tails the session transcript and announces milestones (tests passed, lint clean, code pushed) in real-time when `notify=c`. Uses pattern matching on bash tool output, per-signal throttle, and voice/chime modes.
- `vibe_tags` parameter on `speak` and `chorus` tools: applies expressive tags and clears `vibe_signals` in one step, replacing the separate `set_config` call in the stop-hook flow.
- Friendly voice-not-found errors: when a voice can't be resolved, providers raise `VoiceNotFoundError` with structured data. MCP tool handlers catch it and return a playful message (e.g. "bob stepped out for a coffee. How about matilda, aria, charlie?") instead of a raw traceback.

### Fixed

- Audio no longer clips at the end: 150ms trailing silence appended to all output files (single, stitched, and batch) to prevent MP3 frame truncation.

### Changed

- macOS Say provider default voice changed from Fred to Samantha.
- Stop hook now signal-gated: only fires when `vibe_signals` is non-empty (real work happened). Prevents empty recaps after trivial commands like `/say hello`.
- Signal accumulation decoupled from `vibe_mode`: signals accumulate in all modes (auto, manual, off), not just auto. Required for stop hook gating.
- New signal types: `git-commit` (commit created) and `pr-created` (pull request opened).

## [0.7.1] - 2026-02-27

### Added

- Batch `set_config` mode: pass `updates` dict to write multiple config fields in a single atomic read-write ([#33](https://github.com/punt-labs/vox/pull/33))

### Fixed

- Vibe tags (`[excited]`, `[weary]`, etc.) are now only prepended when the provider supports expressive tags (ElevenLabs). Other providers (Polly, OpenAI, say, espeak) no longer speak bracketed tag text literally ([#39](https://github.com/punt-labs/vox/pull/39))
- Speak hook output now uses gendered pronouns: "matilda said her piece" instead of "matilda said the piece" ([#31](https://github.com/punt-labs/vox/pull/31))
- `install.sh` now uses uninstall+install instead of `claude plugin update` which did not reliably pick up new versions
- `tts doctor` MCP check now says "Claude Desktop MCP" (not "MCP server") and suggests the correct command (`tts install-desktop`)

## [0.7.0] - 2026-02-27

### Added

- macOS `say` command fallback provider: zero-config, offline TTS when no API keys are configured. Uses Fred voice (the iconic 1984 Mac voice) to nudge users toward configuring a real provider
- Linux `espeak-ng` fallback provider: zero-config, offline TTS using espeak-ng speech synthesizer. Auto-detected when espeak-ng is installed and no API keys are set
- Auto-detection now falls back to system TTS on both platforms: `say` on macOS, `espeak` on Linux (when installed). Final fallback remains `polly`
- `--provider say` and `--provider espeak` flags for explicit use of system voices
- Installer and `tts doctor` now check for espeak-ng on Linux and show install hints when no API keys are configured

## [0.6.1] - 2026-02-27

### Fixed

- Stop hook no longer leaks `vibe_mode` debug data in user-visible hook error display; vibe data now read from `.tts/config.md` via Read tool ([#26](https://github.com/punt-labs/vox/pull/26))
- `_apply_vibe()` skips vibe tag prepend when text already starts with an expression tag, preventing `[calm] [calm] ...` doubling ([#27](https://github.com/punt-labs/vox/pull/27))
- Voice name now appears in speak tool panel output; `suppress-output.sh` unwraps the `{"result": "..."}` wrapper Claude Code adds to MCP tool responses
- `install.sh` now detects already-installed plugin and runs `claude plugin update` instead of silently no-oping ([#28](https://github.com/punt-labs/vox/pull/28))
- TestPyPI verification in release CI uses `--refresh` to bust stale uv index cache between retry attempts ([#29](https://github.com/punt-labs/vox/pull/29))

## [0.6.0] - 2026-02-27

### Added

- `/vibe` command with three modes: `auto` (default — detects session mood from signals), `manual` (`/vibe <mood>` — user-specified), and `off`
- Auto-vibe: PostToolUse hook on Bash accumulates session signals (test pass/fail, lint, git ops) and stop-hook continuation passes them to Claude for expressive tag selection
- `set_config` MCP tool: writes plugin config fields atomically, replacing Read/Write/Edit file-tool pattern for all config mutations
- Panel display for vibe shifts: `♪ vibe shifted to [weary]` on config writes
- Shell linting with shellcheck in quality gates and CI

### Changed

- `/vibe`, `/notify`, `/speak`, `/voice` commands now use `set_config` MCP tool instead of Read/Write/Edit file tools
- Panel output personifies the voice: `♪ matilda has spoken` instead of `♪ spoken — matilda (elevenlabs)`. Provider name dropped from display. Phrase pool adds variety.

## [0.5.0] - 2026-02-27

### Fixed

- Installer now refreshes marketplace clone before plugin install, ensuring existing users get the correct `source.ref` pins

## [0.4.0] - 2026-02-27

### Added

- Dev/prod namespace isolation for plugin testing (`claude --plugin-dir .`)
- Audio playback serialized via `flock` — concurrent utterances queue instead of overlapping or being killed
- `tts play <file>` CLI command for flock-serialized audio playback (used by hooks)
- Cross-platform audio player: `afplay` (macOS) with `ffplay` (Linux/cross-platform) fallback

### Changed

- MCP server key renamed from `tts` to `vox`; tools now appear as `plugin:tts:vox` (was `plugin:tts:tts`)
- Installer and session-start hook clean up legacy `mcp__plugin_tts_tts__*` permission entries from pre-rename installs

## [0.3.6] - 2026-02-26

### Changed

- ElevenLabs provider now uses streaming API (`text_to_speech.stream()`) for lower time-to-first-audio

### Fixed

- Installer now runs `claude plugin update` when plugin is already installed; previously users stayed stuck on old versions
- Chime playback detached from hook process group (`nohup + disown`) so audio survives hook exit
- `--scope user` added to `claude plugin update` command (Bugbot catch)

## [0.3.5] - 2026-02-26

### Changed

- Config moved from global `~/.claude/tts.local.md` to per-project `.tts/config.md`; settings no longer leak across projects

### Added

- `argument-hint` frontmatter on `/notify`, `/speak`, `/say`, `/voice` commands for autocomplete-style picker hints

## [0.3.4] - 2026-02-26

### Fixed

- Plugin name is now `tts` on main (was `tts-dev`); marketplace installs show `plugin:tts:tts` instead of `plugin:tts-dev:tts`
- MCP server command is now `tts serve` (was `uv run tts serve`); works on machines without `uv`
- Install script handles SSH auth failure with HTTPS fallback ([#8](https://github.com/punt-labs/vox/issues/8))

### Removed

- Dev/prod plugin name swap scripts (`release-plugin.sh`, `restore-dev-plugin.sh`); plugin.json is always prod-ready

## [0.3.3] - 2026-02-25

### Added

- MCP server `instructions` field primes stop hook behavior at session start (prior-context delivery pattern from biff)

### Changed

- Stop hook phrases now imply the action naturally ("Speaking my thoughts...", "Saying my piece...") — playful for the user, instructive for the model

## [0.3.2] - 2026-02-25

### Changed

- Stop hook reason now shows playful, randomized phrases ("Clearing my throat...", "Finding my words...") instead of functional text

## [0.3.1] - 2026-02-25

### Fixed

- MCP tools now return valid JSON instead of Python repr (fixes panel display showing raw dict)
- Stop hook reason is a clean single line instead of a wall of instructions
- CLI no longer crashes on `tts install` when no TTS provider API keys are configured (lazy provider init)

## [0.3.0] - 2026-02-25

### Changed

- Renamed MCP tools: `synthesize` -> `speak`, `synthesize_batch` -> `chorus`, `synthesize_pair` -> `duet`, `synthesize_pair_batch` -> `ensemble`
- Tool panel output now shows voice and provider context with `♪` prefix (two-channel display pattern)
- Permission and idle notification phrases now vary randomly instead of repeating the same line

## [0.2.0] - 2026-02-25

### Added

- `/notify` command: toggle task-completion and permission-prompt notifications (y/c/n)
- `/speak` command: toggle voice vs chime-only notifications (y/n)
- `/recap` command: on-demand spoken summary of Claude's last response
- Stop hook: blocks Claude's stop to generate a spoken summary when /notify is enabled
- Notification hook: async audio alerts for permission prompts and idle prompts
- Chime audio: bundled MP3 tones for `/speak n` mode (task-complete and needs-approval)
- Shared hook state library (`hooks/state.sh`) for reading tts.local.md from bash
- `tts install`: marketplace-based installation via `claude plugin install tts@punt-labs`
- `tts uninstall`: full cleanup (plugin, commands, permissions, marketplace)
- Marketplace installer module (`installer.py`) for punt-labs marketplace registration
- Release scripts: `scripts/release-plugin.sh` and `scripts/restore-dev-plugin.sh`
- DESIGN.md: design decision log for notification architecture

### Changed

- `tts install` now installs via the Claude Code marketplace (previously wrote Claude Desktop config)
- Old Claude Desktop install behavior moved to `tts install-desktop`
- Plugin uses `uv run` in dev mode for working tree source exercise

## [0.1.0] - 2026-02-25

### Added

- Multi-provider TTS engine extracted from langlearn-tts
- ElevenLabs, AWS Polly, and OpenAI TTS providers
- CLI commands: synthesize, batch, pair, pair-batch, doctor, install, serve
- MCP server with synthesize, batch, pair, and pair-batch tools
- Ephemeral output mode (`.tts/` directory in cwd) for transient audio
- Claude Code plugin shell: plugin.json, hooks, /voice and /say commands
- Auto-detection: ElevenLabs > Polly (based on available API keys)
- GitHub Actions CI: lint and test workflows
