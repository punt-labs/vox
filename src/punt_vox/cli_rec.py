"""The ``vox rec`` CLI -- author, list, play, copy out, and delete recordings.

The recordings store is one daemon-owned directory of MP3s. :class:`RecCli` is a
humble object: each verb parses its arguments, calls one :class:`RecordGateway`
method, and formats the result through the shared :class:`OutputFormatter` -- no
transport detail and no daemon logic (the daemon owns the store and every path
decision). The gateway is a thin protocol so tests inject an in-memory store
instead of a live socket; production backs it with :class:`ClientRecordGateway`
over :class:`VoxClientSync`. Every id-bearing verb takes a bare store id, never a
path.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Protocol,
    Self,
    final,
    runtime_checkable,
)

import typer

from punt_vox.bare_name import BareName
from punt_vox.cli_guard import GATEWAY_ERRORS, CliGuard
from punt_vox.cli_io import OutputFlags
from punt_vox.cli_synthesis import CliSynthesis
from punt_vox.client_errors import FetchWriteError
from punt_vox.client_sync import VoxClientSync
from punt_vox.output_formatter import OutputFormatter
from punt_vox.recording_download import RecordingDownload
from punt_vox.types_cli_options import (
    FromOpt,
    LanguageOpt,
    ModelOpt,
    ProviderOpt,
    RateOpt,
    SimilarityOpt,
    SpeakerBoostFlag,
    StabilityOpt,
    StyleOpt,
    VoiceOpt,
)
from punt_vox.types_synthesis import SynthesisSpec

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import IO

    from punt_vox.client import RecordingSummary, RecordResult

__all__ = ["RecCli", "build_rec_app"]


@runtime_checkable
class RecordGateway(Protocol):
    """The recordings-store operations a client surface issues against ``voxd``."""

    def new(self, text: str, spec: SynthesisSpec, name: str | None) -> RecordResult:
        """Synthesize *text* into the store; return the daemon's locator."""
        ...

    def recordings(self) -> tuple[RecordingSummary, ...]:
        """Return the store's recordings (name + bytes)."""
        ...

    def play(self, ref: str) -> None:
        """Play recording *ref* on the daemon host."""
        ...

    def get_into(self, ref: str, sink: IO[bytes]) -> int:
        """Stream recording *ref* into *sink* and return the verified byte count."""
        ...

    def remove(self, ref: str) -> None:
        """Delete recording *ref* from the store."""
        ...


@final
class ClientRecordGateway:
    """Back the ``RecordGateway`` seam with WebSocket calls to ``voxd``."""

    __slots__ = ("_client",)
    _client: VoxClientSync

    def __new__(cls, client: VoxClientSync) -> Self:
        """Create a client record gateway."""
        self = super().__new__(cls)
        self._client = client
        return self

    def new(self, text: str, spec: SynthesisSpec, name: str | None) -> RecordResult:
        """Synthesize *text* into the store via the ``record`` op."""
        return self._client.record(text, spec, name=name)

    def recordings(self) -> tuple[RecordingSummary, ...]:
        """Return the store's recordings via the ``rec_list`` op."""
        return self._client.rec_list()

    def play(self, ref: str) -> None:
        """Play recording *ref* on the daemon host via the ``play`` op."""
        self._client.play(ref)

    def get_into(self, ref: str, sink: IO[bytes]) -> int:
        """Stream recording *ref* via the chunked ``fetch`` op."""
        return self._client.fetch_into(ref, sink)

    def remove(self, ref: str) -> None:
        """Delete recording *ref* via the ``rec_remove`` op."""
        self._client.rec_remove(ref)


# Per-verb aliases for options defined by the recording CLI.
_TextArg = Annotated[
    str | None, typer.Argument(help="Text to synthesize.", show_default=False)
]
_NameOpt = Annotated[
    str | None,
    typer.Option(
        "--name",
        help="Bare filename. Unnamed same-text takes overwrite. Use distinct names.",
    ),
]
_RefArg = Annotated[str, typer.Argument(help="Bare store recording id.")]
_JsonOutput = Annotated[bool, typer.Option("--json", help="Output JSON.")]
_Verbose = Annotated[
    bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
]
_Quiet = Annotated[
    bool, typer.Option("--quiet", "-q", help="Suppress non-JSON output.")
]


