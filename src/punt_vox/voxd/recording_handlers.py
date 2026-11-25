"""Wire the voxd recording and fetch handlers to one shared store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from punt_vox.paths import recordings_dir
from punt_vox.voxd.fetch_handler import FetchHandler
from punt_vox.voxd.play_handler import PlayHandler
from punt_vox.voxd.rec_handlers import RecRemoveHandler
from punt_vox.voxd.rec_list_handler import RecListHandler
from punt_vox.voxd.record_handler import RecordHandler
from punt_vox.voxd.record_store import RecordStore

if TYPE_CHECKING:
    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.programs.library import MusicLibrary
    from punt_vox.voxd.synthesis import SynthesisPipeline
    from punt_vox.voxd.types import MessageHandler


class RecordingHandlers:
    """Compose handlers that share the same recording root and path checks."""

    @staticmethod
    def build(
        synthesis: SynthesisPipeline, playback: PlaybackQueue, library: MusicLibrary
    ) -> dict[str, MessageHandler]:
        """Return the recording and fetch endpoints for one daemon."""
        store = RecordStore(recordings_dir())
        return {
            "record": RecordHandler(synthesis=synthesis, store=store),
            "play": PlayHandler(playback=playback, store=store),
            "fetch": FetchHandler(store=store, music=library),
            "rec_list": RecListHandler(store),
            "rec_remove": RecRemoveHandler(store),
        }
