"""``vox provider`` -- list or set the TTS provider.

The command is a callable object (:class:`ProviderCommand`) so both
surfaces share one instance and the ratchet counts the class-per-command
shape the rest of vox uses (see
:class:`~punt_vox.server_switches.ProviderTool`).
"""

from __future__ import annotations

from typing import Self, final

from punt_vox.cascade import Cascade, RosterError
from punt_vox.commands._result import CommandResult, Ctx, SwitchList
from punt_vox.server_switches import PROVIDER_NAMES


@final
class ProviderCommand:
    """List or set the TTS provider."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a provider command."""
        return super().__new__(cls)

    async def __call__(self, ctx: Ctx, name: str | None = None) -> CommandResult:
        """List the five providers, or write a new one to the session config.

        No arg: list ``elevenlabs``, ``openai``, ``polly``, ``say``, ``espeak``,
        marking the current selection.

        Name given: validate against the closed enum. On a genuine change,
        the cascade rule fires: writes provider + model = first
        from ``MODEL_TABLE.available(name)`` (empty string for modelless) +
        voice = first from the new provider's voice roster, all in one
        atomic ``write_fields`` call. A re-publish of the same provider is
        a no-op -- no roster fetch, no disk write. A daemon fault on the
        roster fetch aborts the write and returns an error result rather
        than persist a provider whose voice we could not read.
        """
        cfg = ctx.store.read()

        if name is None:
            listing = SwitchList(names=PROVIDER_NAMES, current=cfg.provider)
            return CommandResult(text=listing.render(), json_data=listing.payload())

        if name not in PROVIDER_NAMES:
            allowed = ", ".join(PROVIDER_NAMES)
            return CommandResult.failure(
                f"unknown provider {name!r}. Allowed: {allowed}"
            )

        if cfg.provider == name:
            return CommandResult(text=f"Provider: {name}", json_data={"provider": name})

        target = Cascade.default_target(name)
        voice_default = Cascade.fetch_first_voice(ctx.client, target)
        if isinstance(voice_default, RosterError):
            return CommandResult.failure(voice_default.message)

        updates = {
            "provider": name,
            "model": target.model_field,
            "voice": voice_default,
        }
        try:
            ctx.store.write_fields(updates)
        except ValueError as exc:
            return CommandResult.failure(str(exc))
        return CommandResult(
            text=f"Provider: {name}",
            json_data={**updates},
        )


provider: ProviderCommand = ProviderCommand()
