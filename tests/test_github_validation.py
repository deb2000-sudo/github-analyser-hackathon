"""GitHub URL validation tests."""

from __future__ import annotations

import pytest

from app.github.validation import (
    InvalidGithubUrlError,
    UnsupportedGithubUrlError,
    normalize_github_repo_url,
)


def test_normalize_standard_repo_url():
    assert (
        normalize_github_repo_url("https://github.com/owner/repo")
        == "https://github.com/owner/repo"
    )


def test_normalize_strips_git_suffix_and_trailing_slash():
    assert (
        normalize_github_repo_url("https://github.com/owner/repo.git/")
        == "https://github.com/owner/repo"
    )


def test_normalize_accepts_tree_path():
    assert (
        normalize_github_repo_url("https://github.com/owner/repo/tree/main/src")
        == "https://github.com/owner/repo"
    )


def test_rejects_single_segment():
    with pytest.raises(InvalidGithubUrlError):
        normalize_github_repo_url("https://github.com/owner")


def test_rejects_issues_url():
    with pytest.raises(InvalidGithubUrlError):
        normalize_github_repo_url("https://github.com/owner/repo/issues/1")


def test_rejects_gist():
    with pytest.raises(UnsupportedGithubUrlError):
        normalize_github_repo_url("https://gist.github.com/user/abc123")


def test_rejects_non_github_host():
    with pytest.raises(InvalidGithubUrlError):
        normalize_github_repo_url("https://gitlab.com/owner/repo")
