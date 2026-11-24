"""The seam between a real :class:`VoxPanelService` and a real ``PanelRunner``.

``PanelRunner._applied`` once collapsed
:meth:`~punt_vox.panel.service.VoxPanelService.apply_event`'s three-way
:class:`~punt_vox.panel.control_push.ControlPush` answer back down to a bare
truthy check (``ControlPush.REFRESH if changed else ControlPush.NONE``).
Because ``ControlPush`` is a plain ``Enum`` with no ``__bool__``, every member
-- ``NONE`` and ``CORRECT`` included -- is truthy, so the ternary always
answered ``REFRESH`` regardless of what ``apply_event`` actually returned.

No existing test caught it because none crossed the seam: ``test_panel_runner``
drives ``PanelRunner`` against ``FakeService``, whose own ``apply_event`` (a
bare ``bool``) made the buggy ternary look correct; ``test_service`` drives
``VoxPanelService`` directly and correctly sees ``ControlPush.CORRECT``, but
never through a ``PanelRunner``. This module wires the two real classes
together and drives a genuine roster-fetch failure through ``changed()``, the
same path a panel control's own event handler would.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final
from unittest.mock import MagicMock

import pytest
from punt_lux.operations import Ok, OpError

from panel.doubles import PANEL_LOGGER
from punt_vox.client_errors import VoxdConnectionError, VoxdRejectionError
from punt_vox.client_sync import VoxClientSync
from punt_vox.config import ConfigStore, VoxConfig
from punt_vox.panel.panel_guard import PanelGuard
from punt_vox.panel.panel_notice import PanelNotice
from punt_vox.panel.panel_runner import PanelRunner
from punt_vox.panel.service import VoxPanelService
from punt_vox.panel.topics import PanelTopic
from punt_vox.server_switches import PROVIDER_NAMES

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_lux import LuxClient, RenderRequest, SceneShown
    from punt_lux.hub_client import CallbackHandler, ConnectHandler, EventHandler
    from punt_lux.operations import UpdateRequest

    from punt_vox.client import SynthesizeResult
    from punt_vox.panel.ports import HubListener
    from punt_vox.types_synthesis import SynthesisSpec
    from punt_vox.voice_target import VoiceTarget


def _config(voice: str | None, provider: str | None) -> VoxConfig:
    return VoxConfig(
        notify="y",
        speak="y",
        vibe_mode="auto",
        voice=voice,
        provider=provider,
        model=None,
        vibe=None,
        vibe_tags=None,
    )


@final
class _Store:
    """A ``SettingsStore`` double: records every field a commit writes."""

    def __init__(self, cfg: VoxConfig) -> None:
        self._cfg = cfg
        self.written: dict[str, str] = {}

    def read(self) -> VoxConfig:
        return self._cfg

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self.written.update(updates)


@final
class _RosterFailsAfterPrefetch:
    """A ``PanelDaemonClient`` double: one clean roster read, then voxd drops.

    The prefetch needs a working roster so the panel starts with real state
    to snap back to; the provider commit's own roster fetch is the one that
    fails -- what a voxd that answers once and then drops looks like from a
    click mid-flight.
    """

    def __init__(
        self, error: VoxdConnectionError | VoxdRejectionError | None = None
    ) -> None:
        self.call_count = 0
        self._error = (
            error if error is not None else VoxdConnectionError("voxd unreachable")
        )

    def voices(self, provider: VoiceTarget) -> list[str]:
        self.call_count += 1
        if self.call_count > 1:
            raise self._error
        return ["benno", "aria"]

    def synthesize(
        self, text: str, spec: SynthesisSpec | None = None, *, once: int | None = None
    ) -> SynthesizeResult:
        raise NotImplementedError


@final
class _ProviderRosters:
    def voices(self, target: VoiceTarget) -> list[str]:
        return (
            ["benno", "aria"] if target.provider == "elevenlabs" else ["alloy", "nova"]
        )

    def synthesize(
        self, text: str, spec: SynthesisSpec | None = None, *, once: int | None = None
    ) -> SynthesizeResult:
        raise NotImplementedError


@final
class _SceneAccessor:
    """Records a full install (``show``) apart from a diff patch (``update``).

    They are recorded apart because a diff-based re-push cannot express the
    correction this test exists to prove: a ``show`` unconditionally
    reasserts every field, a patch only touches what moved against the last
    successfully pushed tree.
    """

    def __init__(self, outer: _Rest) -> None:
        self._outer = outer

    async def show(self, request: RenderRequest) -> SceneShown | OpError:
        self._outer.rendered.append(request)
        return cast("SceneShown", Ok())

    async def update(
        self, scene_id: str, request: UpdateRequest | OpError
    ) -> SceneShown | OpError:
        self._outer.patched.append(request)
        return cast("SceneShown", Ok())


@final
class _CallbackAccessor:
    async def register(
        self, callback_id: str, label: str, frame_id: str | None = None
    ) -> Ok | OpError:
        raise NotImplementedError


@final
class _Rest:
    """A ``PanelRestClient`` double recording installs apart from patches."""

    def __init__(self) -> None:
        self.rendered: list[RenderRequest] = []
        self.patched: list[UpdateRequest | OpError] = []
        self.scene = _SceneAccessor(self)
        self.callback = _CallbackAccessor()

    def listener(
        self,
        *,
        on_callback: CallbackHandler,
        on_event: EventHandler,
        on_connect: ConnectHandler | None = None,
    ) -> HubListener:
        raise NotImplementedError


def _selected(request: RenderRequest, element_id: str) -> object:
    """Return ``element_id``'s ``selected`` field from a full render, or raise."""
    for element in request.elements:
        if element.get("id") == element_id:
            return element["selected"]
    msg = f"no {element_id!r} element in the render"
    raise AssertionError(msg)


