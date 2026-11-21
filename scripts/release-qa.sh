#!/usr/bin/env bash
set -euo pipefail
umask 077

die() { printf 'QA failed: %s\n' "$*" >&2; exit 1; }

[[ -t 0 && -t 1 ]] || die "Run make qa in a desktop terminal with speakers."
wheel=${VOX_QA_WHEEL:?Set VOX_QA_WHEEL to the downloaded candidate wheel}
[[ -f "$wheel" && "${wheel##*/}" == punt_vox-*.whl ]] || die "Not a punt-vox wheel: $wheel"
wheel="$(cd "$(dirname "$wheel")" && pwd -P)/${wheel##*/}"
: "${VOX_QA_SHA256:?Set VOX_QA_SHA256 to the wheel digest in the release build summary}"
: "${ELEVENLABS_API_KEY:?Export the ElevenLabs key before starting; do not type it on camera}"
for tool in uv ffmpeg ffprobe mpv shasum; do
    command -v "$tool" >/dev/null || die "Install $tool first."
done

case "$(uname -s)" in
    Darwin)
        video_input=(-f avfoundation -framerate 15 -i "${VOX_QA_AVFOUNDATION:?Set screen:loopback audio device indices}")
        capture_input=("${video_input[@]}")
        ;;
    Linux)
        video_input=(-f x11grab -framerate 15 -i "${DISPLAY:?Run in an X11 desktop session}")
        command -v pactl >/dev/null || die "Install the PulseAudio client tools."
        # Preserve desktop access after isolating Vox's HOME.
        export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
        PULSE_SERVER=${PULSE_SERVER:-"$(LC_ALL=C pactl info | sed -n 's/^Server String: //p')"}
        export PULSE_SERVER
        monitor=${VOX_QA_PULSE_MONITOR:-"$(pactl get-default-sink).monitor"}
        capture_input=("${video_input[@]}" -f pulse -i "$monitor")
        ;;
    *) die "Screen and audio capture need macOS or Linux." ;;
esac

mkdir -p .tmp/qa
evidence=$(mktemp -d "$(pwd -P)/.tmp/qa/run.XXXXXX")
# Keep daemon state, auth tokens, and the venv outside the evidence and checkout.
work=$(mktemp -d /tmp/vox-qa.XXXXXX)
recorder='' daemon='' step=setup
result=0

screenshot() {
    local target="$evidence/fail-$step.png"
    if [[ "$(uname -s)" == Darwin ]]; then
        screencapture -x "$target"
    else
        ffmpeg -loglevel error -nostdin "${video_input[@]}" -frames:v 1 -update 1 "$target"
    fi
}

fail_step() {
    result=1
    printf 'FAIL %s: %s\n' "$step" "$*" | tee -a "$evidence/results.txt"
    screenshot || echo "Screenshot failed for $step" >&2
}

