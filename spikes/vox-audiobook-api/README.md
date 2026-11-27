# vox-audiobook-api: ElevenLabs endpoints for an audiobook Program

A hand-run harness that calls the ElevenLabs endpoints an audiobook Program
would use, the way `voxd` would. It backs the audiobook design work in Linear
PL-827. Nothing here ships and nothing touches `src/`.

## Probes

| Probe | Endpoint | What it checks |
|-------|----------|----------------|
| `tts` | `POST /v1/text-to-speech/{voice}` | One `eleven_v4` call turns a long chapter into audio; a second call carrying `previous_request_ids` is accepted. |
| `dialogue` | `POST /v1/text-to-dialogue` | Two voices on `eleven_v4` and `eleven_v3` return audio. |
| `pron` | `POST /v1/pronunciation-dictionaries/add-from-rules` | An alias dictionary changes the words Scribe hears: the transcript carries the alias words with the dictionary and lacks them without it. |
| `studio` | `POST /v1/studio/projects` | A Studio project builds from JSON content, converts, and yields a snapshot archive. |

## Run it

`ELEVENLABS_API_KEY` comes from the environment (the repo `.envrc` provides
it). The probes spend real credits and `pron` leaves one pronunciation
dictionary on the account.

```bash
uv run --no-project --with httpx python spikes/vox-audiobook-api/spike.py OUT_DIR
uv run --no-project --with httpx python spikes/vox-audiobook-api/spike.py OUT_DIR pron
```

`OUT_DIR` receives the generated audio. The second argument is one of `tts`,
`dialogue`, `pron`, `studio` or `all` (the default). `--help` prints usage and
runs nothing.

## Exit status

The run exits 0 only when every probe reached its assertions and they passed.
A refused request, a transport error, a reply of the wrong shape, an empty or
non-audio body, a refused call by either dialogue model, and a Studio project
that never converts each record a failed verdict for that probe and make the
exit status 1. Later probes still run.

Without `ffprobe` on `PATH`, audio bodies cannot be checked for a readable
duration. Each prints a `WARN ... unverified (no ffprobe)` line instead of
`PASS`, and the run ends with `N audio file(s) unverified (no ffprobe)`. A WARN
does not fail the run, so read that final line before trusting a zero exit.

The Studio probe fails on accounts without Studio API access: ElevenLabs
answers `403 invalid_subscription` and asks for the account to be whitelisted.
That failure is expected there, and it is the finding.

## Tests

The tests run offline against `httpx.MockTransport`. They need no key and spend
no credits. This directory is not under `tests/`, so `make test` does not
collect it; run it by path:

```bash
uv run --no-project --with httpx --with pytest pytest spikes/vox-audiobook-api -q
```

The tests that decode audio need `ffprobe` on `PATH`; it ships with ffmpeg,
which vox already requires. Without it they skip with a stated reason, and a
separate test covers the run's behaviour when `ffprobe` is absent.

## Results

Run on 2026-11-26 against the account's `growing_business` plan. Findings and
the design consequences are on PL-827.

| Probe | Result |
|-------|--------|
| `tts` | 3,096 characters became 210.9 s of audio in one call. `character-cost` was 1491. Stitching with `previous_request_ids` was accepted. |
| `dialogue` | Two voices returned audio on both `eleven_v4` and `eleven_v3`. |
| `pron` | With aliases that change the spoken words, Scribe's transcript carried both aliases with the dictionary and neither without it. |
| `studio` | `403 invalid_subscription`: the Studio API needs an ElevenLabs sales whitelist. |

Eleven v4 already speaks camelCase identifiers such as `PlaceOrderEvents` as
separate words, so an alias that only spaces them out changes nothing the
transcript can show.
