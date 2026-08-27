from __future__ import annotations

import re
from typing import Any

from app.metrics.ai_packages import AI_PACKAGES

# Package name (lowercase) -> provider id
PACKAGE_TO_PROVIDER: dict[str, str] = {
    "openai": "openai",
    "@ai-sdk/openai": "openai",
    "langchain-openai": "openai",
    "openai-agents": "openai",
    "anthropic": "anthropic",
    "@anthropic-ai/sdk": "anthropic",
    "@ai-sdk/anthropic": "anthropic",
    "langchain-anthropic": "anthropic",
    "google-generativeai": "google_gemini",
    "google-genai": "google_gemini",
    "vertexai": "google_gemini",
    "google-cloud-aiplatform": "google_gemini",
    "langchain-google-genai": "google_gemini",
    "langchain-google-vertexai": "google_gemini",
    "@google/generative-ai": "google_gemini",
    "groq": "groq",
    "cohere": "cohere",
    "mistralai": "mistral",
    "together": "together",
    "fireworks-ai": "fireworks",
    "replicate": "replicate",
    "huggingface-hub": "huggingface",
    "transformers": "huggingface",
    "instructor": "instructor",
    "litellm": "litellm",
    "ai": "vercel_ai_sdk",
    "ollama": "ollama",
    "langchain-ollama": "ollama",
}

PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google_gemini": "Google Gemini",
    "groq": "Groq",
    "cohere": "Cohere",
    "mistral": "Mistral",
    "together": "Together AI",
    "fireworks": "Fireworks AI",
    "replicate": "Replicate",
    "huggingface": "Hugging Face",
    "instructor": "Instructor",
    "litellm": "LiteLLM",
    "vercel_ai_sdk": "Vercel AI SDK",
    "ollama": "Ollama",
    "bedrock": "AWS Bedrock",
    "azure_openai": "Azure OpenAI",
}

# Provider id -> regex patterns to find in source / config
PROVIDER_CODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai": re.compile(
        r"\bopenai\b|ChatOpenAI|OpenAI\(|from openai|@ai-sdk/openai|"
        r"gpt-4[o\.]?[\w-]*|gpt-3\.5|gpt-4|o1-preview|o1-mini|o3-",
        re.I,
    ),
    "anthropic": re.compile(
        r"\banthropic\b|ChatAnthropic|Claude|claude-3|claude-2|claude-opus|claude-sonnet",
        re.I,
    ),
    "google_gemini": re.compile(
        r"gemini[-_.]?[\d\.a-z]*|models/gemini|GenerativeModel|google\.generativeai|"
        r"vertexai|ChatVertexAI|GoogleGenerativeAI",
        re.I,
    ),
    "groq": re.compile(r"\bgroq\b|Groq\(|ChatGroq|groq-", re.I),
    "cohere": re.compile(r"\bcohere\b|ChatCohere|command-r", re.I),
    "mistral": re.compile(r"\bmistral\b|mistral-|Mixtral", re.I),
    "together": re.compile(r"\btogether\b|Together\(", re.I),
    "fireworks": re.compile(r"\bfireworks\b|Fireworks", re.I),
    "replicate": re.compile(r"\breplicate\b|Replicate", re.I),
    "huggingface": re.compile(
        r"huggingface|transformers\.|InferenceClient|AutoModelForCausalLM",
        re.I,
    ),
    "ollama": re.compile(r"\bollama\b|ChatOllama|Ollama\(", re.I),
    "litellm": re.compile(r"\blitellm\b|completion\(", re.I),
    "bedrock": re.compile(r"bedrock|BedrockRuntime|ChatBedrock", re.I),
    "azure_openai": re.compile(r"AzureChatOpenAI|azure\.openai|AZURE_OPENAI", re.I),
}

MODEL_HINT_RE = re.compile(
    r"(gpt-4[o\.]?[\w-]*|gpt-3\.5[\w-]*|claude-[\w-]+|gemini-[\w\.]+|"
    r"llama[\w-]*|mixtral[\w-]*|mistral[\w-]*|groq-[\w-]+|command-r[\w-]*|"
    r"o1-[\w-]+|o3-[\w-]+)",
    re.I,
)


