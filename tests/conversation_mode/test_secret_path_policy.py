"""Tests for :class:`SecretPathPolicy`: the call agent's secret-path deny-list."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentSpawnError
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / ".punt-labs" / "ethos").mkdir(parents=True)
    (root / ".gitignore").write_text(
        "\n".join(
            [
                "# comment mentioning a secret",
                "__pycache__/",
                ".envrc.local",
                ".punt-labs/**/*.local.*",
                "!keep.env.example",
                "dist/",
                "spikes/agent-token.json",
            ]
        )
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.fixture
def policy(repo: Path) -> SecretPathPolicy:
    return SecretPathPolicy.for_repo(repo)


class TestBuiltinDenyList:
    @pytest.mark.parametrize(
        "candidate",
        [
            ".env",
            ".env.local",
            "config/.env.production",
            "deploy/prod.env",
            "./.env",
            "src/../.env",
            "certs/server.pem",
            "auth.json",
        ],
    )
    def test_well_known_secret_paths_are_denied(
        self, policy: SecretPathPolicy, repo: Path, candidate: str
    ) -> None:
        assert policy.denies(candidate, cwd=repo)

    @pytest.mark.parametrize(
        "candidate", ["README.md", "src/punt_vox/paths.py", "docs/environment.md", "."]
    )
    def test_ordinary_paths_are_allowed(
        self, policy: SecretPathPolicy, repo: Path, candidate: str
    ) -> None:
        assert not policy.denies(candidate, cwd=repo)


class TestCredentialsFiles:
    @pytest.mark.parametrize(
        "name",
        [
            "credentials",
            "credentials.json",
            "credentials.yml",
            "credentials.yaml",
            "credentials.toml",
            "credentials.ini",
        ],
    )
    def test_secret_shaped_credentials_files_are_denied(
        self, policy: SecretPathPolicy, repo: Path, name: str
    ) -> None:
        assert policy.denies(f"config/{name}", cwd=repo)

    def test_credentials_source_code_is_allowed(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        """vox's own src/punt_vox/providers/credentials.py is code, not a secret."""
        assert not policy.denies("src/punt_vox/providers/credentials.py", cwd=repo)


