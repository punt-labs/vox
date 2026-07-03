"""Shared domain types for the service package."""

from __future__ import annotations

from typing import Literal

type PlatformName = Literal["macos", "linux"]
"""The two operating systems voxd installs a system service on.

Single-sources the platform domain so the health-host gate, the installer's
platform detection, and the public wrappers cannot drift from one another.
"""