finish() {
    local status=$?
    trap - EXIT
    if (( status != 0 )); then
        fail_step "command failed (exit $status)"
    fi
    if [[ -n "$daemon" ]]; then
        kill -TERM "$daemon" 2>/dev/null || true
        wait "$daemon" 2>/dev/null || true
    fi
    if [[ -n "$recorder" ]]; then
        kill -INT "$recorder" 2>/dev/null || true
        wait "$recorder" 2>/dev/null || true
        local video="$evidence/flight.mp4"
        if ! ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$video" | grep -qx video ||
           ! ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$video" | grep -qx audio; then
            step=capture
            fail_step "recording has no picture or audio stream; see capture.log"
        elif (( result == 0 )) && {
            ! ffmpeg -hide_banner -nostdin -i "$video" -vn -af volumedetect -f null - >"$evidence/audio-level.log" 2>&1 ||
            ! awk '/max_volume:/ && ($(NF-1) + 0) > -50 && $(NF-1) != "-inf" { heard = 1 } END { exit !heard }' "$evidence/audio-level.log"
        }; then
            step=capture
            fail_step "recording is effectively silent; see audio-level.log"
        fi
    fi
    rm -rf -- "$work"
    if (( result == 0 )); then
        printf 'PASS flight — Jim must still approve in GitHub.\n' | tee -a "$evidence/results.txt"
    fi
    printf 'Evidence: %s\n' "$evidence"
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

listen() {
    local reply
    if read -r -p "$1 [y/N] " reply && [[ "$reply" == [yY] ]]; then
        printf 'PASS %s\n' "$step" | tee -a "$evidence/results.txt"
    else
        fail_step "$1"
    fi
}

echo "Recording this desktop and system audio. Close unrelated windows first."
read -r -p "Press Enter to start, or Ctrl-C to cancel. "
ffmpeg -loglevel warning -nostdin "${capture_input[@]}" \
    -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -c:v libx264 -preset ultrafast \
    -crf 26 -pix_fmt yuv420p -c:a aac "$evidence/flight.mp4" \
    >"$evidence/capture.log" 2>&1 &
recorder=$!
sleep 2
kill -0 "$recorder" || die "Screen/audio recorder exited; see capture.log."

step=install
checksum=$(shasum -a 256 "$wheel")
printf '%s\n' "$checksum" | tee -a "$evidence/results.txt"
if [[ "${checksum%% *}" != "$VOX_QA_SHA256" ]]; then
    die "The wheel digest does not match the release build summary."
fi
cd "$work"
unset PYTHONPATH PYTHONHOME VOXD_HOST VOXD_PORT VOXD_TOKEN TTS_PROVIDER TTS_MODEL
uv venv --python 3.13 "$work/venv"
uv pip install --python "$work/venv/bin/python" "${wheel}[call]"
export PATH="$work/venv/bin:$PATH" HOME="$work/home" PUNT_TELEMETRY=off
export VOX_OUTPUT_DIR="$work/music"
mkdir -p "$HOME" "$VOX_OUTPUT_DIR"
vox version | tee -a "$evidence/results.txt"

step=daemon
voxd --host 127.0.0.1 --port 0 >"$evidence/daemon.log" 2>&1 &
daemon=$!
for ((attempt = 0; attempt < 100; attempt++)); do
    [[ -s "$HOME/.punt-labs/vox/run/serve.port" ]] && break
    kill -0 "$daemon" || die "Candidate daemon exited; see daemon.log."
    sleep 0.1
done
[[ -s "$HOME/.punt-labs/vox/run/serve.port" ]] || die "Candidate daemon never published its port."

step=voice
line="The river carries the morning light past the garden."
vox say --provider elevenlabs "$line"
listen "After playback ends: was the line clear, natural, and free of clipping or stutter?"

step=voice-repeat
vox say --provider elevenlabs "$line"
listen "After playback ends: did the repeated line sound the same?"

step=recording
id=$(vox rec new --provider elevenlabs "This recording lives in the Vox library.")
vox rec list | grep -Fx -- "$id"
vox rec play "$id"
listen "Did the stored recording play cleanly?"

step=fetch
vox rec get "$id"
[[ -s "$id" ]]
digest=$(shasum -a 256 "$id")
if vox rec get "$id"; then
    die "Second fetch overwrote an existing file."
fi
[[ "$(shasum -a 256 "$id")" == "$digest" ]]
if vox rec get "../escape.mp3"; then
    die "Fetch accepted a path instead of a recording ID."
fi
[[ ! -e ../escape.mp3 ]]
mpv --no-video --no-config -- "$id"
listen "Did the downloaded audio match the daemon playback?"

step=music
album=$(vox music new "Warm slow analog synth pads, in D minor, instrumental, no drums.")
vox music play "$album"
listen "Once it starts: does the music match the prompt, without distortion or voices?"

step=mix
vox say --provider elevenlabs "The voice should stay clear over the music."
listen "After the line: was speech intelligible, and did the music continue?"

step=music-stop
if vox music remove "$album"; then
    die "The catalog removed an album while it was playing."
fi
vox music stop
listen "Did the music stop cleanly?"
step=cleanup
vox music remove "$album"
vox rec remove "$id"
