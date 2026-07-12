"""Vox's public Python API: the voxd clients and the types to call them."""

from __future__ import annotations

from punt_vox.client import SynthesizeResult, VoxClient
from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError, VoxError
from punt_vox.client_sync import VoxClientSync
from punt_vox.music_prompts import PromptSet
from punt_vox.types_synthesis import SynthesisSpec

__all__ = [
    "PromptSet",
    "SynthesisSpec",
    "SynthesizeResult",
    "VoxClient",
    "VoxClientSync",
    "VoxError",
    "VoxdConnectionError",
    "VoxdProtocolError",
    "__version__",
]

__version__ = "4.11.0"
