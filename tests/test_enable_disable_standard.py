"""Acceptance tests for the amended Punt Labs tool-enable-disable standard.

Covers:
- Default local scope vs explicit ``--scope project`` (CLI & MCP)
- PersonalTargetGuard refusing untracked/unignored local targets
- Effective state precedence and deciding-file reporting (status & doctor)
- Committed opt-out (``--opt-out``, refusal in both scopes, ``--clear-opt-out``)
- Local disable overriding project enablement (``disabled`` marker + shared guide note)
- Refresh-only guide deposit (ordinary enable preserves guide; ``--refresh`` updates)
- Byte-preserving no-op writes and sorted insertion (reversed enable order parity)
- Shell hook effective-state gate (``vox_hook_is_enabled``)
- Dormant MCP tools when repo is off (while ``enablement`` and ``status`` stay live)
- Personal preference isolation (``git status --porcelain`` stays empty after toggles)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.claude_md import ClaudeMdImport
from punt_vox.config import ConfigStore
from punt_vox.deposited_files import DepositedGuide
from punt_vox.doctor_guide import GuideCheck
from punt_vox.enablement import RepoEnablement
from punt_vox.enablement_state import EnablementScope, EnablementState
from punt_vox.gitignore_entry import VoxGitignore
from punt_vox.guide_stamp import GuideStamp
from punt_vox.server import mcp
from punt_vox.server_enablement import EnablementTool
from punt_vox.settings_registration import SettingsRegistration

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "plugin" / "hooks"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _init_repo_with_baseline(tmp_path: Path) -> Path:
    """Create a real git repo with the TED-20 gitignore baseline committed."""
    _git(tmp_path, "init", "--quiet", "--template=")
    _git(tmp_path, "config", "user.email", "test@punt-labs.com")
    _git(tmp_path, "config", "user.name", "Test")
    VoxGitignore(tmp_path).ensure()
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-m", "Add gitignore baseline", "--quiet")
    return tmp_path


def _porcelain(repo: Path) -> str:
    return _git(
        repo,
        "-c",
        "core.excludesFile=/dev/null",
        "status",
        "--porcelain=v1",
        "-uall",
    ).strip()


def _point_repo(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    def _fake_root(_start: Path | None = None) -> Path:
        return repo

    monkeypatch.setattr("punt_vox.enablement.find_repo_root", _fake_root)
    monkeypatch.setattr("punt_vox.enablement_state.find_repo_root", _fake_root)
    monkeypatch.setattr("punt_vox.doctor_guide.find_repo_root", _fake_root)


# ---------------------------------------------------------------------------
# Default local scope & zero tracked churn
# ---------------------------------------------------------------------------


@pytest.mark.subprocess
def test_local_enable_and_toggles_leave_git_status_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo_with_baseline(tmp_path)
    _point_repo(monkeypatch, repo)

    runner = CliRunner()
    res = runner.invoke(app, ["enable"])
    assert res.exit_code == 0, res.output
    assert (repo / ".punt-labs" / "local" / "vox" / "enabled").is_file()
    assert (repo / ".punt-labs" / "local" / "vox" / "CLAUDE.md").is_file()
    assert "@.punt-labs/local/vox/CLAUDE.md" in (repo / "CLAUDE.local.md").read_text()
    assert (repo / ".claude" / "settings.local.json").is_file()
    assert not (repo / ".punt-labs" / "vox" / "enabled").exists()
    assert not (repo / "CLAUDE.md").exists()
    assert not (repo / ".claude" / "settings.json").exists()
    assert _porcelain(repo) == ""

    # Personal preference writes land in gitignored vox.local.md; git stays clean.
    store = ConfigStore(repo / ".punt-labs" / "vox")
    store.write_fields(
        {"notify": "c", "speak": "y", "voice": "matilda", "provider": "elevenlabs"}
    )
    assert not (repo / ".punt-labs" / "vox" / "vox.md").exists()
    assert (repo / ".punt-labs" / "vox" / "vox.local.md").is_file()
    assert _porcelain(repo) == ""

    # Disable local scope; git stays clean.
    res_off = runner.invoke(app, ["disable"])
    assert res_off.exit_code == 0, res_off.output
    assert not (repo / ".punt-labs" / "local" / "vox" / "enabled").exists()
    assert _porcelain(repo) == ""


@pytest.mark.subprocess
def test_local_enable_auto_excludes_when_gitignore_baseline_missing(
    tmp_path: Path,
) -> None:
    """Bare local enable works in any git repo via .git/info/exclude with clean git."""
    _git(tmp_path, "init", "--quiet", "--template=")
    state = RepoEnablement.for_repo(tmp_path, scope="local").enable()
    assert state.on is True
    assert state.status == "enabled_local"
    assert (tmp_path / ".punt-labs" / "local" / "vox" / "enabled").exists()
    assert not (tmp_path / ".gitignore").exists()
    assert _porcelain(tmp_path) == ""
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".punt-labs/local/" in exclude
    assert "CLAUDE.local.md" in exclude


@pytest.mark.subprocess
def test_local_enable_refuses_when_target_is_tracked(tmp_path: Path) -> None:
    repo = _init_repo_with_baseline(tmp_path)
    local_claude = repo / "CLAUDE.local.md"
    local_claude.write_text("# accidentally tracked\n", encoding="utf-8")
    _git(repo, "add", "-f", "CLAUDE.local.md")
    _git(repo, "commit", "-m", "track local file", "--quiet")

    with pytest.raises(ValueError, match="tracked in git"):
        RepoEnablement.for_repo(repo, scope="local").enable()


# ---------------------------------------------------------------------------
# Effective state precedence, local override, and committed opt-out
# ---------------------------------------------------------------------------


@pytest.mark.subprocess
def test_effective_state_precedence_and_deciding_files(tmp_path: Path) -> None:
    repo = _init_repo_with_baseline(tmp_path)

    # 6. Absent
    st = EnablementState.resolve(repo)
    assert not st.on
    assert st.status == "absent"
    assert st.deciding_file is None

    # 5. Dormant
    (repo / ".punt-labs" / "vox").mkdir(parents=True)
    st = EnablementState.resolve(repo)
    assert not st.on
    assert st.status == "dormant"

    # 4. Local enabled
    RepoEnablement.for_repo(repo, scope="local").enable()
    st = EnablementState.resolve(repo)
    assert st.on
    assert st.status == "enabled_local"
    assert st.deciding_file == repo / ".punt-labs" / "local" / "vox" / "enabled"

    # 3. Project enabled takes precedence as deciding file
    RepoEnablement.for_repo(repo, scope="project").enable()
    st = EnablementState.resolve(repo)
    assert st.on
    assert st.status == "enabled_project"
    assert st.deciding_file == repo / ".punt-labs" / "vox" / "enabled"

    # 2. Local disable overrides project enablement; shared guide still loads
    st = RepoEnablement.for_repo(repo, scope="local").disable()
    assert not st.on
    assert st.status == "disabled_local"
    assert st.deciding_file == repo / ".punt-labs" / "local" / "vox" / "disabled"
    assert st.shared_guide_loads is True
    assert (repo / ".punt-labs" / "vox" / "enabled").is_file()

    # Re-enabling locally clears the local disabled marker
    st = RepoEnablement.for_repo(repo, scope="local").enable()
    assert st.on
    assert not (repo / ".punt-labs" / "local" / "vox" / "disabled").exists()

    # 1. Committed opt-out overrides everything
    st = RepoEnablement.for_repo(repo, scope="project").disable(opt_out=True)
    assert not st.on
    assert st.status == "opted_out"
    assert st.deciding_file == repo / ".punt-labs" / "vox" / "opt-out"

    # Both local and project enable refuse under opt-out
    with pytest.raises(ValueError, match="opt-out"):
        RepoEnablement.for_repo(repo, scope="local").enable()
    with pytest.raises(ValueError, match="opt-out"):
        RepoEnablement.for_repo(repo, scope="project").enable()

    # --clear-opt-out clears opt-out and enables project scope
    st = RepoEnablement.for_repo(repo, scope="project").enable(clear_opt_out=True)
    assert st.on
    assert st.status == "enabled_project"
    assert not (repo / ".punt-labs" / "vox" / "opt-out").exists()


# ---------------------------------------------------------------------------
# Refresh-only guide deposit & byte-preserving no-op writes
# ---------------------------------------------------------------------------


def test_guide_deposit_is_refresh_only(tmp_path: Path) -> None:
    guide_path = tmp_path / ".punt-labs" / "vox" / "CLAUDE.md"
    guide = DepositedGuide(guide_path, tmp_path)

    # First deposit writes the packaged guide.
    assert guide.deposit() is True
    assert guide_path.is_file()

    # Pin custom content; ordinary enable/deposit must NOT overwrite it.
    pinned = "# pinned team version\n"
    guide_path.write_text(pinned, encoding="utf-8")
    assert guide.deposit() is False
    assert guide_path.read_text(encoding="utf-8") == pinned

    # Explicit refresh replaces it with the packaged asset.
    assert guide.deposit(refresh=True) is True
    assert guide_path.read_text(encoding="utf-8") != pinned
    # Second refresh with identical content is a no-op.
    assert guide.deposit(refresh=True) is False


def test_doctor_reports_effective_state_and_refresh_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo_with_baseline(tmp_path)
    _point_repo(monkeypatch, repo)
    RepoEnablement.for_repo(repo, scope="local").enable()

    guide_path = repo / ".punt-labs" / "local" / "vox" / "CLAUDE.md"
    guide_path.write_text("# drifted\n", encoding="utf-8")
    packaged = tmp_path / "packaged.md"
    packaged.write_text("# current\n", encoding="utf-8")
    monkeypatch.setattr(GuideStamp, "for_packaged_asset", lambda: GuideStamp(packaged))

    rows = GuideCheck().run()
    assert len(rows) == 2
    assert "enabled_local" in rows[0].message
    assert ".punt-labs/local/vox/enabled" in rows[0].message
    assert "vox enable --refresh" in rows[1].message


# ---------------------------------------------------------------------------
# Sorted insertion & reversed enable order parity (TED-25)
# ---------------------------------------------------------------------------


def test_claude_md_sorted_insertion_reversed_order_parity(tmp_path: Path) -> None:
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    header = "# Project\n\nCustom prose.\n"
    (repo_a / "CLAUDE.md").write_text(header, encoding="utf-8")
    (repo_b / "CLAUDE.md").write_text(header, encoding="utf-8")

    ethos = "@.punt-labs/ethos/CLAUDE.md"
    vox = "@.punt-labs/vox/CLAUDE.md"

    # Order A: ethos then vox
    ClaudeMdImport(repo_a / "CLAUDE.md", ethos).register()
    ClaudeMdImport(repo_a / "CLAUDE.md", vox).register()

    # Order B: vox then ethos
    ClaudeMdImport(repo_b / "CLAUDE.md", vox).register()
    ClaudeMdImport(repo_b / "CLAUDE.md", ethos).register()

    assert (repo_a / "CLAUDE.md").read_bytes() == (repo_b / "CLAUDE.md").read_bytes()
    lines = (repo_a / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert lines.index(ethos) < lines.index(vox)
    assert lines[0] == "# Project"


def test_settings_sorted_insertion_reversed_order_parity(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    ethos_rules = {"allow": ("Bash(ethos:*)", "mcp__plugin_ethos__*")}
    vox_rules = {"allow": ("Bash(vox:*)", "mcp__plugin_vox_mic__*")}

    SettingsRegistration(a, rules=ethos_rules).register()
    SettingsRegistration(a, rules=vox_rules).register()

    SettingsRegistration(b, rules=vox_rules).register()
    SettingsRegistration(b, rules=ethos_rules).register()

    assert a.read_bytes() == b.read_bytes()
    assert json.loads(a.read_text())["permissions"]["allow"] == [
        "Bash(ethos:*)",
        "Bash(vox:*)",
        "mcp__plugin_ethos__*",
        "mcp__plugin_vox_mic__*",
    ]


# ---------------------------------------------------------------------------
# Shell hook effective-state gate (TED-24)
# ---------------------------------------------------------------------------


def _run_hook_gate(repo: Path) -> int:
    script = f'source "{_HOOKS_DIR / "repo-root.sh"}" && vox_hook_is_enabled "{repo}"'
    res = subprocess.run(["bash", "-c", script], check=False)
    return res.returncode


@pytest.mark.subprocess
def test_shell_hook_effective_state_gate(tmp_path: Path) -> None:
    repo = _init_repo_with_baseline(tmp_path)
    assert _run_hook_gate(repo) != 0

    # Local enable -> on
    RepoEnablement.for_repo(repo, scope="local").enable()
    assert _run_hook_gate(repo) == 0

    # Project enable + local disable -> off
    RepoEnablement.for_repo(repo, scope="project").enable()
    RepoEnablement.for_repo(repo, scope="local").disable()
    assert _run_hook_gate(repo) != 0

    # Local re-enable -> on
    RepoEnablement.for_repo(repo, scope="local").enable()
    assert _run_hook_gate(repo) == 0

    # Opt-out -> off
    RepoEnablement.for_repo(repo, scope="project").disable(opt_out=True)
    assert _run_hook_gate(repo) != 0


# ---------------------------------------------------------------------------
# Dormant MCP tools when repo is off (TED-24)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tools_dormant_when_repo_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo_with_baseline(tmp_path)
    _point_repo(monkeypatch, repo)

    # Repo is absent (off): calling a normal tool like `voice` returns dormant JSON.
    raw = await mcp.call_tool("voice", {})
    assert isinstance(raw, list)
    assert len(raw) == 1
    payload = json.loads(getattr(raw[0], "text", ""))
    assert payload["enabled"] is False
    assert payload["status"] == "absent"
    assert "vox enable" in payload["error"]

    # `enablement` remains callable while off and can turn local scope on.
    def _factory(scope: EnablementScope = "local") -> RepoEnablement:
        return RepoEnablement.for_repo(repo, scope=scope)

    tool = EnablementTool(_factory)
    reply = json.loads(tool.dispatch("enable"))
    assert reply["enabled"] is True
    assert reply["scope"] == "local"
    assert reply["status"] == "enabled_local"


def test_freshly_enabled_repo_defaults_notify_yes_without_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ConfigStore(None).read() in a freshly enabled repo defaults notify='y'."""
    repo = _init_repo_with_baseline(tmp_path)
    RepoEnablement.for_repo(repo, scope="local").enable()
    _point_repo(monkeypatch, repo)
    monkeypatch.setattr("punt_vox.config.DEFAULT_CONFIG_DIR", Path(".punt-labs/vox"))
    monkeypatch.chdir(repo)

    cfg = ConfigStore(None).read()
    assert cfg.notify == "y"
    assert not (repo / ".punt-labs" / "vox" / "vox.md").exists()
    assert not (repo / ".punt-labs" / "vox" / "vox.local.md").exists()


