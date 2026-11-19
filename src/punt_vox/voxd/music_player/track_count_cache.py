"""Refresh cached track counts without blocking playback or the Lux listener.

Manifest reads run in a worker thread, serialized behind a lock with a bounded
wait. A timeout releases the lock but cannot stop that thread. Each dispatch
captures a generation number before leaving the event loop; CountStore rejects
late results older than the last committed generation. A late landing notifies
the originating loop so committed counts become visible without another event.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.music_player.count_reader import CountReader
from punt_vox.voxd.music_player.count_store import CountStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.programs.album_id import AlbumId
    from punt_vox.voxd.programs.catalog import Album

__all__ = ["TrackCountCache"]

logger = logging.getLogger(__name__)

# Stateless: one shared instance rather than a new one per refresh.
_READER: CountReader = CountReader()


@final
class _NoObserver:
    """Null Object (PY-DP-9): the observer of a cache nobody is watching.

    A cache built without one -- every test that only reads counts back --
    still has to answer the same call when a write lands. Doing nothing is
    the honest answer there, and a stand-in that answers it keeps the notify
    path free of an is-there-an-observer branch only tests would ever take.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a no observer."""
        return super().__new__(cls)

    def __call__(self) -> None:
        """Absorb the repaint request; there is nobody to pass it to."""


# Stateless, so one shared instance stands in for every unwatched cache.
_NOBODY: _NoObserver = _NoObserver()

# A disk read across a real catalog should never legitimately take this long;
# past this, the caller treats the worker thread as stuck rather than block on
# it indefinitely.
_REFRESH_TIMEOUT_SECONDS: float = 5.0


@final
class TrackCountCache:
    """The last-known ready-track count per album, refreshed off the hot path.

    ``@final``, with a leading underscore on every method but :meth:`get` and
    :meth:`serialized_refresh`, to signal internal-use-only -- Python enforces
    neither: there is no true private method, and a caller holding this
    instance can still call :meth:`_refresh` directly, generation argument and
    all, if it chooses to ignore the convention. That is not a gap this class
    tries to close structurally -- a caller determined to corrupt
    the stored counts could just as easily set :attr:`_generation` directly,
    whether or not :meth:`_refresh` takes a parameter. The
    generation is threaded through :meth:`_refresh` as a plain parameter, not
    read back off :attr:`_generation` at execution time, because that is what
    correctness actually requires here: it must be the value captured at
    THIS call's dispatch, not whatever :attr:`_generation` happens to hold by
    the time this call's worker thread gets around to running (see module
    docstring for the concurrent interleaving this closes). The convention
    (leading underscore, ``@final``) is what keeps :meth:`_refresh`
    internal-only in practice; it was never a real enforcement boundary.
    """

    __slots__ = (
        "_generation",
        "_lock",
        "_loop",
        "_on_late_landing",
        "_store",
    )
    # The counts and the rule for replacing them. Held rather than inherited
    # so this class keeps one job -- deciding when to read disk -- and the
    # generation ordering that guards a late write lives with the data it
    # guards. See :mod:`punt_vox.voxd.music_player.count_store`.
    _store: CountStore
    # Guards :meth:`serialized_refresh` so overlapping callers run one at a
    # time instead of racing to overwrite the store with whichever thread
    # happens to finish last. Released early by a timeout -- see module
    # docstring.
    _lock: asyncio.Lock
    # The loop a refresh was dispatched from, captured at dispatch so a write
    # committed on a worker thread can post its repaint back. ``None`` until
    # the first async refresh, and a loop that has since closed once the
    # daemon is stopping.
    _loop: asyncio.AbstractEventLoop | None
    # How the cache asks for a repaint when a write lands; a Null Object when
    # nobody is watching (see ``__new__``).
    _on_late_landing: Callable[[], None]
    # Bumped by ``+= 1`` in :meth:`serialized_refresh` only, on the event
    # loop. Each bump captures the new value into a local variable in the
    # same statement and threads it explicitly into :meth:`_refresh` as a
    # parameter; :meth:`_refresh` itself never reads or writes this
    # attribute.
    _generation: int

    def __new__(cls, on_late_landing: Callable[[], None] = _NOBODY) -> Self:
        """Build the cache; *on_late_landing* is how it asks for a repaint.

        Called on the event loop when a refresh the caller had already given
        up waiting for finally lands its counts -- see
        :meth:`_announce_landing`. It defaults to a Null Object (PY-DP-9) so
        a cache built with no observer, as every test that only reads counts
        does, simply has nobody to tell.
        """
        self = super().__new__(cls)
        self._store = CountStore()
        self._lock = asyncio.Lock()
        self._generation = 0
        self._on_late_landing = on_late_landing
        self._loop = None
        return self

    def _announce_landing(self) -> None:
        """Ask for a repaint from whichever thread just committed a write.

        Called after the store has released its own lock, never under it:
        the observer renders, and holding a lock across a caller's work is
        how a cache ends up owning something it should not.

        The write may have happened on a worker thread whose caller gave up
        waiting for it seconds ago. That is the case this exists for -- the
        caller has already repainted from the stale cache, so without a
        signal here the live counts sit in memory, correct and invisible,
        until some unrelated change happens to schedule another refresh. On
        an idle catalog that is never.

        Every committed write announces, not only a late one, because the
        cache cannot tell which is which: whether a caller is still waiting
        is the caller's state, not this cache's. Announcing on a write whose
        caller is about to repaint anyway costs one extra render that the
        scene diff answers with no push at all -- the cheap half of a trade
        whose expensive half is a count that never appears.

        The hop back through the loop is what makes this safe to call from a
        worker thread. A loop already closed (the daemon is shutting down)
        raises, and there is nothing left to repaint by then.
        """
        if self._loop is None:
            return  # no refresh has been dispatched yet; nothing to post to
        try:
            self._loop.call_soon_threadsafe(self._on_late_landing)
        except RuntimeError:
            logger.debug("music: no live loop to repaint on; the daemon is stopping")

    def get(self, album_id: AlbumId) -> int:
        """Return the last-known ready-track count for ``album_id``, or zero."""
        return self._store.get(album_id)

    async def serialized_refresh(self, albums: tuple[Album, ...]) -> None:
        """Refresh off the event loop via :meth:`_refresh`, one caller at a time.

        This is the entry point the real, async path always uses. The one
        production caller today, the single-flighted background repaint, only
        ever has one logical refresh in flight at a time -- but that guarantee
        is the caller's, not this method's: a timed-out call's orphaned
        dispatch (see module docstring) keeps running and keeps holding the
        lock well after its own caller gave up, so the very next call from
        that same single-flighted caller still has to wait its turn behind it
        rather than race it. Without serialization, both would independently
        build a ``fresh`` dict from their own snapshot and unconditionally
        overwrite the store -- whichever thread happens to finish LAST wins,
        not whichever started most recently. The lock is acquired here,
        on the event loop, before the thread dispatch -- never inside
        :meth:`_refresh` itself, which runs in a worker thread once dispatched
        and cannot await an ``asyncio.Lock`` there -- so a caller that arrives
        while the orphan is still running simply waits its turn instead of
        racing it.

        Bounded by :data:`_REFRESH_TIMEOUT_SECONDS`: a stuck disk read gives up
        the wait rather than blocking this caller (and everyone serialized
        behind it) forever. The caller is answered with whatever the cache
        already held, while the abandoned worker thread goes on reading. Its
        write, if it ever lands, announces itself through
        :meth:`_announce_landing` so those counts reach the display rather
        than sitting in memory correct and invisible; :attr:`_generation`
        keeps it from clobbering a newer write that beat it home (see module
        docstring).

        Only OUR deadline is swallowed. A real disk-level timeout (``OSError``
        with ``errno.ETIMEDOUT``, e.g. a hung NFS mount) is also a
        ``TimeoutError`` in Python (a built-in ``OSError`` subclass), and would
        be caught by an unqualified ``except TimeoutError`` just the same --
        silently misreporting a genuine fault as "gave up waiting" and hiding
        it from :meth:`~punt_vox.voxd.music_player.player.MusicPlayer.
        _try_refresh_cache`. :meth:`asyncio.wait_for` only cancels its inner
        task when ITS OWN deadline elapses, so checking ``task.cancelled()``
        distinguishes the two: cancelled means the deadline fired and this is
        our timeout; not cancelled means the task ran to completion and raised
        that ``TimeoutError`` itself, which is re-raised unchanged.
        """
        self._generation += 1
        generation = self._generation
        self._loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(self._locked_refresh(albums, generation))
        try:
            await asyncio.wait_for(task, timeout=_REFRESH_TIMEOUT_SECONDS)
        except TimeoutError:
            if not task.cancelled():
                raise  # a genuine fault from the read itself, not our deadline
            logger.warning(
                "music: track-count refresh for %d albums exceeded %.1fs; "
                "abandoning the wait (its counts repaint if and when they land)",
                len(albums),
                _REFRESH_TIMEOUT_SECONDS,
            )

    async def _locked_refresh(self, albums: tuple[Album, ...], generation: int) -> None:
        """Hold :attr:`_lock` across the dispatch, so overlapping callers queue."""
        async with self._lock:
            await asyncio.to_thread(self._refresh, albums, generation)

    def _refresh(self, albums: tuple[Album, ...], generation: int) -> None:
        """Re-read every album's live count from disk; the one blocking call here.

        Intended to run only via :meth:`serialized_refresh` -- a direct call
        runs inline, blocking whatever event loop called it, and races any
        concurrent :meth:`serialized_refresh` in flight. Only ``LookupError``
        (the store's documented "this album was deleted" contract) drops an
        album from the refreshed set; any other fault -- a transient
        ``OSError`` from a permission blip or a descriptor exhaustion --
        propagates rather than silently freezing that album's count at its
        last-known value.

        ``generation`` is captured by the caller at DISPATCH time (in
        :meth:`serialized_refresh`, synchronously, before this method's worker
        thread is even created) and handed to this method as a plain
        parameter -- it is never read back off :attr:`_generation` once the
        worker thread actually starts running. Two overlapping calls to
        :meth:`serialized_refresh` share that one counter, and it is bumped
        again on every new dispatch; if this method read :attr:`_generation`
        itself instead of being handed the value that was current at ITS OWN
        dispatch, a call whose worker thread is slow to start could read a
        LATER call's bump instead of its own, write stale data under a newer
        generation number, and cause a genuinely fresher, later-dispatched
        refresh to lose to it -- exactly the race this whole mechanism exists
        to prevent, reachable via two ordinary, fully-successful calls with no
        timeout involved at all.

        The write is handed to :class:`~punt_vox.voxd.music_player.
        count_store.CountStore`, which takes it only when ``generation``
        beats the last write that actually landed -- this method may run on a
        worker thread orphaned by a caller that already timed out, so a late
        write must never clobber a newer result. The store answers whether it
        took the write, and only a write that was taken announces itself.
        """
        if self._store.commit(_READER.read(albums), generation):
            self._announce_landing()