@final
class RecCli:
    """The recordings-store command implementations (a humble object)."""

    __slots__ = ("_flags", "_formatter", "_gateway_factory", "_guard", "_synthesis")
    _formatter: OutputFormatter
    _gateway_factory: Callable[[], RecordGateway]
    _guard: CliGuard
    _flags: OutputFlags
    _synthesis: CliSynthesis

    def __new__(
        cls,
        formatter: OutputFormatter,
        gateway_factory: Callable[[], RecordGateway] | None = None,
        flags: OutputFlags | None = None,
    ) -> Self:
        """Create a rec cli."""
        self = super().__new__(cls)
        self._formatter = formatter
        self._guard = CliGuard(formatter)
        self._synthesis = CliSynthesis(formatter)
        self._gateway_factory = gateway_factory or cls._default_gateway
        self._flags = flags if flags is not None else OutputFlags(formatter)
        return self

    @staticmethod
    def _default_gateway() -> RecordGateway:
        """Build the production gateway -- a fresh WebSocket client per command."""
        return ClientRecordGateway(VoxClientSync())

    def new(
        self,
        text: _TextArg = None,
        from_file: FromOpt = None,
        voice: VoiceOpt = None,
        language: LanguageOpt = None,
        rate: RateOpt = 90,
        name: _NameOpt = None,
        provider: ProviderOpt = None,
        model: ModelOpt = None,
        stability: StabilityOpt = None,
        similarity: SimilarityOpt = None,
        style: StyleOpt = None,
        speaker_boost: SpeakerBoostFlag = False,  # noqa: FBT002 -- typer bool default
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Synthesize speech into the store and print its bare id.

        Reads text from the TEXT argument, ``--from`` (a JSON segments file), or
        stdin. Prints only the store id -- no path, no host. Act on it with
        ``rec play``/``rec get``/``rec remove``.

        Without --name, recording the same text reuses the same id and
        silently replaces the earlier take, even if synthesis settings change.
        Pass a different --name for each take to keep them separate.
        --name only works for a single text segment.
        """
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        segments, spec = self._synthesis.prepare(
            text,
            from_file,
            voice=voice,
            language=language,
            rate=rate,
            provider=provider,
            model=model,
            stability=stability,
            similarity=similarity,
            style=style,
            speaker_boost=speaker_boost,
        )
        self._guard_name(name, segments)
        gateway = self._guard.run(self._gateway_factory)
        for seg_text in segments:
            result = self._guard.run(partial(gateway.new, seg_text, spec, name))
            self._formatter.warn(result.warning)
            self._formatter.emit(
                {
                    "id": result.name,
                    "bytes": result.byte_count,
                    "cached": result.cached,
                },
                result.name,
            )

    def list_recordings(
        self,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """List the store's recording ids, one per line (``--json`` for bytes)."""
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        rows: list[dict[str, str | int]] = []
        names: list[str] = []
        for entry in self._guard.run(lambda: self._gateway_factory().recordings()):
            rows.append({"id": entry.name, "bytes": entry.byte_count})
            names.append(entry.name)
        listing = "\n".join(names) or "No recordings."
        self._formatter.emit({"recordings": rows}, listing)

    def play(
        self,
        ref: _RefArg,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Play recording *ref* on the daemon host."""
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._guard.run(lambda: self._gateway_factory().play(ref))
        self._formatter.emit(
            {"played": ref}, f"played store recording {ref} on the daemon host"
        )

    def get(
        self,
        ref: _RefArg,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Copy recording *ref* into the current directory under its store name."""
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        dest = Path.cwd() / self._bare_ref(ref)
        try:
            count = RecordingDownload(dest).save(
                lambda sink: self._fetch_download(ref, sink)
            )
        except FileExistsError:
            self._guard.fail(f"rec get: ./{ref} exists")
        except OSError as exc:
            self._guard.fail(f"cannot write {dest}: {exc}")
        self._formatter.emit({"path": str(dest), "bytes": count}, f"./{ref}")

    def _fetch_download(self, ref: str, sink: IO[bytes]) -> int:
        """Report gateway errors separately from errors writing to the sink."""
        try:
            return self._gateway_factory().get_into(ref, sink)
        except FetchWriteError:
            raise
        except GATEWAY_ERRORS as exc:
            self._guard.fail(str(exc))

    def remove(
        self,
        ref: _RefArg,
        *,
        json_output: _JsonOutput = False,
        verbose: _Verbose = False,
        quiet: _Quiet = False,
    ) -> None:
        """Delete recording *ref* from the store."""
        self._flags.apply(json_output=json_output, verbose=verbose, quiet=quiet)
        self._guard.run(lambda: self._gateway_factory().remove(ref))
        self._formatter.emit({"removed": ref}, f"removed {ref}")

    def _bare_ref(self, ref: str) -> str:
        """Return *ref* as a bare filename, failing cleanly if it escapes the CWD.

        ``rec get`` joins *ref* onto the working directory to land the fetched
        bytes, so a ref with a separator, a ``.``/``..`` token, or an absolute
        path would write outside it. :class:`BareName` rejects those before the
        join, so the destination is always a plain file in the CWD.
        """
        try:
            return BareName(ref, "recording id").value
        except ValueError as exc:
            self._guard.fail(str(exc))

    def _guard_name(self, name: str | None, segments: list[str]) -> None:
        """Reject an empty name, or ``--name`` given for multiple segments."""
        if name == "":
            # The daemon is the single authority on name validity; the CLI
            # fast-fails an empty name before a wasted round-trip.
            self._guard.fail("--name must not be empty")
        if name and len(segments) > 1:
            self._guard.fail("--name supports a single segment only")


def build_rec_app(formatter: OutputFormatter, flags: OutputFlags) -> typer.Typer:
    """Return the ``vox rec`` Typer group with bound methods (no wrappers)."""
    cli = RecCli(formatter, flags=flags)
    app = typer.Typer(
        help="Author and manage stored recordings.",
        no_args_is_help=True,
    )
    app.command("new")(cli.new)
    app.command("list")(cli.list_recordings)
    app.command("play")(cli.play)
    app.command("get")(cli.get)
    app.command("remove")(cli.remove)
    return app
