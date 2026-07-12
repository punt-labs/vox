"""The top-level ``punt_vox`` package exposes the supported public API."""

from __future__ import annotations

import pytest

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


def test_error_types_are_public() -> None:
    """The exceptions a caller catches are importable from the package root."""
    from punt_vox import VoxdConnectionError, VoxdProtocolError, VoxError

    assert VoxError is punt_vox.VoxError
    assert VoxdConnectionError is punt_vox.VoxdConnectionError
    assert VoxdProtocolError is punt_vox.VoxdProtocolError


def test_error_hierarchy_allows_one_except_clause() -> None:
    """Both concrete errors subclass ``VoxError`` so one ``except`` catches either."""
    from punt_vox import VoxdConnectionError, VoxdProtocolError, VoxError

    assert issubclass(VoxdConnectionError, VoxError)
    assert issubclass(VoxdProtocolError, VoxError)
    for error in (VoxdConnectionError("x"), VoxdProtocolError("y")):
        with pytest.raises(VoxError):
            raise error


def test_prompt_set_is_public() -> None:
    """``PromptSet`` -- a program_on/select argument -- is importable publicly."""
    from punt_vox import PromptSet

    assert PromptSet is punt_vox.PromptSet


def test_result_types_are_public() -> None:
    """The typed return values of the client methods are importable publicly."""
    from punt_vox import CommandOutcome, HealthStatus, ProgramStatus, ProgramSummary

    assert HealthStatus is punt_vox.HealthStatus
    assert ProgramStatus is punt_vox.ProgramStatus
    assert CommandOutcome is punt_vox.CommandOutcome
    assert ProgramSummary is punt_vox.ProgramSummary


def test_public_api_is_reexported_from_owning_modules() -> None:
    """Each name is the same object the owning submodule defines."""
    from punt_vox.client import SynthesizeResult, VoxClient
    from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError, VoxError
    from punt_vox.client_sync import VoxClientSync
    from punt_vox.music_prompts import PromptSet
    from punt_vox.types_synthesis import SynthesisSpec

    assert punt_vox.VoxClient is VoxClient
    assert punt_vox.VoxClientSync is VoxClientSync
    assert punt_vox.SynthesisSpec is SynthesisSpec
    assert punt_vox.SynthesizeResult is SynthesizeResult
    assert punt_vox.VoxError is VoxError
    assert punt_vox.VoxdConnectionError is VoxdConnectionError
    assert punt_vox.VoxdProtocolError is VoxdProtocolError
    assert punt_vox.PromptSet is PromptSet


def test_all_lists_the_public_api() -> None:
    """``__all__`` names the clients, their types, the errors, and the version."""
    assert set(punt_vox.__all__) == {
        "CommandOutcome",
        "HealthStatus",
        "ProgramStatus",
        "ProgramSummary",
        "PromptSet",
        "SynthesisSpec",
        "SynthesizeResult",
        "VoxClient",
        "VoxClientSync",
        "VoxError",
        "VoxdConnectionError",
        "VoxdProtocolError",
        "__version__",
    }
