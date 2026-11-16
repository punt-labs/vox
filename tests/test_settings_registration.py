"""Tests for the additive ``.claude/settings.json`` registration (§ 2.8)."""

from __future__ import annotations

import json
import pwd
from pathlib import Path

import pytest

from punt_vox.permission_rules import VOX_RULES
from punt_vox.settings_registration import SettingsRegistration
from punt_vox.sibling_lock import SiblingLock

_ENTRY = "Bash(vox:*)"


def _reg(tmp_path: Path) -> SettingsRegistration:
    return SettingsRegistration(
        tmp_path / ".claude" / "settings.json", {"allow": (_ENTRY,)}
    )


def _default_reg(tmp_path: Path) -> SettingsRegistration:
    return SettingsRegistration(tmp_path / ".claude" / "settings.json")


def _allow(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(entry) for entry in data["permissions"]["allow"]]


# ---------------------------------------------------------------------------
# register -- additive, order-preserving, idempotent
# ---------------------------------------------------------------------------


def test_register_creates_file_with_the_entry(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    assert reg.register() is True
    settings = tmp_path / ".claude" / "settings.json"
    assert _allow(settings) == [_ENTRY]


def test_second_register_is_a_no_op(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    assert reg.register() is True
    assert reg.register() is False
    assert _allow(tmp_path / ".claude" / "settings.json") == [_ENTRY]


def test_register_preserves_existing_entries_and_order(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"permissions": {"allow": ["Bash(git:*)", "Read"]}}),
        encoding="utf-8",
    )
    assert _reg(tmp_path).register() is True
    # Appended in order, existing entries untouched.
    assert _allow(settings) == ["Bash(git:*)", "Read", _ENTRY]


def test_register_preserves_unrelated_settings_keys(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "hooks": {}}), encoding="utf-8")
    assert _reg(tmp_path).register() is True
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["hooks"] == {}
    assert data["permissions"]["allow"] == [_ENTRY]


# ---------------------------------------------------------------------------
# deregister -- exact value-match removal
# ---------------------------------------------------------------------------


def test_deregister_removes_only_the_owned_entry(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"permissions": {"allow": ["Bash(git:*)", _ENTRY, "Read"]}}),
        encoding="utf-8",
    )
    assert _reg(tmp_path).deregister() is True
    assert _allow(settings) == ["Bash(git:*)", "Read"]


def test_deregister_absent_entry_is_a_no_op(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8"
    )
    assert _reg(tmp_path).deregister() is False
    assert _allow(settings) == ["Read"]


def test_deregister_missing_file_is_a_no_op(tmp_path: Path) -> None:
    assert _reg(tmp_path).deregister() is False


def test_register_then_deregister_round_trip(tmp_path: Path) -> None:
    reg = _reg(tmp_path)
    reg.register()
    assert reg.deregister() is True
    assert _allow(tmp_path / ".claude" / "settings.json") == []


# ---------------------------------------------------------------------------
# boundary -- malformed settings fails fast, never silently discarded
# ---------------------------------------------------------------------------


def test_malformed_json_raises_rather_than_clobbering(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        _reg(tmp_path).register()
    # The user's file is untouched -- not reset to {}.
    assert settings.read_text(encoding="utf-8") == "{ not json"


def test_non_object_root_raises(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        _reg(tmp_path).register()


def _write_settings(tmp_path: Path, data: object) -> Path:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(data), encoding="utf-8")
    return settings


def test_register_raises_when_permissions_is_not_an_object(tmp_path: Path) -> None:
    # A present-but-malformed nested value is a boundary error, symmetric with
    # the root check: never silently discarded, never overwritten.
    settings = _write_settings(tmp_path, {"permissions": "nope"})
    with pytest.raises(ValueError, match="permissions must be a JSON object"):
        _reg(tmp_path).register()
    assert json.loads(settings.read_text(encoding="utf-8")) == {"permissions": "nope"}


def test_register_raises_when_allow_is_not_a_list(tmp_path: Path) -> None:
    settings = _write_settings(tmp_path, {"permissions": {"allow": "nope"}})
    with pytest.raises(ValueError, match=r"permissions\.allow must be a JSON array"):
        _reg(tmp_path).register()
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "permissions": {"allow": "nope"}
    }


def test_deregister_raises_when_permissions_is_not_an_object(tmp_path: Path) -> None:
    _write_settings(tmp_path, {"permissions": 5})
    with pytest.raises(ValueError, match="permissions must be a JSON object"):
        _reg(tmp_path).deregister()


