"""Tests for :class:`SecretPathPolicy`: the call agent's secret-path deny-list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
        self, tmp_path: Path
    ) -> None:
        assert SecretPathPolicy.for_repo(tmp_path).denies(".env", cwd=tmp_path)


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
