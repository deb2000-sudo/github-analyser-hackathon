from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})
BLOCKED_PATH_PREFIXES = (
    "issues",
    "pull",
    "pulls",
    "settings",
    "actions",
    "projects",
    "wiki",
    "security",
    "pulse",
    "graphs",
    "marketplace",
    "sponsors",
    "orgs",
    "organizations",
    "login",
    "join",
    "explore",
    "topics",
    "collections",
    "events",
    "about",
)
OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


class InvalidGithubUrlError(ValueError):
    """Malformed GitHub repository URL."""


class UnsupportedGithubUrlError(ValueError):
    """Valid GitHub host but not a repository URL (gist, etc.)."""


@dataclass
class RepoAccessInfo:
    owner: str
    name: str
    is_public: bool
    exists: bool
    default_branch: str | None
    reason: str | None = None


def normalize_github_repo_url(url: str) -> str:
    """Validate and normalize a GitHub repo URL to https://github.com/owner/repo."""
    raw = url.strip()
    if not raw:
        raise InvalidGithubUrlError("github_url is required")

    if raw.startswith("git@"):
        raise UnsupportedGithubUrlError("SSH URLs are not supported; use https://github.com/owner/repo")

    if "gist.github.com" in raw or raw.startswith("https://gist."):
        raise UnsupportedGithubUrlError("Gist URLs are not supported")

    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidGithubUrlError("github_url must use github.com")

    path = parsed.path.strip("/")
    if not path:
        raise InvalidGithubUrlError("github_url must include owner and repository name")

    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        raise InvalidGithubUrlError("github_url must be https://github.com/owner/repo")

    if segments[0].lower() in BLOCKED_PATH_PREFIXES:
        raise UnsupportedGithubUrlError(f"URL path is not a repository: /{segments[0]}")

    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    if len(segments) > 2 and segments[2] not in ("tree", "blob", "src", "raw", "releases", "tags"):
        raise InvalidGithubUrlError(
            "github_url must point to a repository root, not a sub-page "
            "(use https://github.com/owner/repo)"
        )

    if not OWNER_REPO_RE.match(owner) or not OWNER_REPO_RE.match(repo):
        raise InvalidGithubUrlError("invalid owner or repository name in github_url")

    return f"https://github.com/{owner}/{repo}"


def access_payload(info: RepoAccessInfo) -> dict[str, object]:
    return {
        "valid_url": True,
        "is_public": info.is_public,
        "exists": info.exists,
        "owner": info.owner,
        "name": info.name,
        "reason": info.reason,
    }
