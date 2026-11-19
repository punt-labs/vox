"""Role labels stay local to the handler receiving the filtered record."""

from __future__ import annotations

import logging

import pytest

from punt_vox.logging_role import ClientRoleFilter, Role


@pytest.mark.parametrize("role", ["cli", "mcp", "hook", "worker"])
@pytest.mark.parametrize("name", ["punt_vox.test", ""])
def test_role_filter_copies_record(role: Role, name: str) -> None:
    original = logging.LogRecord(name, logging.INFO, "", 0, "value %s", (42,), None)
    filtered = ClientRoleFilter(role).filter(original)
    other = ClientRoleFilter("other").filter(original)
    assert filtered is not original
    assert filtered.name == f"client.{role}.{name}"
    assert other.name == f"client.other.{name}"
    assert original.name == name
    assert filtered.msg == original.msg
    assert filtered.args == original.args
    assert filtered.getMessage() == "value 42"
