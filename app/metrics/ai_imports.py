from __future__ import annotations

import json
import re
from typing import Any

from app.metrics.ai_packages import AI_PACKAGES, is_agent_framework

# Longest / most specific patterns first when scanning code.
_CODE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("@langchain/langgraph", re.compile(r"@langchain/langgraph|langchain/langgraph", re.I)),
    ("@langchain/core", re.compile(r"@langchain/core|from ['\"]@langchain/core", re.I)),
    ("langchain-openai", re.compile(r"langchain_openai|langchain-openai", re.I)),
    ("langchain-anthropic", re.compile(r"langchain_anthropic|langchain-anthropic", re.I)),
    ("langchain-community", re.compile(r"langchain_community|langchain-community", re.I)),
    ("langchain-core", re.compile(r"langchain_core|langchain-core", re.I)),
    ("langchain", re.compile(r"(?:from|import)\s+langchain\b|require\(['\"]langchain['\"]\)", re.I)),
    ("langgraph", re.compile(r"(?:from|import)\s+langgraph\b|require\(['\"]langgraph['\"]\)|@langchain/langgraph", re.I)),
    ("openai-agents", re.compile(r"openai\.agents|openai-agents|from agents import", re.I)),
    ("@ai-sdk/openai", re.compile(r"@ai-sdk/openai", re.I)),
    ("@ai-sdk/anthropic", re.compile(r"@ai-sdk/anthropic", re.I)),
    ("@anthropic-ai/sdk", re.compile(r"@anthropic-ai/sdk", re.I)),
    ("google-generativeai", re.compile(r"google\.generativeai|google-generativeai|import genai", re.I)),
    ("google-genai", re.compile(r"google\.genai|google-genai", re.I)),
    ("vertexai", re.compile(r"(?:from|import)\s+vertexai\b|vertexai\.generative_models", re.I)),
    ("langsmith", re.compile(r"(?:from|import)\s+langsmith\b", re.I)),
    ("llama-index", re.compile(r"llama_index|llama-index|from llama_index", re.I)),
    ("crewai", re.compile(r"(?:from|import)\s+crewai\b", re.I)),
    ("autogen", re.compile(r"(?:from|import)\s+autogen\b|pyautogen", re.I)),
    ("ag2", re.compile(r"(?:from|import)\s+ag2\b", re.I)),
    ("semantic-kernel", re.compile(r"semantic_kernel|semantic-kernel", re.I)),
    ("haystack-ai", re.compile(r"haystack|Haystack", re.I)),
    ("dspy", re.compile(r"(?:from|import)\s+dspy\b", re.I)),
    ("phidata", re.compile(r"(?:from|import)\s+phi\b|phidata", re.I)),
    ("smolagents", re.compile(r"smolagents", re.I)),
    ("pydantic-ai", re.compile(r"pydantic_ai|pydantic-ai", re.I)),
    ("instructor", re.compile(r"(?:from|import)\s+instructor\b", re.I)),
    ("litellm", re.compile(r"(?:from|import)\s+litellm\b", re.I)),
    ("openai", re.compile(r"(?:from|import)\s+openai\b|require\(['\"]openai['\"]\)|OpenAI\(", re.I)),
    ("anthropic", re.compile(r"(?:from|import)\s+anthropic\b|Anthropic\(", re.I)),
    ("groq", re.compile(r"(?:from|import)\s+groq\b|Groq\(", re.I)),
    ("cohere", re.compile(r"(?:from|import)\s+cohere\b", re.I)),
    ("mistralai", re.compile(r"(?:from|import)\s+mistralai\b|Mistral\(", re.I)),
    ("together", re.compile(r"(?:from|import)\s+together\b", re.I)),
    ("replicate", re.compile(r"(?:from|import)\s+replicate\b", re.I)),
    ("chromadb", re.compile(r"(?:from|import)\s+chromadb\b|import chroma", re.I)),
    ("pinecone", re.compile(r"(?:from|import)\s+pinecone\b", re.I)),
    ("weaviate", re.compile(r"weaviate", re.I)),
    ("qdrant-client", re.compile(r"qdrant_client|qdrant-client", re.I)),
    ("transformers", re.compile(r"(?:from|import)\s+transformers\b", re.I)),
    ("huggingface-hub", re.compile(r"huggingface_hub|huggingface-hub", re.I)),
    ("ollama", re.compile(r"(?:from|import)\s+ollama\b|ChatOllama", re.I)),
]

# Generic AI usage without a known SDK (backend API routes, Vertex, etc.)
_GENERIC_AI_CODE = re.compile(
    r"generateContent|chat\.completions|ChatCompletion|/v1/chat/completions|"
    r"GenerativeModel|invoke_llm|call_llm|llm_client|LLMClient|"
    r"openai\.ChatCompletion|anthropic\.messages|"
    r"AZURE_OPENAI|OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|"
    r"scoring_mode['\"]?\s*:\s*['\"]ai['\"]|aiPrompts|evaluateSubmission",
    re.I,
)

_SOURCE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".kt")


def _normalize_pkg(name: str) -> str:
    name = name.strip().strip("\"'").lower()
    name = re.split(r"[<=>!~\s\[]", name, maxsplit=1)[0]
    return name