@pytest.mark.subprocess
def test_local_disable_succeeds_when_only_local_dir_is_ignored(
    tmp_path: Path,
) -> None:
    """Local disable only verifies targets it touches (e.g. local/vox/disabled)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--template=")
    (repo / ".gitignore").write_text(".punt-labs/local/\n", encoding="utf-8")
    (repo / ".punt-labs" / "vox").mkdir(parents=True)
    (repo / ".punt-labs" / "vox" / "enabled").write_text("on\n", encoding="utf-8")

    state = RepoEnablement.for_repo(repo, scope="local").disable()
    assert state.on is False
    assert state.status == "disabled_local"
    assert (repo / ".punt-labs" / "local" / "vox" / "disabled").exists()


def test_purge_refuses_symlinked_punt_labs_ancestor(tmp_path: Path) -> None:
    """Purge refuses when .punt-labs is a symlink pointing outside the repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    (outside / "vox").mkdir(parents=True)
    (outside / "vox" / "sentinel.txt").write_text("keep\n", encoding="utf-8")
    (outside / "vox" / "enabled").write_text("on\n", encoding="utf-8")
    (repo / ".punt-labs").symlink_to(outside)

    with pytest.raises(ValueError, match="symlinked or external"):
        RepoEnablement.for_repo(repo, scope="project").purge()
    assert (outside / "vox" / "sentinel.txt").exists()
    assert (outside / "vox" / "enabled").exists()


