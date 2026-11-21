# Installed release candidate QA

Vox release tags start a **candidate**, not a production publish. The
[Release workflow](../../.github/workflows/release.yml) builds the final-version
wheel and sdist once, then runs the [nightly suite](../../.github/workflows/roundtrip-live.yml)
against that installed wheel. Nothing uploads to TestPyPI or PyPI until the
`release` environment reviewer approves the **installed-candidate QA** job.

## Run the flight

1. Open the tagged [Release run](https://github.com/punt-labs/vox/actions/workflows/release.yml).
   Wait for **Run nightly suite against the release candidate** to pass.
   Check out the tag and download the `vox-candidate-<attempt>` artifact from
   that run. Use the artifact name and wheel SHA-256 in the build job summary;
   do not rebuild or substitute a local, TestPyPI, or PyPI wheel.
2. On a desktop with working speakers, install `uv`, `ffmpeg` (with
   `libx264`), and `mpv`. Load `ELEVENLABS_API_KEY` into the terminal
   environment from your normal secret store before recording. The flight
   uses paid ElevenLabs speech and generates one paid music track.
3. Set up system-audio capture (not the microphone):
   - **Linux:** use an X11 session with PulseAudio or PipeWire-Pulse and
     its client tools (`pactl`).
     Capture defaults to the monitor of the default sink. Set
     `VOX_QA_PULSE_MONITOR` if Vox audio uses a different sink.
   - **macOS:** grant the terminal screen/audio recording permissions and
     route output to both speakers and a loopback device (e.g. a Multi-Output
     Device with BlackHole). List devices with
     `ffmpeg -f avfoundation -list_devices true -i ""`; set
     `VOX_QA_AVFOUNDATION` to the screen and loopback indices as
     `video:audio`, for example `1:0`.
4. Close unrelated windows; the recorder captures the whole screen and system
   audio. Run this from the tagged checkout:

   ```bash
   make qa VOX_QA_WHEEL=/absolute/path/to/punt_vox-X.Y.Z-py3-none-any.whl \
     VOX_QA_SHA256='copy-the-wheel-sha256-from-the-summary'
   ```

   The script installs that wheel in a temporary environment and starts its
   own loopback daemon with fresh state. It does not replace the installed
   production CLI or stop the production daemon. It refuses a wheel whose
   digest does not match the build summary.

## Judge each audible step

Answer each prompt only after hearing the step. No, silence, and uncertainty
are failures, not skips. A logfile cannot attest to listening quality.

| Step | What to listen for |
| --- | --- |
| Speech, then repeat | A natural, clear line without clipping or stutter; the repeat sounds identical. |
| Stored and fetched recording | Both play clearly and sound the same. The script verifies fetch and no clobber. |
| Music | The generated track sounds like warm, slow, instrumental synth pads. |
| Speech over music | Speech remains intelligible and the music continues afterward. |
| Music stop | Playback stops cleanly, without bleed. |

The script also asserts that the live album cannot be deleted while playing
and that a recording fetch refuses paths rather than IDs.

## Attach evidence and approve

- Keep the per-run directory printed by `make qa`: `flight.mp4` is screen
  and system audio; `results.txt` has the wheel digest and verdicts;
  `fail-*.png` are the screenshots for failed steps. Check that the clip
  captures the actual speech and music before sharing it. Logs in the same
  directory help diagnose recording or daemon failures.
- Link the evidence and wheel digest in the release PR. If anything fails,
  attach its screenshot and recording to the finding in Linear with
  `repo:vox`; reject (or leave blocked) the pending deployment. If code needs
  a fix, prepare a new version and tag; do not move the failed tag.
- **Jim:** confirm the voice and music results, then approve the pending
  **installed-candidate QA** deployment in the Release run. If GitHub prompts
  again for the PyPI deployment after TestPyPI validation, approve that
  deployment only for the same run and artifact.
  Copy the exact **Required release approval comment** from the build summary
  into the QA approval dialog; an approval without it cannot ship.

After PyPI publishes the same files, the workflow installs that exact public
version into a new environment. It synthesizes and transcribes one line;
every word must match. Failure here cannot unpublish the package: investigate
the smoke before continuing the GitHub release and marketplace rollout.
If PyPI has not indexed the version yet, rerun only the failed smoke job, not
the publish jobs.

For a pre-merge PR audio demo, build from the clean PR head with
`make build` and pass its local wheel and digest (`shasum -a 256`).
This is not a release sign-off. Exercise any other affected entry points separately.