def _engine_combo(request: RenderRequest, label: str) -> tuple[str, object]:
    for element in request.elements:
        if element.get("label") == label:
            return str(element["id"]), element["selected"]
        if element.get("id") == "vox.panel.voice.row":
            for child in cast("list[dict[str, object]]", element["children"]):
                if child.get("label") == label:
                    return str(child["id"]), child["selected"]
    raise AssertionError(label)


class TestRunnerServiceSeam:
    """A real ``VoxPanelService`` driven through a real ``PanelRunner``."""

    @pytest.mark.parametrize("topic", [PanelTopic.VOICE, PanelTopic.MODEL])
    async def test_stale_openai_click_preserves_and_restores_espeak(
        self, tmp_path: Path, topic: PanelTopic
    ) -> None:
        store = ConfigStore(tmp_path)
        store.write_fields({"provider": "openai", "model": "tts-1", "voice": "alloy"})
        client = MagicMock(spec=VoxClientSync)
        rosters = {"openai": ["alloy", "ash"], "espeak": ["af"]}

        def voices(target: VoiceTarget) -> list[str]:
            return rosters[target.provider]

        client.voices.side_effect = voices
        service = VoxPanelService(client, store)
        rest = _Rest()

        def rest_factory() -> LuxClient:
            return cast("LuxClient", rest)

        guard = PanelGuard(service, rest_factory, PANEL_LOGGER)
        runner = PanelRunner(service, rest_factory, guard, PANEL_LOGGER)
        await runner.warmed()
        await service.push_scene(rest_factory())
        assert len(rest.rendered) == 1
        assert _selected(rest.rendered[0], "vox.panel.provider") == (
            PROVIDER_NAMES.index("openai") + 1
        )

        store.write_fields({"provider": "espeak", "model": "", "voice": "af"})
        expected = store.read()
        element_id, _ = _engine_combo(rest.rendered[0], topic.field_name.capitalize())
        await runner.changed(topic.value, {"element_id": element_id, "value": 2})

        assert store.read() == expected
        assert len(rest.rendered) == 2
        assert rest.patched == []
        assert _selected(rest.rendered[1], "vox.panel.provider") == (
            PROVIDER_NAMES.index("espeak") + 1
        )
        assert service.scene().model is None
        assert service.scene().voice == "af"

    @pytest.mark.parametrize("external", [True, False], ids=["external", "panel"])
    async def test_a_delayed_roster_event_resyncs_without_writing(
        self, tmp_path: Path, external: bool
    ) -> None:
        store = ConfigStore(tmp_path)
        store.write_fields(
            {"provider": "elevenlabs", "model": "eleven_v3", "voice": "benno"}
        )
        service = VoxPanelService(_ProviderRosters(), store)
        rest = _Rest()

        def rest_factory() -> LuxClient:
            return cast("LuxClient", rest)

        runner = PanelRunner(
            service,
            rest_factory,
            PanelGuard(service, rest_factory, PANEL_LOGGER),
            PANEL_LOGGER,
        )
        await runner.warmed()
        await service.install_scene(rest_factory())
        old_voice, _ = _engine_combo(rest.rendered[0], "Voice")
        old_model, _ = _engine_combo(rest.rendered[0], "Model")

        if external:
            store.write_fields(
                {"provider": "openai", "model": "tts-1", "voice": "alloy"}
            )
            await runner.reconciled()
        else:
            await runner.changed(
                PanelTopic.PROVIDER, {"value": PROVIDER_NAMES.index("openai") + 1}
            )

        confirmed = store.read()
        renders_before = len(rest.rendered)
        await runner.changed(PanelTopic.VOICE, {"element_id": old_voice, "value": 2})
        assert store.read() == confirmed
        await runner.changed(PanelTopic.MODEL, {"element_id": old_model, "value": 2})
        assert store.read() == confirmed
        assert len(rest.rendered) == renders_before + 2

        new_voice, voice_selected = _engine_combo(rest.rendered[-1], "Voice")
        new_model, model_selected = _engine_combo(rest.rendered[-1], "Model")
        assert (new_voice, new_model) != (old_voice, old_model)
        assert (voice_selected, model_selected) == (1, 1)
        await runner.changed(PanelTopic.VOICE, {"element_id": new_voice, "value": 2})
        assert store.read().voice == "nova"
        await runner.changed(PanelTopic.MODEL, {"element_id": new_model, "value": 2})
        assert store.read().model == "tts-1-hd"

    async def test_an_external_voice_write_patches_only_an_installed_panel(
        self, tmp_path: Path
    ) -> None:
        store = ConfigStore(tmp_path)
        store.write_fields({"provider": "elevenlabs", "voice": "benno"})
        client = _RosterFailsAfterPrefetch()
        service = VoxPanelService(client, store)
        rest = _Rest()

        def rest_factory() -> LuxClient:
            return cast("LuxClient", rest)

        guard = PanelGuard(service, rest_factory, PANEL_LOGGER)
        runner = PanelRunner(service, rest_factory, guard, PANEL_LOGGER)
        await runner.warmed()
        await runner.reconciled()
        assert rest.rendered == []
        assert rest.patched == []

        await service.install_scene(rest_factory())
        ConfigStore(tmp_path).write_field("voice", "aria")
        await runner.reconciled()
        await runner.reconciled()

        assert client.call_count == 1
        assert len(rest.rendered) == 1
        assert len(rest.patched) == 1
        assert not isinstance(rest.patched[0], OpError)
        assert rest.patched[0].to_wire() == [
            {"id": _engine_combo(rest.rendered[0], "Voice")[0], "set": {"selected": 2}}
        ]

    async def test_a_reverted_external_edit_clears_only_the_poll_warning(
        self, tmp_path: Path
    ) -> None:
        store = ConfigStore(tmp_path)
        store.write_field("provider", "elevenlabs")
        service = VoxPanelService(_RosterFailsAfterPrefetch(), store)
        service.prefetch()
        await service.install_scene(cast("LuxClient", _Rest()))

        store.write_field("provider", "espeak")
        assert await service.reconcile()
        assert service.scene().notice == PanelNotice.voxd_unavailable()

        store.write_field("provider", "elevenlabs")
        assert await service.reconcile()
        assert not service.scene().notice.is_present

        service.note_control_rejected("voice")
        store.write_field("voice", "aria")
        assert await service.reconcile()
        assert service.scene().notice == PanelNotice.control_rejected("voice")

    async def test_a_refused_external_roster_fetch_keeps_the_confirmed_picker(
        self, tmp_path: Path
    ) -> None:
        store = ConfigStore(tmp_path)
        store.write_fields({"provider": "elevenlabs", "voice": "benno"})
        service = VoxPanelService(
            _RosterFailsAfterPrefetch(VoxdRejectionError("roster refused")), store
        )
        service.prefetch()
        await service.install_scene(cast("LuxClient", _Rest()))
        before = service.scene()

        store.write_fields({"provider": "openai", "voice": "alloy"})
        assert await service.reconcile()
        assert service.scene().notice == PanelNotice.voxd_rejected("roster refused")
        assert service.scene().roster == before.roster
        assert service.scene().provider == before.provider
        assert service.scene().voice == before.voice

    async def test_an_external_rejected_model_can_be_repaired_on_the_first_pick(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        voices = _ProviderRosters.voices

        def reject_unknown_model(
            client: _ProviderRosters, target: VoiceTarget
        ) -> list[str]:
            if target.model == "unknown-model":
                msg = "unknown model"
                raise VoxdRejectionError(msg)
            return voices(client, target)

        monkeypatch.setattr(_ProviderRosters, "voices", reject_unknown_model)
        store = ConfigStore(tmp_path)
        store.write_fields({"provider": "openai", "model": "tts-1", "voice": "alloy"})
        service = VoxPanelService(_ProviderRosters(), store)
        rest = _Rest()

        def rest_factory() -> LuxClient:
            return cast("LuxClient", rest)

        runner = PanelRunner(
            service,
            rest_factory,
            PanelGuard(service, rest_factory, PANEL_LOGGER),
            PANEL_LOGGER,
        )
        await runner.warmed()
        await service.install_scene(rest_factory())
        store.write_field("model", "unknown-model")
        await runner.reconciled()
        assert service.scene().notice == PanelNotice.voxd_rejected("unknown model")
        model_id, _ = _engine_combo(rest.rendered[-1], "Model")
        await runner.changed(PanelTopic.MODEL, {"element_id": model_id, "value": 2})
        assert store.read().model == "tts-1-hd"

    async def test_an_unreadable_config_and_control_notices_both_show(
        self, tmp_path: Path
    ) -> None:
        store = ConfigStore(tmp_path, durable_only=True)
        store.write_fields({"provider": "elevenlabs", "voice": "benno"})
        service = VoxPanelService(_ProviderRosters(), store)
        service.prefetch()
        await service.install_scene(cast("LuxClient", _Rest()))
        control_notice = PanelNotice.control_rejected("voice")
        service.note_control_rejected("voice")
        assert service.scene().notice == control_notice
        path = tmp_path / "vox.md"
        original = path.read_bytes()

        path.write_bytes(b"\xff")
        assert await service.reconcile()
        unreadable = PanelNotice.config_unreadable()
        assert (
            service.scene().notice.message
            == f"{control_notice.message}\n{unreadable.message}"
        )

        service.note_control_rejected("model")
        latest_control = PanelNotice.control_rejected("model")
        assert (
            service.scene().notice.message
            == f"{latest_control.message}\n{unreadable.message}"
        )

        path.write_bytes(original)
        assert await service.reconcile()
        assert service.scene().notice == latest_control

    @pytest.mark.parametrize("external", [True, False], ids=["poll", "menu"])
    @pytest.mark.parametrize(
        ("bad_config", "diagnostic"),
        [
            pytest.param(b"\xff", "UnicodeDecodeError", id="invalid-utf8"),
            pytest.param(
                b"---\nprovider: elevenlabs\nvoice: aria\n",
                "no closing ---",
                id="unclosed-frontmatter",
            ),
        ],
    )
    async def test_an_unreadable_config_cannot_replace_confirmed_settings(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        external: bool,
        bad_config: bytes,
        diagnostic: str,
    ) -> None:
        store = ConfigStore(tmp_path, durable_only=True)
        store.write_fields({"provider": "elevenlabs", "voice": "benno"})
        service = VoxPanelService(_RosterFailsAfterPrefetch(), store)
        service.prefetch()
        await service.install_scene(cast("LuxClient", _Rest()))
        path = tmp_path / "vox.md"
        original = path.read_bytes()

        path.write_bytes(bad_config)
        if external:
            assert await service.reconcile()
        else:
            assert service.refresh()
        assert service.scene().voice == "benno"
        assert service.scene().notice == PanelNotice.config_unreadable()
        assert diagnostic in caplog.text
        assert not await service.reconcile()

        path.write_bytes(original)
        assert await service.reconcile()
        assert service.scene().voice == "benno"
        assert not service.scene().notice.is_present

    async def test_an_unreadable_local_file_does_not_block_durable_refresh(
        self, tmp_path: Path
    ) -> None:
        store = ConfigStore(tmp_path, durable_only=True)
        store.write_fields({"provider": "elevenlabs", "voice": "benno"})
        service = VoxPanelService(_RosterFailsAfterPrefetch(), store)
        service.prefetch()
        await service.install_scene(cast("LuxClient", _Rest()))

        (tmp_path / "vox.local.md").write_bytes(b"\xff")
        store.write_field("voice", "aria")
        assert await service.reconcile()
        assert service.scene().voice == "aria"
        assert not service.scene().notice.is_present

    async def test_a_roster_fetch_failure_during_a_provider_commit_corrects(
        self,
    ) -> None:
        client = _RosterFailsAfterPrefetch()
        store = _Store(_config(voice="benno", provider="elevenlabs"))
        service = VoxPanelService(client, store)
        rest = _Rest()

        def rest_factory() -> LuxClient:
            return cast("LuxClient", rest)

        guard = PanelGuard(service, rest_factory, PANEL_LOGGER)
        runner = PanelRunner(service, rest_factory, guard, PANEL_LOGGER)

        # Establish the panel's first render -- the "last successfully pushed"
        # baseline a diff-based re-push would compare its next plan against.
        await runner.warmed()
        await service.push_scene(rest_factory())
        assert len(rest.rendered) == 1
        # One past its position in the enum: the combo leads with ``(none)``.
        elevenlabs_index = PROVIDER_NAMES.index("elevenlabs") + 1
        assert _selected(rest.rendered[0], "vox.panel.provider") == elevenlabs_index

        # The widget already shows "espeak" the instant the click fires; the
        # roster fetch behind this commit then fails, so ``_state`` never
        # moves off "elevenlabs" -- the regression this seam test
        # guards: a diff-based re-push always looked like the right answer
        # against a fake that couldn't tell REFRESH from CORRECT.
        espeak_index = PROVIDER_NAMES.index("espeak") + 1
        await runner.changed(PanelTopic.PROVIDER.value, {"value": espeak_index})

        # A full reinstall, not a diff patch: the corrective render must
        # reassert the control's true (unchanged) field over the widget's
        # wrong guess, which a patch -- finding nothing changed to diff --
        # would skip entirely.
        assert len(rest.rendered) == 2
        assert rest.patched == []
        assert _selected(rest.rendered[1], "vox.panel.provider") == elevenlabs_index
        assert store.written == {}