@pytest.mark.subprocess
def test_enabled_dir_resolves_for_freshly_enabled_repo_without_config(
    tmp_path: Path,
) -> None:
    """Hooks resolve default config dir on freshly enabled repo without vox.md."""
    repo = _init_repo_with_baseline(tmp_path)
    RepoEnablement.for_repo(repo, scope="local").enable()
    sub = repo / "sub"
    sub.mkdir()
    resolved = ConfigStore.enabled_dir(sub)
    assert resolved == repo / ".punt-labs" / "vox"
    assert ConfigStore(resolved).read().notify == "y"


@pytest.mark.subprocess
def test_local_purge_on_project_enabled_repo_keeps_disabled_marker(
    tmp_path: Path,
) -> None:
    """Local purge keeps local/vox/disabled so project-enabled vox stays off."""
    repo = _init_repo_with_baseline(tmp_path)
    RepoEnablement.for_repo(repo, scope="project").enable()
    extra = repo / ".punt-labs" / "local" / "vox" / "extra.txt"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("remove me\n", encoding="utf-8")

    state = RepoEnablement.for_repo(repo, scope="local").purge()
    assert state.on is False
    assert state.status == "disabled_local"
    assert (repo / ".punt-labs" / "local" / "vox" / "disabled").exists()
    assert not extra.exists()


