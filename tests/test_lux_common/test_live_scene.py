"""Tests for :mod:`punt_vox.lux_common.live_scene`.

``LiveScene`` is a state machine over one field, ``_previous``. Each modeled
property is pinned by exactly one test of the same name:

- ``starts_disarmed`` -- a new scene believes nothing is installed.
- ``disarmed_first_push_installs`` -- with nothing installed, ``plan`` installs.
- ``push_arms`` -- any ``plan`` leaves the scene armed.
- ``identical_render_is_no_push`` -- an unchanged render pushes nothing.
- ``changed_value_patches`` -- a moved value patches only that element.
- ``changed_child_value_patches_child`` -- a moved value inside a group patches
  the child, never the group's roster.
- ``roster_change_reinstalls`` -- an added or removed element installs again.
- ``shell_change_reinstalls`` -- a changed scene id, title, layout, or frame
  spec installs again, since none has a patch seam.
- ``explicit_install_always_installs`` -- ``install`` installs even when the
  render is identical.
- ``explicit_install_arms`` -- after ``install`` the next ``plan`` can patch.
- ``disarm_returns_to_install`` -- ``disarm`` returns to the not-installed
  state, so the next ``plan`` installs.
"""

from __future__ import annotations

import pytest
from punt_lux import RenderRequest
from punt_lux.operations.models import FrameSpec

from punt_vox.lux_common.live_scene import LiveScene
from punt_vox.lux_common.scene_push import InstallScene, NoPush, PatchScene


def _scene(
    *elements: dict[str, object],
    scene_id: str = "vox.music",
    title: str = "Music",
    # Absence is lux's own contract here: no frame spec means the default frame.
    frame: FrameSpec | None = None,
) -> RenderRequest:
    return RenderRequest(
        scene_id=scene_id,
        elements=list(elements),
        title=title,
        frame=frame,
    )


def _text(element_id: str, content: str) -> dict[str, object]:
    return {"kind": "text", "id": element_id, "content": content}


def _row(label: str) -> dict[str, object]:
    return {
        "kind": "group",
        "id": "row",
        "children": [{"kind": "button", "id": "row.go", "label": label}],
    }


def test_starts_disarmed() -> None:
    assert not LiveScene().armed


def test_disarmed_first_push_installs() -> None:
    assert isinstance(LiveScene().plan(_scene(_text("a", "1"))), InstallScene)


def test_push_arms() -> None:
    live = LiveScene()
    live.plan(_scene(_text("a", "1")))
    assert live.armed


def test_identical_render_is_no_push() -> None:
    live = LiveScene()
    live.plan(_scene(_text("a", "1")))
    assert isinstance(live.plan(_scene(_text("a", "1"))), NoPush)


def test_changed_value_patches() -> None:
    live = LiveScene()
    live.plan(_scene(_text("a", "1"), _text("b", "2")))
    push = live.plan(_scene(_text("a", "9"), _text("b", "2")))
    assert isinstance(push, PatchScene)
    assert [patch.element_id for patch in push.patches.patches] == ["a"]


def test_changed_child_value_patches_child() -> None:
    live = LiveScene()
    live.plan(_scene(_row("Play")))
    push = live.plan(_scene(_row("Pause")))
    assert isinstance(push, PatchScene)
    assert push.patches.patches[0].element_id == "row.go"
    assert "children" not in push.patches.patches[0].fields


def test_roster_change_reinstalls() -> None:
    live = LiveScene()
    live.plan(_scene(_text("a", "1")))
    assert isinstance(live.plan(_scene(_text("a", "1"), _text("b", "2"))), InstallScene)


_ELEMENTS: list[dict[str, object]] = [_text("a", "1")]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            _scene(*_ELEMENTS),
            _scene(*_ELEMENTS, title="Other"),
            id="title",
        ),
        pytest.param(
            _scene(*_ELEMENTS, frame=FrameSpec(size=(10, 10))),
            _scene(*_ELEMENTS, frame=FrameSpec(size=(20, 20))),
            id="frame",
        ),
        pytest.param(
            _scene(*_ELEMENTS),
            _scene(*_ELEMENTS, scene_id="vox.other"),
            id="scene_id",
        ),
        pytest.param(
            _scene(*_ELEMENTS),
            RenderRequest(
                scene_id="vox.music",
                elements=list(_ELEMENTS),
                title="Music",
                layout="rows",
            ),
            id="layout",
        ),
    ],
)
def test_shell_change_reinstalls(before: RenderRequest, after: RenderRequest) -> None:
    live = LiveScene()
    live.plan(before)
    assert isinstance(live.plan(after), InstallScene)


def test_explicit_install_always_installs() -> None:
    live = LiveScene()
    request = _scene(_text("a", "1"))
    live.plan(request)
    assert isinstance(live.install(request), InstallScene)


def test_explicit_install_arms() -> None:
    live = LiveScene()
    live.install(_scene(_text("a", "1")))
    assert isinstance(live.plan(_scene(_text("a", "2"))), PatchScene)


def test_disarm_returns_to_install() -> None:
    live = LiveScene()
    live.plan(_scene(_text("a", "1")))
    live.disarm()
    assert not live.armed
    assert isinstance(live.plan(_scene(_text("a", "1"))), InstallScene)
