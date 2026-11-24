"""``vox voice`` -- list or set the session voice for the active provider.

The CLI command and MCP voice tool share the voice-selection rules in
:class:`~punt_vox.voice_roster.VoiceRoster`.
"""

from __future__ import annotations

from typing import Self, final

from punt_vox.commands._result import CommandResult, Ctx, SwitchList
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.types_synthesis_errors import ProviderNotConfiguredError
from punt_vox.voice_roster import RosterError, VoiceRoster


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
        voice requires an active provider and accepts a roster name or a raw
        ElevenLabs ID. An explicit provider must match the active one.

        Set branch: strip a stray leading ``@`` via
        :meth:`SynthesisSpec.normalize_voice`; a blank/lone-``@`` write
        returns an error result rather than corrupting the config.
        """
        if name is None:
            cfg = ctx.store.read()
            return self._list(ctx, provider or cfg.provider, cfg.voice)
        normalized = SynthesisSpec.normalize_voice(name)
        if normalized is None:
            return self._error("voice name is empty", prefix="")
        return self._set(ctx, normalized, provider)

    @classmethod
    def _set(cls, ctx: Ctx, name: str, target: str | None) -> CommandResult:
        try:
            active = cls._require_provider(target, ctx.store.read_field("provider"))
            if (
                error := VoiceRoster.selection_error(ctx.client, active, name)
            ) is not None:
                return cls._error(error)

            def check(fields: dict[str, str]) -> None:
                cls._require_provider(active, fields.get("provider"))

            ctx.store.write_field("voice", name, check=check)
        except (OSError, ValueError) as exc:
            return cls._error(str(exc))
        return CommandResult(text=f"{name}'s here.", json_data={"voice": name})

    @staticmethod
    def _require_provider(target: str | None, active: str | None) -> str:
        if not active:
            raise ProviderNotConfiguredError.for_cli()
        if target and target.lower() != active.lower():
            msg = (
                f"Cannot set a voice for {target!r}: the active provider is {active!r}."
            )
            raise ValueError(msg)
        return active

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
        roster = VoiceRoster.fetch(ctx.client, provider) if provider else []
        if isinstance(roster, RosterError):
            return cls._error(roster.message)
        listing = SwitchList(names=tuple(roster), current=current)
        return CommandResult(
            text=listing.render("" if provider else "No voices for this provider."),
            json_data=listing.payload() | {"provider": provider},
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
