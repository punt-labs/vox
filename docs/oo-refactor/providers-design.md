# Providers Package Redesign

Date: 2026-05-14
Author: claude (COO)
Reviewer: Ralph Johnson (rej)
Status: GO — revised per review feedback

## Problem

The providers package has 2165 lines across 7 files. Every provider is a
monolith that mixes five distinct responsibilities into one class:

1. **Synthesis** — the core `synthesize()` call to the provider SDK
2. **Voice resolution** — cache management, name→ID lookup, API fetching
3. **Health checking** — credential validation, API reachability
4. **Chunked synthesis** — splitting long text, stitching audio
5. **Direct playback** — subprocess-based play-to-device (say, espeak only)

### Current metric failures

| File | Failures |
|------|----------|
| `__init__.py` | method_ratio=0, class_to_func_ratio=0 (9 top-level functions, 0 classes) |
| `elevenlabs.py` | module_size=348, max_complexity=11, init_violations=1 |
| `espeak.py` | module_size=355, max_complexity=17, class_to_func_ratio=0.4, init_violations=1 |
| `polly.py` | module_size=301, class_to_func_ratio=0.4, init_violations=1 |
| `say.py` | module_size=330, class_to_func_ratio=0.4, init_violations=1 |
| `openai.py` | init_violations=1 |
| `elevenlabs_music.py` | init_violations=1 |

Every provider uses `__init__` instead of `__new__`. That's 6 init_violations.

### Design problems

**1. Voice cache as mutable module-level state (say, espeak).**
`say.py` and `espeak.py` have module-level `VOICES: dict[str, VoiceConfig] = {}`
and `_voices_loaded: bool = False` with a `_load_voices_from_system()` function
that mutates them. This is global mutable state. Tests that run in parallel or
in different orders can interfere. The function uses `global _voices_loaded` —
a pattern our standards explicitly reject.

ElevenLabs and Polly moved voice caches to instance attributes in a recent
commit. Say and espeak did not.

**2. Duplicated chunked synthesis.**
ElevenLabs and OpenAI have nearly identical `_chunked_synthesize` methods:
split text → synthesize each chunk to a temp file → stitch with `stitch_audio`.
~25 lines duplicated.

**3. Duplicated voice resolution pattern.**
Every provider has `_resolve_voice_config` / `_resolve_voice_name` with the
same pattern: check cache → load voices if needed → check again → raise
`VoiceNotFoundError`. Four implementations of the same algorithm with different
VoiceConfig types.

**4. Duplicated health check structure.**
Every provider's `check_health()` follows the same pattern: check API key →
check API access → return list of HealthCheck. The structure is identical; only
the SDK-specific calls differ.

**5. Registry is procedural.**
`__init__.py` has 9 module-level functions and a mutable module-level dict.
`auto_detect_provider` is a 25-line function with 7 branches. `get_provider`
reads config, resolves names, calls factories. None of this is on a class.

**6. Direct play is on the synthesis class.**
`SayProvider` and `EspeakProvider` implement `DirectPlayProvider` protocol
alongside `TTSProvider`. Direct playback has nothing to do with file synthesis —
different subprocess commands, different I/O paths. Two responsibilities on one
class.

**7. Local providers (say, espeak) have identical structure.**
Both: discover binary → parse voice list from subprocess → resolve voice →
convert rate to WPM → synthesize to temp file → ffmpeg to MP3 → return result.
And both: play directly via subprocess without file. The only differences are
the binary name, output format (AIFF vs WAV), and voice parsing regex.

## Proposed Design

### Principle: separate what varies from what's common

Five providers share the same protocol interface (`TTSProvider`). What varies
per provider is the SDK call, voice catalog format, and authentication. What's
common is voice caching, chunked synthesis, health check structure, rate
conversion, and the synthesize-to-file workflow.

### Module layout