def test_register_raises_when_permissions_is_null(tmp_path: Path) -> None:
    # A present-but-null permissions is malformed, not absent: a JSON `null` is a
    # value the user wrote, so treating it as "key absent" and overwriting it
    # would silently discard it. Symmetric with the non-dict case.
    settings = _write_settings(tmp_path, {"permissions": None})
    with pytest.raises(ValueError, match="permissions must be a JSON object"):
        _reg(tmp_path).register()
    assert json.loads(settings.read_text(encoding="utf-8")) == {"permissions": None}


def test_register_raises_when_allow_is_null(tmp_path: Path) -> None:
    # A present-but-null allow is malformed, not absent -- never silently created
    # over the user's explicit null.
    settings = _write_settings(tmp_path, {"permissions": {"allow": None}})
    with pytest.raises(ValueError, match=r"permissions\.allow must be a JSON array"):
        _reg(tmp_path).register()
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "permissions": {"allow": None}
    }


def test_deregister_raises_when_permissions_is_null(tmp_path: Path) -> None:
    _write_settings(tmp_path, {"permissions": None})
    with pytest.raises(ValueError, match="permissions must be a JSON object"):
        _reg(tmp_path).deregister()


def test_register_creates_absent_permissions_key(tmp_path: Path) -> None:
    # An ABSENT permissions key is safe to add (create-when-absent), leaving
    # unrelated keys untouched.
    settings = _write_settings(tmp_path, {"model": "opus"})
    assert _reg(tmp_path).register() is True
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["permissions"]["allow"] == [_ENTRY]


def test_register_creates_absent_allow_key(tmp_path: Path) -> None:
    # permissions present as an object but with no allow list: the list is
    # created, the object is not overwritten.
    settings = _write_settings(tmp_path, {"permissions": {"deny": ["X"]}})
    assert _reg(tmp_path).register() is True
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["deny"] == ["X"]
    assert data["permissions"]["allow"] == [_ENTRY]


# ---------------------------------------------------------------------------
# default rules and refusals
# ---------------------------------------------------------------------------


def test_default_rules_allow_each_surface_broadly(tmp_path: Path) -> None:
    assert _default_reg(tmp_path).register() is True
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert data["permissions"]["allow"] == list(VOX_RULES["allow"])
    assert data["permissions"]["ask"] == list(VOX_RULES["ask"])
    assert VOX_RULES["allow"] == (
        "Bash(vox:*)",
        "mcp__plugin_vox_mic__*",
        "Skill(vox:*)",
    )


def test_unsafe_verbs_ask_but_read_only_flags_do_not(tmp_path: Path) -> None:
    ask = VOX_RULES["ask"]
    assert "Bash(vox -*)" not in ask
    assert not [rule for rule in ask if "*" in rule.removesuffix(":*)")]
    for verb in (
        "install",
        "uninstall",
        "enable",
        "disable",
        "daemon",
        "desktop",
        "register-guidance",
    ):
        assert f"Bash(vox {verb}:*)" in ask
    assert "Bash(vox music:*)" not in ask
    assert "Bash(vox rec:*)" not in ask
    assert _default_reg(tmp_path).rules == VOX_RULES


def test_no_dev_rule_is_granted() -> None:
    assert not [
        rule for rules in VOX_RULES.values() for rule in rules if "-dev" in rule
    ]


def test_deregister_never_creates_an_absent_tier(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    user = {"permissions": {"allow": ["Bash(vox:*)", "Read"]}}
    settings.write_text(json.dumps(user), encoding="utf-8")
    assert _default_reg(tmp_path).deregister() is True
    assert json.loads(settings.read_text()) == {"permissions": {"allow": ["Read"]}}


def test_register_raises_when_ask_is_not_a_list(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"ask": "x"}}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"permissions\.ask must be a JSON array"):
        _default_reg(tmp_path).register()


def test_deregister_removes_exactly_vox_rules(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    user = {"model": "x", "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Y"]}}
    settings.write_text(json.dumps(user), encoding="utf-8")
    reg = _default_reg(tmp_path)
    reg.register()
    reg.deregister()
    # enable created the ask tier; disable empties it rather than deleting it.
    expected = {
        "model": "x",
        "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Y"], "ask": []},
    }
    assert json.loads(settings.read_text(encoding="utf-8")) == expected


