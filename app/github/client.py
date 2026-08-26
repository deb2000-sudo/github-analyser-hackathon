from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.gcs_cache import GcsCache

GITHUB_API = "https://api.github.com"


@dataclass
class RepoRef:
    owner: str
    name: str
    default_branch: str = "main"
    commit_sha: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class RepoSnapshot:
    """Fetched (or cached) view of a repository used by all metrics."""

    ref: RepoRef
    tree: list[dict[str, Any]] = field(default_factory=list)
    file_contents: dict[str, str] = field(default_factory=dict)
    commits: list[dict[str, Any]] = field(default_factory=list)
    contributors: list[dict[str, Any]] = field(default_factory=list)
    package_manifests: dict[str, str] = field(default_factory=dict)


def parse_github_url(url: str) -> RepoRef:
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Cannot parse owner/repo from URL: {url}")
    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]
    return RepoRef(owner=owner, name=name)


class GithubClient:
    def __init__(self, settings: Settings | None = None, cache: GcsCache | None = None):
        self.settings = settings or get_settings()
        self.cache = cache if cache is not None else GcsCache(self.settings)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-analyser-hackathon",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        self._headers = headers

    async def _get(self, client: httpx.AsyncClient, path: str, **params: Any) -> Any:
        resp = await client.get(
            f"{GITHUB_API}{path}", params={k: v for k, v in params.items() if v is not None}
        )
        resp.raise_for_status()
        return resp.json()

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        if not self.cache.enabled:
            return None
        return await asyncio.to_thread(self.cache.get, key)

    async def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        if not self.cache.enabled:
            return
        await asyncio.to_thread(self.cache.set, key, payload)

    async def fetch_snapshot(
        self,
        github_url: str,
        *,
        extra_paths: list[str] | None = None,
        max_file_kb: int = 80,
    ) -> RepoSnapshot:
        ref = parse_github_url(github_url)
        async with httpx.AsyncClient(headers=self._headers, timeout=60.0, follow_redirects=True) as client:
            repo = await self._get(client, f"/repos/{ref.owner}/{ref.name}")
            ref.default_branch = repo.get("default_branch") or "main"
            branch = await self._get(
                client, f"/repos/{ref.owner}/{ref.name}/branches/{ref.default_branch}"
            )
            ref.commit_sha = branch["commit"]["sha"]

            cache_key = f"{ref.full_name}@{ref.commit_sha}:snapshot"
            cached = await self._cache_get(cache_key)
            if cached:
                return RepoSnapshot(
                    ref=ref,
                    tree=cached.get("tree", []),
                    file_contents=cached.get("file_contents", {}),
                    commits=cached.get("commits", []),
                    contributors=cached.get("contributors", []),
                    package_manifests=cached.get("package_manifests", {}),
                )

            tree_resp = await self._get(
                client,
                f"/repos/{ref.owner}/{ref.name}/git/trees/{ref.commit_sha}",
                recursive="1",
            )
            tree = [t for t in tree_resp.get("tree", []) if t.get("type") == "blob"]

            commits = await self._paginate(
                client, f"/repos/{ref.owner}/{ref.name}/commits", per_page=100, max_pages=5
            )
            try:
                contributors = await self._paginate(
                    client,
                    f"/repos/{ref.owner}/{ref.name}/contributors",
                    per_page=100,
                    max_pages=1,
                )
            except httpx.HTTPStatusError:
                contributors = []

            manifest_names = {
                "package.json",
                "package-lock.json",
                "requirements.txt",
                "pyproject.toml",
                "Pipfile",
                "poetry.lock",
                "go.mod",
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
                "Cargo.toml",
                "composer.json",
                "Gemfile",
                "environment.yml",
                "conda.yml",
            }
            paths_to_fetch = [
                t["path"]
                for t in tree
                if t["path"].split("/")[-1] in manifest_names
                or any(
                    t["path"].endswith(s)
                    for s in ("/App.tsx", "/App.jsx", "/App.js", "/App.ts", "/main.py", "/index.html")
                )
            ]
            if extra_paths:
                paths_to_fetch.extend(extra_paths)
            seen: set[str] = set()
            unique_paths: list[str] = []
            for p in paths_to_fetch:
                if p not in seen:
                    seen.add(p)
                    unique_paths.append(p)

            file_contents: dict[str, str] = {}
            package_manifests: dict[str, str] = {}
            max_bytes = max_file_kb * 1024
            for path in unique_paths[:80]:
                content = await self._get_raw_file(client, ref, path, max_bytes=max_bytes)
                if content is None:
                    continue
                file_contents[path] = content
                if path.split("/")[-1] in manifest_names:
                    package_manifests[path] = content

            snapshot = RepoSnapshot(
                ref=ref,
                tree=tree,
                file_contents=file_contents,
                commits=commits,
                contributors=contributors,
                package_manifests=package_manifests,
            )
            await self._cache_set(
                cache_key,
                {
                    "tree": tree,
                    "file_contents": file_contents,
                    "commits": commits,
                    "contributors": contributors,
                    "package_manifests": package_manifests,
                },
            )
            return snapshot

    async def fetch_files(
        self,
        snapshot: RepoSnapshot,
        paths: list[str],
        *,
        max_file_kb: int = 40,
    ) -> dict[str, str]:
        missing = [p for p in paths if p not in snapshot.file_contents]
        if not missing:
            return {p: snapshot.file_contents[p] for p in paths if p in snapshot.file_contents}

        async with httpx.AsyncClient(
            headers=self._headers, timeout=60.0, follow_redirects=True
        ) as client:
            max_bytes = max_file_kb * 1024
            for path in missing[:40]:
                content = await self._get_raw_file(client, snapshot.ref, path, max_bytes=max_bytes)
                if content is not None:
                    snapshot.file_contents[path] = content
        return {p: snapshot.file_contents[p] for p in paths if p in snapshot.file_contents}

    async def _get_raw_file(
        self,
        client: httpx.AsyncClient,
        ref: RepoRef,
        path: str,
        *,
        max_bytes: int,
    ) -> str | None:
        url = f"https://raw.githubusercontent.com/{ref.owner}/{ref.name}/{ref.commit_sha}/{path}"
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        if len(resp.content) > max_bytes:
            return resp.content[:max_bytes].decode("utf-8", errors="replace") + "\n...[truncated]"
        return resp.text

    async def _paginate(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        per_page: int = 100,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = await self._get(client, path, per_page=per_page, page=page)
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < per_page:
                break
        return items


PATH_HINTS = re.compile(r"(agent|orchestrat|langchain|crewai|autogen|langgraph|rag|llm)", re.I)


def paths_matching(tree: list[dict[str, Any]], pattern: re.Pattern[str]) -> list[str]:
    return [t["path"] for t in tree if pattern.search(t["path"])]
