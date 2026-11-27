# Python Library Guide

vox is also a Python library. It talks to the same `voxd` daemon as the plugin and the CLI; `vox install` is the only setup.

Every `SynthesisSpec` must name a provider. The daemon rejects a request without one (`Unknown provider ''`); it does not guess.

## Synchronous Client

```python
from punt_vox import SynthesisSpec, VoiceTarget, VoxClientSync, VoxError

vox = VoxClientSync()  # connects per call — no setup, no teardown

try:
    # Speak text through the daemon (plays on the daemon's audio device):
    vox.synthesize("Build finished", SynthesisSpec(voice="sarah", provider="elevenlabs"))

    # ...or record into the daemon's store, then fetch the MP3 bytes:
    spec = SynthesisSpec(voice="matilda", provider="elevenlabs")
    recording = vox.record("Deploy complete", spec)
    audio = vox.fetch(recording.name)

    # List voices for a provider and model (omit the model for its default):
    print(vox.voices(VoiceTarget("openai", "gpt-4o-mini-tts")))
except VoxError as exc:  # VoxdConnectionError, VoxdProtocolError, or VoxdRejectionError
    print(f"vox failed: {exc}")
```

`record()` returns a `RecordResult`: the recording's store name and byte count, not the audio. The audio stays in the daemon's store until `fetch()` copies it; `play()` plays it on the daemon's machine.

## Async Client

`VoxClient` is the async version. It connects once and is an async context manager:

```python
import asyncio
from punt_vox import VoxClient, SynthesisSpec


async def main() -> None:
    async with VoxClient() as vox:  # connect on enter, close on exit
        spec = SynthesisSpec(voice="sarah", provider="elevenlabs")
        await vox.synthesize("Tests green", spec)
        health = await vox.health()  # typed HealthStatus, not a bare dict
        print(health.status, health.daemon_version)


asyncio.run(main())
```

`HealthStatus` has no provider field: the daemon has no provider of its own. `provider_status()` reports per-provider readiness.

## Errors and Types

Every failure raises a `VoxError` subclass, and `health()` and the `program_*` methods return typed results (`HealthStatus`, `ProgramStatus`, …) rather than dicts. The public names are listed in `punt_vox.__all__`: `VoxClient`, `VoxClientSync`, `SynthesisSpec`, `SynthesizeResult`, `VoiceTarget`, `VoxError`, `VoxdConnectionError`, `VoxdProtocolError`, `VoxdRejectionError`, `HealthStatus`, `ProgramStatus`, `CommandOutcome`, `ProgramSummary`, and `PromptSet`. The package ships type information (`py.typed`). Importing `punt_vox` does not import any provider SDK.

The mkdocs source for the generated API reference is [api-reference/index.md](api-reference/index.md).
