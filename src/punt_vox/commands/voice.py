"""``vox voice`` -- list or set the session voice for the active provider.

The command is a callable object (:class:`VoiceCommand`) so both surfaces
share one instance and the ratchet counts the class-per-command shape
the rest of vox uses (see :class:`~punt_vox.server_switches.VoiceTool`).
"""

from __future__ import annotations

from typing import Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.commands._result import CommandResult, Ctx, SwitchList
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import ProviderNotConfiguredError


@final
class VoiceCommand:
    """List a provider's voices or set the active provider's voice."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a voice command."""
        return super().__new__(cls)

    async def __call__(
        self, ctx: Ctx, name: str | None = None, provider: str | None = None
    ) -> CommandResult:
        """List the roster (no arg) or set the session voice (name given).

        Listing uses the requested provider or the active one. Setting a
        voice requires an active provider and rejects an explicit mismatch.

        Set branch: strip a stray leading ``@`` via
        :meth:`SynthesisSpec.normalize_voice`; a blank/lone-``@`` write
        returns an error result rather than corrupting the config.
        """
        cfg = ctx.store.read()

        if name is None:
            return self._list(ctx, provider or cfg.provider, cfg.voice)

        if (error := self._provider_error(provider, cfg.provider)) is not None:
            return error
        return self._set(ctx, name)

    @classmethod
    def _provider_error(
        cls, target: str | None, active: str | None
    ) -> CommandResult | None:
        if not active:
            return cls._error(str(ProviderNotConfiguredError.for_cli()))
        if target and target.lower() != active.lower():
            return cls._error(
                f"Cannot set a voice for {target!r}: the active provider is {active!r}."
            )
        return None

    @classmethod
    def _set(cls, ctx: Ctx, name: str) -> CommandResult:
        normalized = SynthesisSpec.normalize_voice(name)
        if normalized is None:
            return cls._error("voice name is empty", prefix="")
        try:
            ctx.store.write_field("voice", normalized)
        except ValueError as exc:
            # ConfigStore rejects control chars / unescaped quotes in string
            # values (raises ConfigValueError, a ValueError subclass). Return a
            # clean error envelope rather than let the exception cross the
            # boundary as a traceback.
            return cls._error(str(exc))
        return CommandResult(
            text=f"{normalized}'s here.", json_data={"voice": normalized}
        )

    @classmethod
    def _list(
        cls, ctx: Ctx, provider: str | None, current: str | None
    ) -> CommandResult:
        """Return the voice roster, or an empty listing when no provider is set.

        Listing needs no synthesis, so an unset provider (no ``--provider``
        flag and no ``provider:`` in ``vox.md``) yields an honest empty
        roster instead of a refusal -- refusing at listing time when a user
        is trying to discover what to configure would be the worst possible
        moment to say "configure something first". Symmetric with ``vox
        model``'s list branch and ``mic:voice``'s. The set path
        (``VoiceCommand.__call__`` with a name) still refuses when there is
        no provider, since a wrong-provider voice landing in ``vox.md`` is
        the substitution the whole subsystem exists to prevent.

        ``VoxClientSync.voices`` requires a provider (state is the sole
        authority on which provider voxd runs) so the empty-roster branch
        must not reach it.
        """
        names: list[str] = []
        if provider:
            try:
                names = ctx.client.voices(provider)
            except (VoxdConnectionError, VoxdProtocolError) as exc:
                return cls._error(str(exc))
        listing = SwitchList(names=tuple(names), current=current)
        payload = listing.payload()
        payload["provider"] = provider or None
        return CommandResult(
            text=listing.render("" if provider else "No voices for this provider."),
            json_data=payload,
        )

    @staticmethod
    def _error(message: str, *, prefix: str = "Error: ") -> CommandResult:
        return CommandResult(
            text=f"{prefix}{message}",
            json_data={"error": message},
            error=True,
            exit_code=1,
        )


voice: VoiceCommand = VoiceCommand()
