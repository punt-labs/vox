"""Tests for :class:`SecretPathPolicy`: the call agent's secret-path deny-list."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".gitignore").write_text(
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
    return tmp_path


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
            "~/.ssh/id_ed25519",
            "/home/someone/.aws/credentials",
            "/home/someone/.pi/agent/auth.json",
            "/home/someone/.punt-labs/vox/keys.env",
        ],
    )
    def test_well_known_secret_paths_are_denied(
        self, policy: SecretPathPolicy, candidate: str
    ) -> None:
        assert policy.denies(candidate)

    @pytest.mark.parametrize(
        "candidate",
        ["README.md", "src/punt_vox/paths.py", "docs/environment.md", "."],
    )
    def test_ordinary_paths_are_allowed(
        self, policy: SecretPathPolicy, candidate: str
    ) -> None:
        assert not policy.denies(candidate)

    def test_an_empty_candidate_is_not_denied(self, policy: SecretPathPolicy) -> None:
        assert not policy.denies("  ")

    def test_a_glob_naming_env_files_is_denied(self, policy: SecretPathPolicy) -> None:
        """A grep/find glob aimed at env files is refused like a direct path."""
        assert policy.denies("**/.env*")


class TestGitignoreSecretPatterns:
    def test_a_secret_looking_gitignore_line_is_denied(
        self, policy: SecretPathPolicy
    ) -> None:
        assert policy.denies(".envrc.local")

    def test_an_anchored_gitignore_glob_is_denied(
        self, policy: SecretPathPolicy
    ) -> None:
        assert policy.denies(".punt-labs/ethos/identities/claude.local.yaml")

    def test_a_token_file_from_gitignore_is_denied(
        self, policy: SecretPathPolicy
    ) -> None:
        assert policy.denies("spikes/agent-token.json")

    def test_non_secret_gitignore_lines_are_not_adopted(
        self, policy: SecretPathPolicy
    ) -> None:
        assert "dist/" not in policy.patterns

    def test_negations_and_comments_are_not_adopted(
        self, policy: SecretPathPolicy
    ) -> None:
        assert not any(p.startswith(("!", "#")) for p in policy.patterns)

    def test_a_repo_without_a_gitignore_still_gets_the_builtins(
        self, tmp_path: Path
    ) -> None:
        assert SecretPathPolicy.for_repo(tmp_path).denies(".env")


class TestSymlinks:
    def test_an_innocent_name_linked_to_a_secret_is_denied(self, repo: Path) -> None:
        (repo / ".env").write_text("FAKE_API_KEY=not-real\n")
        (repo / "notes.txt").symlink_to(repo / ".env")
        assert SecretPathPolicy.for_repo(repo).denies("notes.txt")


class TestPromptClause:
    def test_the_prompt_clause_names_every_pattern(
        self, policy: SecretPathPolicy
    ) -> None:
        clause = policy.prompt_clause()
        assert all(pattern in clause for pattern in policy.patterns)