def test_refuses_invalid_utf8(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_bytes(b"\xff\xfe{}")
    with pytest.raises(ValueError, match="UTF-8"):
        _reg(tmp_path).register()
    assert settings.read_bytes() == b"\xff\xfe{}"


def test_refuses_a_symlinked_settings_file(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").symlink_to(victim)
    with pytest.raises(ValueError, match="symlinked"):
        _reg(tmp_path).register()
    assert victim.read_text(encoding="utf-8") == "{}"


def test_refuses_a_symlinked_claude_dir(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".claude").symlink_to(elsewhere)
    with pytest.raises(ValueError, match="symlinked"):
        _reg(repo).deregister()
    assert list(elsewhere.iterdir()) == []


def test_refuses_a_repo_rooted_at_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="home directory"):
        _reg(tmp_path).register()
    assert not (tmp_path / ".claude").exists()


# ---------------------------------------------------------------------------
# swap race -- .claude/ replaced by a symlink while the lock is awaited
# ---------------------------------------------------------------------------


def _swap_claude_while_locking(
    repo: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename ``.claude`` aside and symlink it at *victim* during lock acquire."""
    real_acquire = SiblingLock._acquire

    def swap_then_acquire(lock: SiblingLock, fd: int) -> None:
        (repo / ".claude").rename(repo / "claude-moved")
        (repo / ".claude").symlink_to(victim)
        real_acquire(lock, fd)

    monkeypatch.setattr(SiblingLock, "_acquire", swap_then_acquire)


def test_register_writes_the_checked_dir_not_a_swapped_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    victim = tmp_path / "victim-claude"
    victim.mkdir()
    victim_settings = victim / "settings.json"
    victim_settings.write_text('{"env": {"K": "v"}}', encoding="utf-8")
    _swap_claude_while_locking(repo, victim, monkeypatch)

    assert _reg(repo).register() is True

    assert victim_settings.read_text(encoding="utf-8") == '{"env": {"K": "v"}}'
    assert sorted(p.name for p in victim.iterdir()) == ["settings.json"]
    assert _allow(repo / "claude-moved" / "settings.json") == [_ENTRY]


def test_deregister_never_strips_rules_through_a_swapped_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": [_ENTRY]}}), encoding="utf-8"
    )
    victim = tmp_path / "victim-claude"
    victim.mkdir()
    victim_settings = victim / "settings.json"
    original = json.dumps({"permissions": {"allow": [_ENTRY, "Read"]}})
    victim_settings.write_text(original, encoding="utf-8")
    _swap_claude_while_locking(repo, victim, monkeypatch)

    assert _reg(repo).deregister() is True

    assert victim_settings.read_text(encoding="utf-8") == original
    assert _allow(repo / "claude-moved" / "settings.json") == []


def test_refuses_a_settings_file_swapped_for_a_symlink_while_locking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    real_acquire = SiblingLock._acquire

    def swap_then_acquire(lock: SiblingLock, fd: int) -> None:
        settings.unlink()
        settings.symlink_to(victim)
        real_acquire(lock, fd)

    monkeypatch.setattr(SiblingLock, "_acquire", swap_then_acquire)

    with pytest.raises(ValueError, match="symlinked"):
        _reg(tmp_path).register()
    assert victim.read_text(encoding="utf-8") == "{}"


def test_refuses_the_real_home_even_when_home_is_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    account = pwd.struct_passwd(("u", "x", 0, 0, "", str(tmp_path), "/bin/sh"))

    def fake_getpwuid(_uid: int) -> pwd.struct_passwd:
        return account

    monkeypatch.setattr("punt_vox.settings_registration.pwd.getpwuid", fake_getpwuid)
    with pytest.raises(ValueError, match="home directory"):
        _reg(tmp_path).register()
    assert not (tmp_path / ".claude").exists()


# ---------------------------------------------------------------------------
# legacy rules -- an older vox granted ``Bash(vox -*)``; both verbs retire it
# ---------------------------------------------------------------------------


def _seed_legacy(tmp_path: Path) -> Path:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    ask = ["Bash(ls:*)", "Bash(vox -*)", *VOX_RULES["ask"]]
    path.write_text(json.dumps({"permissions": {"ask": ask}}), encoding="utf-8")
    return path


def _ask(path: Path) -> list[str]:
    return list(json.loads(path.read_text(encoding="utf-8"))["permissions"]["ask"])


def test_register_strips_the_legacy_flag_rule(tmp_path: Path) -> None:
    path = _seed_legacy(tmp_path)
    assert _default_reg(tmp_path).register() is True
    assert _ask(path) == ["Bash(ls:*)", *VOX_RULES["ask"]]
    assert _default_reg(tmp_path).register() is False


def test_deregister_strips_the_legacy_flag_rule(tmp_path: Path) -> None:
    path = _seed_legacy(tmp_path)
    assert _default_reg(tmp_path).deregister() is True
    assert _ask(path) == ["Bash(ls:*)"]