| File | Class(es) | Lines (est.) | Responsibility |
|------|-----------|-------------|----------------|
| `__init__.py` | `ProviderRegistry` | ~120 | Registry, auto-detection, `get_provider()`. Single class replaces 9 functions. |
| `voice_cache.py` | `VoiceCache[V]` | ~80 | Generic voice cache with TTL, force-refresh, cooldown. Used by all providers. |
| `chunked.py` | `chunked_synthesize()` | ~40 | Shared helper: split → synthesize chunks → stitch. Eliminates duplication. |
| `elevenlabs.py` | `ElevenLabsProvider` | ~250 | Slimmed: delegates voice caching to `VoiceCache`, chunking to `chunked_synthesize`. |
| `openai.py` | `OpenAIProvider` | ~150 | Slimmed: delegates chunking. Voice list is static (no cache needed). |
| `polly.py` | `PollyProvider` | ~220 | Delegates voice caching. `VoiceConfig` stays here (Polly-specific). |
| `say.py` | `SayProvider` | ~180 | Voice cache on instance (no globals). `SayVoiceConfig` stays. |
| `espeak.py` | `EspeakProvider` | ~200 | Voice cache on instance (no globals). `EspeakVoiceConfig` stays. |
| `local_play.py` | `SayDirectPlayer`, `EspeakDirectPlayer` | ~100 | Direct playback extracted from say/espeak. Implements `DirectPlayProvider`. |
| `elevenlabs_music.py` | `ElevenLabsMusicProvider` | ~100 | Unchanged except `__init__` → `__new__`. |

### `ProviderRegistry` — replaces procedural `__init__.py`

```python
class ProviderRegistry:
    """Provider registration, auto-detection, and resolution."""

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._factories: dict[str, Callable[..., TTSProvider]] = {}
        return self

    def register(self, name: str, factory: Callable[..., TTSProvider]) -> None: ...
    def get(self, name: str | None = None, **kwargs) -> TTSProvider: ...
    def auto_detect(self) -> str: ...
```

The default registry is constructed at module level with all 5 providers
registered. `get_provider()` becomes a thin module-level function that
delegates to the default registry — backward compatible.

`auto_detect` moves onto the registry. The detection logic (check env vars,
probe credentials, check platform) stays the same but becomes a method.

### `VoiceCache[V]` — generic voice cache

```python
class VoiceCache[V]:
    """TTL-based voice cache with force-refresh and cooldown."""

    __slots__ = ("_voices", "_loaded_at", "_force_refreshed_at", "_ttl_seconds", "_cooldown_seconds")

    def __new__(cls, *, ttl_seconds: int = 1800, cooldown_seconds: int = 60) -> Self: ...
    def get(self, key: str) -> V | None: ...
    def load(self, voices: dict[str, V]) -> None: ...
    def is_stale(self) -> bool: ...
    def can_force_refresh(self) -> bool: ...
    def mark_force_refreshed(self) -> None: ...
```

Replaces the duplicated voice cache logic in ElevenLabs (TTL + cooldown),
Polly (loaded flag), say (global dict + flag), espeak (global dict + flag).

The generic parameter `V` is the value type. Concrete instantiations:

| Provider | Instantiation | Notes |
|----------|--------------|-------|
| ElevenLabs | `VoiceCache[str]` (name → voice_id) | TTL=1800s, cooldown=60s |
| Polly | `VoiceCache[VoiceConfig]` | TTL=0 (load-once) |
| say | `VoiceCache[SayVoiceConfig]` | TTL=0 (load-once from subprocess) |
| espeak | `VoiceCache[EspeakVoiceConfig]` | TTL=0 (load-once from subprocess) |

`ttl_seconds=0` degenerates to a load-once flag. The cooldown becomes a no-op
if never force-refreshed. This is the Strategy pattern applied to cache policy
via constructor parameters.

### `chunked_synthesize()` — shared helper

```python
def chunked_synthesize(
    text: str,
    char_limit: int,
    synthesize_chunk: Callable[[str, Path], None],
    output_path: Path,
    pause_ms: int = 0,
) -> None:
    """Split text, synthesize each chunk, stitch into one file."""
```

Synchronous — both ElevenLabs and OpenAI `_single_synthesize` are synchronous
(they write bytes from streaming responses). The function accepts a sync
callable. No async/sync mismatch.

Replaces `ElevenLabsProvider._chunked_synthesize` and
`OpenAIProvider._chunked_synthesize`. The provider passes its
`_single_synthesize` via a lambda that closes over voice_id/speed:

```python
# In ElevenLabsProvider.synthesize():
chunked_synthesize(
    text=text,
    char_limit=char_limit,
    synthesize_chunk=lambda chunk, path: self._single_synthesize(chunk, path, voice_id, request),
    output_path=output_path,
)
```

The helper handles split, temp dir, iteration, and stitch.

### Direct play separation