def scan_manifests(manifests: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (all_ai_deps, agent_framework_deps) — exact manifest matches only."""
    found: set[str] = set()
    for path, content in manifests.items():
        lower_path = path.lower()
        if lower_path.endswith("package.json"):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
            deps = {
                **(data.get("dependencies") or {}),
                **(data.get("devDependencies") or {}),
                **(data.get("peerDependencies") or {}),
            }
            for pkg in deps:
                key = pkg.lower()
                if key in AI_PACKAGES:
                    found.add(key)
        elif lower_path.endswith(("requirements.txt", "requirements-dev.txt")):
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg = _normalize_pkg(line)
                if pkg in AI_PACKAGES:
                    found.add(pkg)
        elif lower_path.endswith("pyproject.toml"):
            for m in re.finditer(
                r'["\']([a-zA-Z0-9_.\-@/]+)["\']\s*[=>~<]|^\s*([a-zA-Z0-9_.\-]+)\s*[=>~<]',
                content,
                re.M,
            ):
                pkg = _normalize_pkg(m.group(1) or m.group(2) or "")
                if pkg in AI_PACKAGES:
                    found.add(pkg)
            for line in content.splitlines():
                m = re.match(r'^\s*([a-zA-Z0-9_.\-]+)\s*=', line)
                if m:
                    pkg = _normalize_pkg(m.group(1))
                    if pkg in AI_PACKAGES:
                        found.add(pkg)
        elif lower_path.endswith("go.mod"):
            for line in content.splitlines():
                parts = line.strip().split()
                if len(parts) >= 1:
                    mod = parts[0].lower()
                    tail = mod.split("/")[-1]
                    if tail in AI_PACKAGES:
                        found.add(tail)

    ai_list = sorted(found)
    agent_list = [p for p in ai_list if is_agent_framework(p)]
    return ai_list, agent_list


def scan_code_for_ai_imports(file_contents: dict[str, str]) -> dict[str, list[str]]:
    """Map AI package -> source files where imports/API usage was found."""
    hits: dict[str, list[str]] = {}
    for path, content in file_contents.items():
        if not content or not path.endswith(_SOURCE_EXTS):
            continue
        if "node_modules" in path or ".venv" in path or "/dist/" in path:
            continue
        for pkg, pattern in _CODE_PATTERNS:
            if pattern.search(content):
                hits.setdefault(pkg, [])
                if path not in hits[pkg]:
                    hits[pkg].append(path)
    return hits


def scan_generic_ai_usage(file_contents: dict[str, str]) -> list[str]:
    """Files with AI-related API patterns but no known SDK import."""
    paths: list[str] = []
    for path, content in file_contents.items():
        if not content or not path.endswith(_SOURCE_EXTS):
            continue
        if _GENERIC_AI_CODE.search(content):
            paths.append(path)
    return paths


def reconcile_ai_dependencies(
    manifest_deps: list[str],
    code_hits: dict[str, list[str]],
) -> dict[str, Any]:
    """
    Verify AI deps: agent frameworks require code imports; LLM SDKs may come from manifest only.
    Returns verified deps plus diagnostics for the metric payload.
    """
    manifest_set = set(manifest_deps)
    code_set = set(code_hits.keys())

    verified: set[str] = set()
    manifest_only: list[str] = []
    rejected_manifest: list[str] = []

    for dep in manifest_deps:
        category = AI_PACKAGES.get(dep, "")
        if dep in code_hits:
            verified.add(dep)
        elif category == "agent_framework":
            rejected_manifest.append(dep)
        elif category in ("llm_sdk", "vector_db", "ml"):
            verified.add(dep)
            manifest_only.append(dep)
        else:
            verified.add(dep)
            manifest_only.append(dep)

    for dep in code_set:
        verified.add(dep)

    verified_list = sorted(verified)
    agent_verified = [p for p in verified_list if is_agent_framework(p)]
    code_evidence_files = sorted({f for files in code_hits.values() for f in files})

    return {
        "ai_dependencies_found": verified_list,
        "agent_frameworks_found": agent_verified,
        "manifest_deps_raw": sorted(manifest_set),
        "code_imports_found": sorted(code_set),
        "manifest_only_deps": sorted(set(manifest_only)),
        "rejected_false_manifest_deps": sorted(rejected_manifest),
        "code_evidence_by_package": {k: v for k, v in sorted(code_hits.items())},
        "code_evidence_files": code_evidence_files,
    }


def select_ai_evidence_paths(
    tree_paths: list[str],
    file_contents: dict[str, str],
    verified_deps: list[str],
    code_hits: dict[str, list[str]],
    *,
    max_files: int = 12,
) -> list[str]:
    """Prefer files with verified import evidence, then backend manifests and server code."""
    paths: list[str] = []
    for dep in verified_deps:
        paths.extend(code_hits.get(dep, []))

    generic = scan_generic_ai_usage(file_contents)
    paths.extend(generic)

    backendish = [
        p
        for p in tree_paths
        if p.endswith(_SOURCE_EXTS)
        and any(part in p.lower() for part in ("backend", "server", "api/", "app/", "src/"))
        and "node_modules" not in p
    ]
    for p in backendish:
        if p in file_contents and any(
            kw in file_contents[p].lower()
            for kw in ("openai", "langchain", "langgraph", "gemini", "anthropic", "llm", "generative")
        ):
            paths.append(p)

    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:max_files]
