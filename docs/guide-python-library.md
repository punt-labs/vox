# Python Library Guide

vox is also a Python library. It talks to the same `voxd` daemon as the plugin and the CLI; `vox install` is the only setup.

## Synchronous Client

```python
from punt_vox import SynthesisSpec, VoiceTarget, VoxClientSync, VoxError

vox = VoxClientSync()  # connects per call — no setup, no teardown

try:
    # Speak text through the daemon (plays on the local audio device):
    vox.synthesize("Build finished", SynthesisSpec(voice="sarah"))

    # ...or get the MP3 bytes to save or stream yourself:
    audio = vox.record("Deploy complete", SynthesisSpec(voice="matilda"))

    # List voices for a provider and model (omit the model for its default):
    print(vox.voices(VoiceTarget("openai", "gpt-4o-mini-tts")))
except VoxError as exc:  # VoxdConnectionError, VoxdProtocolError, or VoxdRejectionError
    print(f"vox failed: {exc}")
```

## Async Client

`VoxClient` is the async version. It connects once and is an async context manager:

```python
import asyncio
from punt_vox import VoxClient, SynthesisSpec


async def main() -> None:
    async with VoxClient() as vox:  # connect on enter, close on exit
        await vox.synthesize("Tests green", SynthesisSpec(voice="sarah"))
        health = await vox.health()  # typed HealthStatus, not a bare dict
        print(health.provider, health.daemon_version)


asyncio.run(main())
```

## Errors and Types

Every failure raises a `VoxError` subclass, and `health()` and the `program_*` methods return typed results (`HealthStatus`, `ProgramStatus`, …) rather than dicts. The public names are listed in `punt_vox.__all__`: `VoxClient`, `VoxClientSync`, `SynthesisSpec`, `SynthesizeResult`, `VoxError`, `VoxdConnectionError`, `VoxdProtocolError`, `VoxdRejectionError`, `HealthStatus`, `ProgramStatus`, `CommandOutcome`, `ProgramSummary`, and `PromptSet`. The package ships type information (`py.typed`). Importing `punt_vox` does not import any provider SDK.

The generated reference for every public name is in [api-reference/index.md](api-reference/index.md).