def _provider_from_package(pkg: str) -> str | None:
    key = pkg.lower()
    if key in PACKAGE_TO_PROVIDER:
        return PACKAGE_TO_PROVIDER[key]
    if AI_PACKAGES.get(key) == "llm_sdk":
        # Unknown LLM SDK — use package basename as provider id
        base = key.split("/")[-1].replace("-", "_")
        return base
    if key.startswith("langchain-") and key not in PACKAGE_TO_PROVIDER:
        suffix = key.replace("langchain-", "").replace("-", "_")
        if suffix not in ("core", "community", "langgraph"):
            return suffix
    return None


def detect_llm_providers(
    ai_dependencies: list[str],
    file_contents: dict[str, str],
    evidence_files: list[str] | None = None,
) -> dict[str, Any]:
    """Detect all LLM providers used via dependencies and/or source code."""
    by_provider: dict[str, dict[str, Any]] = {}

    def _ensure(provider_id: str) -> dict[str, Any]:
        if provider_id not in by_provider:
            by_provider[provider_id] = {
                "provider": provider_id,
                "label": PROVIDER_LABELS.get(
                    provider_id, provider_id.replace("_", " ").title()
                ),
                "dependencies": [],
                "evidence_files": [],
                "model_hints": [],
            }
        return by_provider[provider_id]

    for dep in ai_dependencies:
        pid = _provider_from_package(dep)
        if pid:
            _ensure(pid)["dependencies"].append(dep)

    search_paths = list(evidence_files or []) or list(file_contents.keys())
    if not search_paths:
        search_paths = list(file_contents.keys())

    all_model_hints: set[str] = set()
    for path in search_paths:
        content = file_contents.get(path)
        if not content:
            continue
        for hint in MODEL_HINT_RE.findall(content):
            all_model_hints.add(hint)
        for pid, pattern in PROVIDER_CODE_PATTERNS.items():
            if pattern.search(content):
                entry = _ensure(pid)
                if path not in entry["evidence_files"]:
                    entry["evidence_files"].append(path)

    # Broader scan if nothing found yet
    if not by_provider:
        for path, content in file_contents.items():
            if not content or not path.endswith(
                (".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".toml", ".yaml", ".yml", ".env.example")
            ):
                continue
            for hint in MODEL_HINT_RE.findall(content):
                all_model_hints.add(hint)
            for pid, pattern in PROVIDER_CODE_PATTERNS.items():
                if pattern.search(content):
                    entry = _ensure(pid)
                    if path not in entry["evidence_files"]:
                        entry["evidence_files"].append(path)

    # Attach model hints to providers heuristically
    for hint in all_model_hints:
        h = hint.lower()
        target: str | None = None
        if h.startswith("gpt") or h.startswith("o1") or h.startswith("o3"):
            target = "openai"
        elif h.startswith("claude"):
            target = "anthropic"
        elif h.startswith("gemini"):
            target = "google_gemini"
        elif h.startswith("groq"):
            target = "groq"
        elif "llama" in h or "mixtral" in h:
            target = "huggingface"
        elif h.startswith("mistral"):
            target = "mistral"
        elif h.startswith("command"):
            target = "cohere"
        if target and target in by_provider:
            hints = by_provider[target]["model_hints"]
            if hint not in hints:
                hints.append(hint)

    providers = sorted(by_provider.values(), key=lambda p: p["label"])
    for p in providers:
        p["evidence_files"] = p["evidence_files"][:8]
        p["model_hints"] = p["model_hints"][:8]

    uses_llm = bool(providers)
    names = [p["label"] for p in providers]

    if uses_llm:
        parts = []
        for p in providers:
            bits = [p["label"]]
            if p["dependencies"]:
                bits.append(f"deps: {', '.join(p['dependencies'][:3])}")
            if p["model_hints"]:
                bits.append(f"models: {', '.join(p['model_hints'][:3])}")
            if p["evidence_files"]:
                bits.append(f"files: {', '.join(p['evidence_files'][:2])}")
            parts.append("; ".join(bits))
        reasoning = "LLM provider(s) detected — " + " | ".join(parts[:4])
    else:
        reasoning = "No LLM provider SDK, model name, or API usage detected in scanned files."

    return {
        "uses_llm": uses_llm,
        "providers_detected": providers,
        "provider_names": names,
        "model_hints": sorted(all_model_hints)[:15],
        "reasoning": reasoning,
    }
