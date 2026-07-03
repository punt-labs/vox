# Manual Test Flight

Vox produces audio. Tests against logs and exit codes are not sufficient — the operator must hear the output and confirm it matches expectations. This document defines the canonical manual test flight: a short sequence that exercises the synthesis, cache, multi-segment, music, and mix paths through real audio hardware.

Referenced from `CLAUDE.md` inner-loop step 5 ("Exercise manually"). Run this whenever code in `src/punt_vox/voxd/`, `src/punt_vox/providers/`, `src/punt_vox/core.py`, `src/punt_vox/hooks.py`, or `src/punt_vox/server.py` changes in ways that affect runtime behavior.

## Hard rule: ask the operator after every audible step

Each step ends with a question to the operator about what they heard. **Ask immediately, not at the end.** Audio impressions decay within seconds — by the time the test flight is done the operator will not remember whether call 2 sounded identical to call 1, or whether the music ducked when voice played. If you batch the questions, the operator says "I don't remember" and the test result is unverifiable.

Use `AskUserQuestion` after each audible step. Wait for the answer before proceeding to the next step. If an answer reveals a defect, file a bead before moving on so the finding is captured while the audio is still fresh in everyone's head.

## Pre-flight

1. Working tree is clean except for the change under test.
2. `make check` passes.
3. `make install` builds and installs the wheel:

   ```bash
   make install
   ```

4. Confirm the installed env has today's code. Pick a symbol the change touched and verify it imports:

   ```bash
   /Users/jfreeman/.local/share/uv/tools/punt-vox/bin/python3 -c "from punt_vox import signal; print(signal.SignalLog.MAX_ENTRIES)"
   ```

5. Restart voxd:

   ```bash
   pkill -9 -f voxd
   /Users/jfreeman/.local/share/uv/tools/punt-vox/bin/voxd --port 8421 > /tmp/voxd.log 2>&1 &
   sleep 2
   pgrep -f voxd  # must print a PID
   ```

6. `tail /tmp/voxd.log` must show `voxd listening on http://127.0.0.1:8421` and not an `ImportError`.

## Flight

### Step 1 — single synthesis, auto-picked provider

Call `mcp__plugin_vox_mic__unmute` with `text="Phase two signal extraction is live in voxd."`. Do not pass `voice` or `provider` — the daemon picks both from the algorithm (ElevenLabs > OpenAI > Polly > platform fallback, based on available keys) and the session config.

Confirm in `/tmp/voxd.log` that the synthesis line shows the expected provider (e.g. `provider=elevenlabs`).

**Ask the operator:** "Did the single voice play cleanly — no clipping, no stutter, voice matches the picked provider?"

### Step 2 — cache hit on identical text

Repeat step 1 with the exact same `text`. The cache key is `(text, voice, provider)`; identical inputs must hit the cache.

The daemon response includes a different success quip the second time (`"heard from roger"` vs `"roger has spoken"`) — that's the indicator the cache was consulted. The actual audio should be byte-identical to step 1.

**Ask the operator:** "Did this call sound identical to step 1 — same intonation, same pacing, no fresh synthesis variation?"

> ⚠️ Cache hit identity is currently hard to confirm by ear because both paths produce identical-sounding audio. Tracked in `vox-90vw`. Until that's fixed, accept "sounded the same" as best-effort.

### Step 3 — long text, sentence splitting

Call `unmute` with a three-sentence text:

```text
"This is a longer test to exercise sentence boundary splitting in core dot py. The text gets chunked into multiple parallel synthesis calls. Then merged back into a single playable audio stream."
```

`core.py:split_text()` splits on sentence boundaries; the daemon synthesizes each chunk in parallel and merges them.

**Ask the operator:** "Did all three sentences play in order with natural pauses between them, no audible seam at the chunk boundaries?"

### Step 4 — multi-voice, multi-segment

Call `unmute` with `segments=[{"text": "First segment from roger.", "voice": "roger"}, {"text": "Second segment from matilda.", "voice": "matilda"}]`.

This exercises the segment path: per-segment voice override, sequential playback, the configured `pause_ms` gap between segments.

**Ask the operator:** "Did you hear two clearly distinct voices in order — male first, female second — with a brief pause between them?"

### Step 5 — music start

Call `mcp__plugin_vox_mic__music` with `mode="on"`. The first time per vibe, ElevenLabs Music generates a fresh ~2-minute track (10–15 seconds wall time). Subsequent calls reuse the cached track.

Confirm in `/tmp/voxd.log` that the music track is written under `~/Music/vox/tracks/`. Confirm via `ps aux | grep afplay` that a separate `afplay --volume 0.3 .../<track>.mp3` process is running — this is the loop player.

**Ask the operator:** "Is music playing at low volume? Does it sound like instrumental ambient material matching the session vibe?"

### Step 6 — voice over music (mix)

Wait 15 seconds so the music has clearly started, then call `unmute` with a short text. The voice should play at full volume on top of the ducked music.

**Ask the operator:** "While the voice played, did the music duck audibly so the voice was clearly intelligible, and did the music return to its prior level after the voice finished?"

> ⚠️ Current behavior is **static** ducking only — music plays at `--volume 0.3` from music-on time and does not duck further during voice. Tracked in `vox-zuqr`. Note the operator's answer and file a bead if the behavior has regressed beyond the known baseline.

### Step 7 — music stop

Call `music` with `mode="off"`. Confirm via `pgrep -f "afplay.*tracks"` that the music process is gone.

**Ask the operator:** "Did music stop cleanly when you sent the off command, no lingering bleed?"

## Post-flight

- Summarize: which steps passed, which raised findings.
- For every operator answer that revealed a defect, confirm a bead was filed.
- For every step that emitted unexpected error or warning log lines, capture them and decide whether to file.
- Stop the test-flight daemon if it was a temporary instance: `pkill -9 -f voxd`.

## Known issues affecting this flight

- `vox-ekmx` — small clipping artifact at end of synthesized audio (missing trailing silence)
- `vox-zuqr` — music ducking is static, not dynamic during voice playback
- `vox-90vw` — cache hit on identical text needs a verifiable audible or visual signal

When these are open, factor them in when interpreting operator answers. A "yes, clipping at the end" answer for step 1 is the known issue, not a new regression — unless the clipping is markedly worse or in a different place.
