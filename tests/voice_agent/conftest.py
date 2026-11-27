from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fake_wire import serve

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .fake_wire import Requests


@pytest.fixture
def wire() -> Iterator[Requests]:
    with serve() as requests:
        yield requests