Currently `SayProvider` and `EspeakProvider` each implement both `TTSProvider`
(file synthesis) and `DirectPlayProvider` (play to device). These are different
responsibilities:

- `TTSProvider.synthesize()` — subprocess → temp file → ffmpeg → MP3
- `DirectPlayProvider.play_directly()` — subprocess → audio device

After the split:
- `SayProvider` implements `TTSProvider` only
- `SayDirectPlayer` implements `DirectPlayProvider` only
- Both share a single `VoiceCache` instance via constructor injection

```python
class SayDirectPlayer(DirectPlayProvider):
    __slots__ = ("_voice_cache",)

    def __new__(cls, *, voice_cache: VoiceCache[SayVoiceConfig]) -> Self: ...
    def play_directly(self, request: AudioRequest) -> int: ...
```

Daemon wiring:

```python
say_cache: VoiceCache[SayVoiceConfig] = VoiceCache(ttl_seconds=0)
say_provider = SayProvider(voice_cache=say_cache)
say_player = SayDirectPlayer(voice_cache=say_cache)
```

The `VoiceCache` is the shared collaborator — classic Composition. The
synthesis pipeline uses `isinstance(provider, DirectPlayProvider)` checks
(already in place) to determine if direct play is available. The registry
returns the synthesis provider; the daemon holds the direct player separately.

This resolves the SRP violation and makes each class independently testable.

### Local provider base class

`say.py` and `espeak.py` have identical structure: discover binary, parse voice
list, resolve voice, convert rate, synthesize via subprocess + ffmpeg. The
differences are: binary name, voice parsing, output format (AIFF vs WAV), and
command-line flags.

A `LocalProvider` base class or shared helper could eliminate ~100 lines of
duplication. However, this risks premature abstraction — the two providers have
diverged in subtle ways (espeak registers voices by multiple keys, say
discovers voices dynamically). A shared helper for the ffmpeg conversion step
is safer than a full base class.

**Recommendation**: Extract `_ffmpeg_to_mp3(input_path, output_path)` and
`_rate_to_wpm(rate) -> int` as shared functions in a `convert.py` module
(~30 lines). Both are identical in say and espeak. Do not create a base class.
The duplication in voice resolution is already addressed by `VoiceCache`.
Revisit the base class decision if a third local provider appears (e.g.,
Windows SAPI).

### `__init__` → `__new__` on all providers

All 6 provider classes currently use `__init__`. Convert to `__new__` per
PY-CC-1. The pattern:

```python
def __new__(cls, *, model: str | None = None) -> Self:
    self = super().__new__(cls)
    self._model = model or os.environ.get("TTS_MODEL") or _DEFAULT_MODEL
    ...
    return self
```

This fixes all 6 init_violations.

### Say/espeak: module-level globals → instance attributes

Move `VOICES` dict and `_voices_loaded` flag from module level to instance
attributes on each provider, backed by `VoiceCache`. The
`_load_voices_from_system()` module-level function becomes a private method.

This eliminates global mutable state and makes tests hermetic.

### Complexity reduction

`espeak.py` has CC=17 in `_load_voices_from_system` (the voice parsing logic).
After moving to instance method, apply Extract Method:
- `_parse_voice_line(line, header) -> EspeakVoiceConfig | None`
- `_discover_binary() -> str | None` (already exists as module-level function)

Target: every method CC ≤ 10.

`elevenlabs.py` has CC=11 in `_load_voices`. The logic is simpler (iterate API
response) but the force-refresh + TTL + cooldown branching adds complexity.
After `VoiceCache` absorbs the TTL/cooldown logic, `_load_voices` becomes
a straightforward API call + `cache.load()`. Target: CC ≤ 5.

### Dependency direction

```
ProviderRegistry ──> TTSProvider (protocol)
                 ──> individual providers (via factory callables)

ElevenLabsProvider ──> VoiceCache
                   ──> chunked_synthesize
                   ──> ElevenLabs SDK

PollyProvider ──> VoiceCache
              ──> boto3

SayProvider ──> VoiceCache
SayDirectPlayer ──> VoiceCache (shared instance)

EspeakProvider ──> VoiceCache
EspeakDirectPlayer ──> VoiceCache (shared instance)

OpenAIProvider ──> chunked_synthesize
               ──> openai SDK
```