@pytest.mark.subprocess
def test_local_purge_refuses_git_tracked_file_under_local_dir(
    tmp_path: Path,
) -> None:
    """Local purge refuses if any file under .punt-labs/local/vox is tracked."""
    repo = _init_repo_with_baseline(tmp_path)
    tracked = repo / ".punt-labs" / "local" / "vox" / "tracked.txt"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("keep\n", encoding="utf-8")
    _git(repo, "add", "-f", str(tracked))

    with pytest.raises(ValueError, match="git-tracked"):
        RepoEnablement.for_repo(repo, scope="local").purge()
    assert tracked.exists()


@pytest.mark.subprocess
def test_failed_clear_opt_out_keeps_opt_out_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If project enable fails during --clear-opt-out, opt-out stays in place."""
    repo = _init_repo_with_baseline(tmp_path)
    RepoEnablement.for_repo(repo, scope="project").disable(opt_out=True)

    def _boom(_self: RepoEnablement, *, refresh: bool) -> None:
        del refresh
        msg = "simulated setup failure"
        raise OSError(msg)

    monkeypatch.setattr(RepoEnablement, "_enable_project", _boom)
    with pytest.raises(OSError, match="simulated setup failure"):
        RepoEnablement.for_repo(repo, scope="project").enable(clear_opt_out=True)
    assert (repo / ".punt-labs" / "vox" / "opt-out").exists()
    assert EnablementState.resolve(repo).status == "opted_out"


@pytest.mark.subprocess
def test_local_refresh_on_project_enabled_repo_raises(tmp_path: Path) -> None:
    """Local --refresh refuses when shared project guide is active."""
    repo = _init_repo_with_baseline(tmp_path)
    RepoEnablement.for_repo(repo, scope="project").enable()
    with pytest.raises(ValueError, match="--scope project --refresh"):
        RepoEnablement.for_repo(repo, scope="local").enable(refresh=True)


@pytest.mark.subprocess
def test_personal_write_skips_gitignore_when_already_ignored(tmp_path: Path) -> None:
    """Personal setting write does not dirty .gitignore if exclude covers it."""
    from punt_vox.config_seed import InheritedConfig

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--template=")
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".punt-labs/**/*.local.*\n", encoding="utf-8")
    gitignore = repo / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    InheritedConfig(None).seed(repo, repo / ".punt-labs" / "vox")
    assert gitignore.read_text(encoding="utf-8") == "build/\n"


@pytest.mark.subprocess
def test_local_disable_leaves_no_lockfiles_for_absent_targets(tmp_path: Path) -> None:
    """Local disable skips prune/deregister when CLAUDE.local.md/settings absent."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--template=")
    (repo / ".gitignore").write_text(".punt-labs/local/\n", encoding="utf-8")
    (repo / ".punt-labs" / "vox").mkdir(parents=True)
    (repo / ".punt-labs" / "vox" / "enabled").write_text("on\n", encoding="utf-8")

    RepoEnablement.for_repo(repo, scope="local").disable()
    assert not (repo / ".CLAUDE.local.md.punt-import.lock").exists()
    assert not (repo / ".claude").exists()


