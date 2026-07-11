"""The top-level ``punt_vox`` package exposes the supported public API."""

from __future__ import annotations

import punt_vox


def test_top_level_imports_resolve() -> None:
    """A third party can import the clients and their types from the package root."""
    from punt_vox import (
        SynthesisSpec,
        SynthesizeResult,
        VoxClient,
        VoxClientSync,
    )

    assert VoxClient is punt_vox.VoxClient
    assert VoxClientSync is punt_vox.VoxClientSync
    assert SynthesisSpec is punt_vox.SynthesisSpec
    assert SynthesizeResult is punt_vox.SynthesizeResult


def test_public_api_is_reexported_from_owning_modules() -> None:
    """Each name is the same object the owning submodule defines."""
    from punt_vox.client import SynthesizeResult, VoxClient
    from punt_vox.client_sync import VoxClientSync
    from punt_vox.types_synthesis import SynthesisSpec

    assert punt_vox.VoxClient is VoxClient
    assert punt_vox.VoxClientSync is VoxClientSync
    assert punt_vox.SynthesisSpec is SynthesisSpec
    assert punt_vox.SynthesizeResult is SynthesizeResult


def test_all_lists_the_public_api() -> None:
    """``__all__`` names the clients, their types, and the version."""
    assert set(punt_vox.__all__) == {
        "SynthesisSpec",
        "SynthesizeResult",
        "VoxClient",
        "VoxClientSync",
        "__version__",
    }
