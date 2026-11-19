# Python API reference

Use the asynchronous or synchronous clients to control a running `voxd` daemon.
The daemon owns synthesis, playback, recordings, and audio programs.
See the [Vox README](https://github.com/punt-labs/vox) for installation and CLI usage.

::: punt_vox
    options:
      members: false

## Clients and synthesis

::: punt_vox.client.VoxClient

::: punt_vox.client_sync.VoxClientSync

::: punt_vox.types_synthesis.SynthesisSpec

::: punt_vox.client.SynthesizeResult

## Programs and health

::: punt_vox.types_programs.CommandOutcome

::: punt_vox.types_programs.HealthStatus

::: punt_vox.types_programs.ProgramStatus

::: punt_vox.types_programs.ProgramSummary

::: punt_vox.types_programs.PromptSet

## Errors

::: punt_vox.client_errors.VoxError

::: punt_vox.client_errors.VoxdConnectionError

::: punt_vox.client_errors.VoxdProtocolError

::: punt_vox.client_errors.VoxdRejectionError
