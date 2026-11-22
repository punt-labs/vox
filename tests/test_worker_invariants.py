"""Named properties from docs/vox-workers.tex."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, is_dataclass, replace
from pathlib import Path
from typing import cast

import pytest
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.workers.codec import WorkerRecordCodec
from punt_vox.voxd.workers.contracts import WorkerProvider


def test_worker_record_is_a_frozen_domain_value(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    assert is_dataclass(record)
    with pytest.raises(FrozenInstanceError):
        object.__getattribute__(record, "__setattr__")("state", "stopped")
    with pytest.raises(TypeError):
        object.__getattribute__(record, "last_report")["session_started"] = True


def test_worker_launch_obeys_parameter_limit() -> None:
    parameters = inspect.signature(WorkerProvider.start).parameters
    assert len(parameters) - 1 <= 4


def test_worker_record_detaches_report_snapshots_on_both_edges(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    files = ["a.py"]
    record = replace(record, last_report={"active_files": files})
    files.append("outside.py")
    wire = WorkerRecordCodec.encode(record)
    cast("list[str]", wire["last_report"]["active_files"]).append("wire.py")
    assert record.last_report == {"active_files": ["a.py"]}