@pytest.mark.subprocess
def test_doctor_reports_shared_guide_still_loads_when_locally_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor reports shared_guide_loads when locally disabled on project repo."""
    repo = _init_repo_with_baseline(tmp_path)
    RepoEnablement.for_repo(repo, scope="project").enable()
    RepoEnablement.for_repo(repo, scope="local").disable()

    monkeypatch.chdir(repo)
    results = GuideCheck().run()
    messages = [r.message for r in results]
    assert any("shared guide still loads" in m for m in messages)


@pytest.mark.subprocess
def test_nested_enabled_repo_inheriting_disabled_outer_config_defaults_notify_y(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested enabled repo inheriting outer disabled config defaults notify to y."""
    outer_dir = tmp_path / "outer"
    outer_dir.mkdir()
    outer = _init_repo_with_baseline(outer_dir)
    outer_cfg = outer / ".punt-labs" / "vox"
    outer_cfg.mkdir(parents=True, exist_ok=True)
    (outer_cfg / "vox.md").write_text("---\n---\n", encoding="utf-8")

    nested_dir = outer / "nested"
    nested_dir.mkdir()
    nested = _init_repo_with_baseline(nested_dir)
    RepoEnablement.for_repo(nested, scope="local").enable()
    monkeypatch.chdir(nested)
    assert ConfigStore(outer_cfg).read().notify == "y"