No cycles. Providers depend on shared infrastructure (`VoiceCache`,
`chunked_synthesize`). Shared infrastructure depends on nothing.

### Test strategy

| Test file | Tests | Mocks |
|-----------|-------|-------|
| `test_voice_cache.py` | TTL expiry, force refresh, cooldown, get/load | Pure — no mocks needed |
| `test_chunked.py` | Split + stitch, single chunk passthrough | Mock `synthesize_chunk` callable |
| `test_providers.py` | Registration, get by name, auto-detect | Mock env vars, mock `shutil.which` |
| `test_elevenlabs.py` | Existing tests + voice cache delegation | Mock SDK client |
| `test_openai.py` | Existing tests + chunking delegation | Mock SDK client |
| `test_polly.py` | Existing tests + voice cache delegation | Mock boto3 |
| `test_say.py` | Existing tests, no global state leaks | Mock subprocess |
| `test_espeak.py` | Existing tests, no global state leaks | Mock subprocess |
| `test_local_play.py` | Direct play for say + espeak | Mock subprocess |

`VoiceCache` tests are pure unit tests — no SDK, no subprocess, no file I/O.
This is the highest-value new test: the TTL/cooldown/force-refresh logic is
currently untested because it's interleaved with API calls.

### Execution order

1. `VoiceCache[V]` + tests (leaf dependency, unblocks everything)
2. `__init__` → `__new__` on all 6 providers (mechanical, no design change)
3. `chunked_synthesize()` + tests, update ElevenLabs + OpenAI
4. `ProviderRegistry` class in `__init__.py`, backward-compatible `get_provider()`
5. Extract `_ffmpeg_to_mp3` and `_rate_to_wpm` into `convert.py` (before touching say/espeak)
6. Say/espeak: move globals to instance + `VoiceCache` + use `convert.py`
7. ElevenLabs: delegate voice cache to `VoiceCache`, reduce complexity. Move `_extract_api_error_message` to static method.
8. Polly: delegate voice cache to `VoiceCache`
9. Extract `SayDirectPlayer` and `EspeakDirectPlayer` into `local_play.py`
10. Complexity reduction: Extract Method on espeak voice parsing, elevenlabs voice loading

Step 5 precedes Step 6 to avoid touching say.py/espeak.py twice for
overlapping concerns (PY-RF-1: one transformation per step).

### Notes on specific items

**OpenAI `VOICES` dict** (module-level, line 33-43): frozen constant, not
mutable state. Acceptable as-is. No action needed.

**`_extract_api_error_message`** (elevenlabs.py, line 61-70): standalone
module-level function. Move to `@staticmethod` on `ElevenLabsProvider` in
Step 7 to avoid `class_to_func_ratio` regression on the slimmed file.

**`warnings.filterwarnings`** (`__init__.py`, line 16-21): suppresses a
third-party pydantic v1 bug on Python 3.14+. Out of scope for this refactor.
Documented per PY-EH-7 exception.

### Metrics after completion

| File | module_size | max_complexity | init_violations | All pass? |
|------|-------------|----------------|-----------------|-----------|
| `__init__.py` | ~120 | ≤5 | 0 | yes |
| `voice_cache.py` | ~80 | ≤5 | 0 | yes |
| `chunked.py` | ~40 | ≤3 | 0 | yes |
| `elevenlabs.py` | ~250 | ≤8 | 0 | yes |
| `openai.py` | ~150 | ≤5 | 0 | yes |
| `polly.py` | ~220 | ≤8 | 0 | yes |
| `say.py` | ~180 | ≤6 | 0 | yes |
| `espeak.py` | ~200 | ≤8 | 0 | yes |
| `local_play.py` | ~100 | ≤4 | 0 | yes |
| `convert.py` | ~30 | ≤2 | 0 | yes |
| `elevenlabs_music.py` | ~100 | ≤5 | 0 | yes |

Zero metric failures.

### Non-negotiable constraints

- `from __future__ import annotations` in every file
- `__new__` constructors, never `__init__` (except `@dataclass`)
- All instance attributes prefixed with `_`
- `__slots__` on every class
- `__all__` in every module
- `@dataclass(frozen=True, slots=True)` for value objects
- `module_size <= 300`, `classes_per_module <= 3`, `max_complexity <= 10`
- No `# noqa`, `# type: ignore`, `--no-verify`, or `xfail` added to pass checks
- Tests mirror source structure
- No global mutable state
