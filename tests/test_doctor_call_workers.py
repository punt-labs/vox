"""Host tool diagnostics for call workers."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from punt_vox.doctor_call_workers import CallWorkerChecks


@pytest.mark.parametrize(
    ("platform", "systemd_booted", "version", "status_kind"),
    [
        ("linux", True, "249 (249.11-0ubuntu3.21)", "warn"),
        ("linux", True, "253 (253.5-1)", "warn"),
        ("linux", True, "254 (254.1-1)", "pass"),
        ("linux", True, "255.4-1ubuntu8.13", "pass"),
        ("linux", True, None, None),
        ("linux", False, "249", None),
        ("darwin", True, "249", None),
    ],
)
def test_call_checks_report_systemd_worker_requirement(
    platform: str,
    systemd_booted: bool,
    version: str | None,
    status_kind: str | None,
) -> None:
    def which(name: str) -> str | None:
        return (
            "/bin/systemd-run"
            if version is not None and name == "systemd-run"
            else None
        )

    with (
        patch("sys.platform", platform),
        patch("punt_vox.doctor_call_workers.Path") as path,
        patch("shutil.which", side_effect=which),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["/bin/systemd-run", "--version"], 0, stdout=f"systemd {version}\n"
            ),
        ) as probe,
    ):
        path.return_value.is_dir.return_value = systemd_booted
        results = CallWorkerChecks().run()

    rows = [
        result for result in results if result.message.startswith("systemd version:")
    ]
    if status_kind is None:
        probe.assert_not_called()
        assert not rows
        return

    (result,) = rows
    probe.assert_called_once()
    assert probe.call_args.args[0] == ["/bin/systemd-run", "--version"]
    assert result.status_kind == status_kind
    assert not result.required
    if status_kind == "warn":
        assert version is not None
        assert version.split()[0] in result.message
        assert "call workers" in result.message
        assert "254+" in result.message
        assert "Ubuntu 24.04" in result.message


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (None, "unexpected version output"),
        (subprocess.TimeoutExpired("systemd-run", 5), "the command timed out"),
        (subprocess.CalledProcessError(1, "systemd-run"), "the command exited 1"),
        (FileNotFoundError("systemd-run"), "the command could not run"),
    ],
)
def test_systemd_probe_errors_are_advisory(
    error: OSError | subprocess.SubprocessError | None,
    reason: str,
) -> None:
    def which(name: str) -> str | None:
        return "/bin/systemd-run" if name == "systemd-run" else None

    with (
        patch("sys.platform", "linux"),
        patch("punt_vox.doctor_call_workers.Path") as path,
        patch("shutil.which", side_effect=which),
        patch(
            "subprocess.run",
            side_effect=error,
            return_value=subprocess.CompletedProcess([], 0, stdout="unknown"),
        ),
    ):
        path.return_value.is_dir.return_value = True
        results = CallWorkerChecks().run()

    (result,) = [row for row in results if row.message.startswith("systemd version:")]
    assert result.status_kind == "warn"
    assert not result.required
    assert reason in result.message