class TestPiNormalization:
    """The path judged is the path pi opens: ``@``, ``~``, case all handled."""

    def test_a_leading_at_sign_is_stripped_as_pi_does(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        assert policy.denies("@.env.local", cwd=repo)

    @pytest.mark.parametrize("candidate", [".ENV", "Config/.Env.Local", "KEY.PEM"])
    def test_patterns_match_case_insensitively(
        self, policy: SecretPathPolicy, repo: Path, candidate: str
    ) -> None:
        assert policy.denies(candidate, cwd=repo)

    def test_a_relative_path_resolves_against_the_cwd_not_the_root(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        (repo / "src" / "config").mkdir()
        assert policy.denies("config/../../.env", cwd=repo / "src")


class TestConfinement:
    """Anything outside the repository root is denied, however it is spelled."""

    @pytest.mark.parametrize(
        "candidate",
        [
            "/proc/self/environ",
            "~/.claude/.credentials.json",
            "~/.config/gh/hosts.yml",
            "@~/.ssh/id_ed25519",
            "/etc/hosts",
            "../outside.txt",
            "src/../../outside.txt",
            "file:///etc/passwd",
        ],
    )
    def test_paths_outside_the_repo_are_denied(
        self, policy: SecretPathPolicy, repo: Path, candidate: str
    ) -> None:
        assert policy.denies(candidate, cwd=repo)

    def test_a_symlink_inside_the_repo_pointing_outside_is_denied(
        self, policy: SecretPathPolicy, repo: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("not in the repo")
        (repo / "innocent.txt").symlink_to(outside)
        assert policy.denies("innocent.txt", cwd=repo)

    def test_an_innocent_name_linked_to_a_secret_is_denied(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        (repo / ".env").write_text("FAKE_API_KEY=not-real\n")
        (repo / "notes.txt").symlink_to(repo / ".env")
        assert policy.denies("notes.txt", cwd=repo)


class TestGitignoreSecretPatterns:
    def test_a_secret_looking_gitignore_line_is_denied(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        assert policy.denies(".envrc.local", cwd=repo)

    def test_an_anchored_gitignore_glob_is_denied(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        assert policy.denies(".punt-labs/ethos/identities/claude.local.yaml", cwd=repo)

    def test_an_anchored_glob_is_denied_from_a_subdirectory_cwd(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        assert policy.denies("claude.local.yaml", cwd=repo / ".punt-labs" / "ethos")

    def test_a_token_file_from_gitignore_is_denied(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        assert policy.denies("spikes/agent-token.json", cwd=repo)

    def test_non_secret_gitignore_lines_are_not_adopted(
        self, policy: SecretPathPolicy
    ) -> None:
        assert "dist/" not in policy.patterns

    def test_negations_and_comments_are_not_adopted(
        self, policy: SecretPathPolicy
    ) -> None:
        assert not any(p.startswith(("!", "#")) for p in policy.patterns)

    def test_a_repo_without_a_gitignore_still_gets_the_builtins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # git must not find the repo these tests run inside.
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
        assert SecretPathPolicy.for_repo(tmp_path).denies(".env", cwd=tmp_path)


class TestEveryIgnoreSourceGitHonours:
    """A path git ignores is denied, whichever file ignores it."""

    @pytest.fixture
    def nested(self, repo: Path) -> Path:
        (repo / "sub").mkdir()
        (repo / "sub" / ".gitignore").write_text("creds.json\n*.txt\n!keep.txt\n")
        (repo / "sub" / "creds.json").write_text('{"token": "not-real"}\n')
        (repo / "sub" / "keep.txt").write_text("plain\n")
        (repo / ".git" / "info" / "exclude").write_text("private.md\n")
        return repo

    @pytest.mark.parametrize("path", ["sub/creds.json", "sub/notes.txt", "private.md"])
    def test_an_ignored_path_is_denied(self, nested: Path, path: str) -> None:
        assert SecretPathPolicy.for_repo(nested).denies(path, cwd=nested)

    def test_a_negated_nested_pattern_is_allowed(self, nested: Path) -> None:
        assert not SecretPathPolicy.for_repo(nested).denies("sub/keep.txt", cwd=nested)

    def test_a_git_that_cannot_answer_denies(
        self, nested: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = SecretPathPolicy.for_repo(nested)
        monkeypatch.setenv("PATH", "")
        assert policy.denies("sub/keep.txt", cwd=nested)

    def test_the_guard_is_told_to_ask_git(self, nested: Path) -> None:
        env = SecretPathPolicy.for_repo(nested).guard_env()
        assert json.loads(env["VOX_CALL_GUARD_GIT_IGNORES"]) is True


class TestGitInternals:
    """.git/ holds remote URLs and hooks; .git-credentials holds passwords."""

    @pytest.mark.parametrize(
        "path", [".git/config", ".git", ".git-credentials", "sub/.git-credentials"]
    )
    def test_git_internals_are_denied(
        self, policy: SecretPathPolicy, repo: Path, path: str
    ) -> None:
        assert policy.denies(path, cwd=repo)

    def test_a_glob_that_excludes_git_is_not_aimed_at_it(
        self, policy: SecretPathPolicy
    ) -> None:
        """``!.git`` keeps a grep out of .git; it names what is not read."""
        assert not policy.denies_glob("!.git")


class TestGlobs:
    @pytest.mark.parametrize("glob", ["**/.env*", "*.env*", ".ENV", "**/*.pem"])
    def test_a_glob_aimed_at_secrets_is_denied(
        self, policy: SecretPathPolicy, glob: str
    ) -> None:
        assert policy.denies_glob(glob)

    @pytest.mark.parametrize("glob", ["*.py", "**/*.md", "src/**"])
    def test_an_ordinary_glob_is_allowed(
        self, policy: SecretPathPolicy, glob: str
    ) -> None:
        assert not policy.denies_glob(glob)


class TestGuardConfig:
    def test_the_guard_gets_the_resolved_root_and_every_pattern(
        self, policy: SecretPathPolicy, repo: Path
    ) -> None:
        env = policy.guard_env()
        assert (
            env["VOX_CALL_GUARD_ROOT"],
            json.loads(env["VOX_CALL_GUARD_PATTERNS"]),
        ) == (str(repo.resolve()), list(policy.patterns))


class TestPromptClause:
    def test_the_prompt_clause_names_every_pattern(
        self, policy: SecretPathPolicy
    ) -> None:
        clause = policy.prompt_clause()
        assert all(pattern in clause for pattern in policy.patterns)


def test_an_undecodable_gitignore_fails_the_call_start(repo: Path) -> None:
    """The deny-list cannot be built without it, so the agent must not start."""
    (repo / ".gitignore").write_bytes(b"\xff\xfe not utf-8 \xff")
    with pytest.raises(CallAgentSpawnError, match=r"\.gitignore"):
        SecretPathPolicy.for_repo(repo)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-000 file regardless of its mode"
)
def test_a_gitignore_without_read_permission_fails_the_call_start(
    repo: Path,
) -> None:
    gitignore = repo / ".gitignore"
    gitignore.chmod(0)
    try:
        with pytest.raises(CallAgentSpawnError, match="Permission denied"):
            SecretPathPolicy.for_repo(repo)
    finally:
        gitignore.chmod(0o644)


def test_a_gitignore_that_is_not_a_file_fails_the_call_start(repo: Path) -> None:
    """Only a missing .gitignore means no repo patterns; anything else is an error."""
    (repo / ".gitignore").unlink()
    (repo / ".gitignore").mkdir()
    with pytest.raises(CallAgentSpawnError, match=r"\.gitignore"):
        SecretPathPolicy.for_repo(repo)
