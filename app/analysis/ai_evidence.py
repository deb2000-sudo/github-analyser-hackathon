"""Phase 8: AI evidence levels from Code Facts, manifests, and README/UI mentions.

Not compiler-grade taint analysis. Does not use Gemini. Does not invent flows.
Level 4 means an invocation was found — not that user input reaches the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.metrics.ai_imports import scan_manifests
from app.metrics.ai_packages import AI_PACKAGES, is_agent_framework

LEVEL_NONE = 0
LEVEL_MENTION = 1
LEVEL_DEPENDENCY = 2
LEVEL_SDK = 3
LEVEL_INVOCATION = 4

PROVIDER_ORDER = (
    "openai",
    "gemini",
    "anthropic",
    "groq",
    "mistral",
    "huggingface",
    "ollama",
    "langchain",
    "llamaindex",
)

LLM_PROVIDERS = frozenset(
    {"openai", "gemini", "anthropic", "groq", "mistral", "huggingface", "ollama"}
)

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "mistral": "Mistral",
    "huggingface": "Hugging Face",
    "ollama": "Ollama",
    "langchain": "LangChain",
    "llamaindex": "LlamaIndex",
}

# Longest import prefixes first.
_IMPORT_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("@ai-sdk/openai", ("openai",)),
    ("@ai-sdk/anthropic", ("anthropic",)),
    ("@ai-sdk/google", ("gemini",)),
    ("@ai-sdk/groq", ("groq",)),
    ("@ai-sdk/mistral", ("mistral",)),
    ("@anthropic-ai/sdk", ("anthropic",)),
    ("@google/generative-ai", ("gemini",)),
    ("@google/genai", ("gemini",)),
    ("@llamaindex/core", ("llamaindex",)),
    ("@langchain/", ("langchain",)),
    ("langchain_google_vertexai", ("langchain", "gemini")),
    ("langchain_google_genai", ("langchain", "gemini")),
    ("langchain-google-vertexai", ("langchain", "gemini")),
    ("langchain-google-genai", ("langchain", "gemini")),
    ("langchain_openai", ("langchain", "openai")),
    ("langchain-openai", ("langchain", "openai")),
    ("langchain_anthropic", ("langchain", "anthropic")),
    ("langchain-anthropic", ("langchain", "anthropic")),
    ("langchain_mistralai", ("langchain", "mistral")),
    ("langchain-mistralai", ("langchain", "mistral")),
    ("langchain_huggingface", ("langchain", "huggingface")),
    ("langchain-huggingface", ("langchain", "huggingface")),
    ("langchain_ollama", ("langchain", "ollama")),
    ("langchain-ollama", ("langchain", "ollama")),
    ("langchain_groq", ("langchain", "groq")),
    ("langchain-groq", ("langchain", "groq")),
    ("langchain_community", ("langchain",)),
    ("langchain-community", ("langchain",)),
    ("langchain_core", ("langchain",)),
    ("langchain-core", ("langchain",)),
    ("google.cloud.aiplatform", ("gemini",)),
    ("google.generativeai", ("gemini",)),
    ("google.genai", ("gemini",)),
    ("huggingface_hub", ("huggingface",)),
    ("huggingface-hub", ("huggingface",)),
    ("llama_index", ("llamaindex",)),
    ("llama-index", ("llamaindex",)),
    ("llamaindex", ("llamaindex",)),
    ("vertexai", ("gemini",)),
    ("langgraph", ("langchain",)),
    ("langchain", ("langchain",)),
    ("transformers", ("huggingface",)),
    ("mistralai", ("mistral",)),
    ("anthropic", ("anthropic",)),
    ("openai", ("openai",)),
    ("ollama", ("ollama",)),
    ("groq", ("groq",)),
)

_PACKAGE_TO_PROVIDER: dict[str, tuple[str, ...]] = {
    "openai": ("openai",),
    "@ai-sdk/openai": ("openai",),
    "langchain-openai": ("langchain", "openai"),
    "openai-agents": ("openai",),
    "anthropic": ("anthropic",),
    "@anthropic-ai/sdk": ("anthropic",),
    "@ai-sdk/anthropic": ("anthropic",),
    "langchain-anthropic": ("langchain", "anthropic"),
    "google-generativeai": ("gemini",),
    "google-genai": ("gemini",),
    "vertexai": ("gemini",),
    "google-cloud-aiplatform": ("gemini",),
    "@google/generative-ai": ("gemini",),
    "@google/genai": ("gemini",),
    "langchain-google-genai": ("langchain", "gemini"),
    "langchain-google-vertexai": ("langchain", "gemini"),
    "groq": ("groq",),
    "langchain-groq": ("langchain", "groq"),
    "mistralai": ("mistral",),
    "langchain-mistralai": ("langchain", "mistral"),
    "huggingface-hub": ("huggingface",),
    "transformers": ("huggingface",),
    "ollama": ("ollama",),
    "langchain-ollama": ("langchain", "ollama"),
    "langchain": ("langchain",),
    "langchain-core": ("langchain",),
    "langchain-community": ("langchain",),
    "langgraph": ("langchain",),
    "@langchain/core": ("langchain",),
    "@langchain/langgraph": ("langchain",),
    "llama-index": ("llamaindex",),
    "llama_index": ("llamaindex",),
    "llamaindex": ("llamaindex",),
    "@llamaindex/core": ("llamaindex",),
}

_CLIENT_NAMES: dict[str, frozenset[str]] = {
    "openai": frozenset({"OpenAI", "AzureOpenAI", "AsyncOpenAI"}),
    "anthropic": frozenset({"Anthropic", "AsyncAnthropic"}),
    "gemini": frozenset(
        {
            "GenerativeModel",
            "GoogleGenerativeAI",
            "ChatGoogleGenerativeAI",
            "ChatVertexAI",
            "VertexAI",
        }
    ),
    "groq": frozenset({"Groq", "AsyncGroq", "ChatGroq"}),
    "mistral": frozenset({"Mistral", "MistralClient"}),
    "huggingface": frozenset(
        {"InferenceClient", "AsyncInferenceClient", "HuggingFaceEndpoint", "pipeline"}
    ),
    "ollama": frozenset({"Ollama", "ChatOllama", "AsyncClient"}),
    "langchain": frozenset(
        {
            "ChatOpenAI",
            "ChatAnthropic",
            "ChatGroq",
            "ChatOllama",
            "ChatVertexAI",
            "ChatGoogleGenerativeAI",
            "AgentExecutor",
            "ChatMistralAI",
        }
    ),
    "llamaindex": frozenset({"VectorStoreIndex", "OpenAIEmbedding"}),
}

_QUALIFIED_CLIENTS: dict[str, str] = {
    "genai.Client": "gemini",
    "genai.GenerativeModel": "gemini",
    "google.generativeai.GenerativeModel": "gemini",
    "google.genai.Client": "gemini",
    "vertexai.generative_models.GenerativeModel": "gemini",
    "openai.OpenAI": "openai",
    "anthropic.Anthropic": "anthropic",
    "groq.Groq": "groq",
    "mistralai.Mistral": "mistral",
    "huggingface_hub.InferenceClient": "huggingface",
    "ollama.Client": "ollama",
    "ollama.AsyncClient": "ollama",
}

# Provider-specific inference calls. Ambiguous suffixes need import/client context.
_SPECIFIC_INVOCATIONS: tuple[tuple[str, str], ...] = (
    ("responses.create", "openai"),
    ("beta.chat.completions.parse", "openai"),
    ("images.generate", "openai"),
    ("messages.create", "anthropic"),
    ("messages.stream", "anthropic"),
    ("generate_content_async", "gemini"),
    ("generate_content", "gemini"),
    ("generateContentStream", "gemini"),
    ("generateContent", "gemini"),
    ("models.generate_content", "gemini"),
    ("models.generateContent", "gemini"),
    ("chat.complete_async", "mistral"),
    ("chat.complete", "mistral"),
    ("text_generation", "huggingface"),
    ("chat_completion", "huggingface"),
    ("ollama.chat", "ollama"),
    ("ollama.generate", "ollama"),
)

_CONTEXT_INVOCATIONS: dict[str, tuple[str, ...]] = {
    "openai": ("chat.completions.create", "completions.create"),
    "groq": ("chat.completions.create", "completions.create"),
    "mistral": ("chat.completions.create",),
    "huggingface": ("generate",),
    "ollama": ("chat", "generate"),
    "langchain": ("invoke", "ainvoke", "stream", "astream", "batch", "abatch"),
    "llamaindex": ("query", "aquery", "complete", "acomplete", "as_query_engine"),
}

_MENTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai", re.compile(r"\bopenai\b|\bgpt-4\b|\bgpt-3\.5\b|\bchatgpt\b", re.I)),
    ("gemini", re.compile(r"\bgemini\b|\bvertex\s*ai\b|\bgoogle\s+generative\b", re.I)),
    ("anthropic", re.compile(r"\banthropic\b|\bclaude\b", re.I)),
    ("groq", re.compile(r"\bgroq\b", re.I)),
    ("mistral", re.compile(r"\bmistral\b|\bmixtral\b", re.I)),
    ("huggingface", re.compile(r"\bhugging\s*face\b|\bhuggingface\b", re.I)),
    ("ollama", re.compile(r"\bollama\b", re.I)),
    ("langchain", re.compile(r"\blangchain\b|\blanggraph\b", re.I)),
    ("llamaindex", re.compile(r"\bllama[\s-]?index\b", re.I)),
)

_MENTION_EXTS = (".md", ".mdx", ".rst", ".txt", ".html", ".htm")
_UI_EXTS = (".jsx", ".tsx", ".vue", ".svelte", ".html", ".htm")
_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build"}


@dataclass
class ProviderEvidence:
    provider: str
    level: int = LEVEL_NONE
    dependency: bool = False
    imported: bool = False
    client: bool = False
    invocation: bool = False
    packages: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def note(self, file: str | None, detail: str) -> None:
        if file and file not in self.files:
            self.files.append(file)
        if detail and detail not in self.details:
            self.details.append(detail)

    def raise_level(self, level: int) -> None:
        if level > self.level:
            self.level = level


@dataclass
class AiEvidence:
    integration_level: int = LEVEL_NONE
    providers: list[str] = field(default_factory=list)
    dependency_detected: bool = False
    import_detected: bool = False
    client_detected: bool = False
    model_invocation_detected: bool = False
    packages: list[str] = field(default_factory=list)
    imported_packages: list[str] = field(default_factory=list)
    evidence_files: list[str] = field(default_factory=list)
    by_provider: dict[str, ProviderEvidence] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return self.integration_level > LEVEL_NONE

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "providers": list(self.providers),
            "integration_level": self.integration_level,
            "dependency_detected": self.dependency_detected,
            "import_detected": self.import_detected,
            "client_detected": self.client_detected,
            "model_invocation_detected": self.model_invocation_detected,
            "evidence": self.evidence[:40],
        }


def detect_ai_evidence(
    source_facts: list[dict[str, Any]] | None = None,
    *,
    manifests: dict[str, str] | None = None,
    parsed_manifests: dict[str, Any] | None = None,
    file_contents: dict[str, str] | None = None,
    submission_context: dict[str, Any] | None = None,
) -> AiEvidence:
    """Build evidence levels from facts + manifests + mention-only text."""
    facts = list(source_facts or [])
    contents = file_contents or {}
    by_provider: dict[str, ProviderEvidence] = {}

    packages = _manifest_packages(manifests, parsed_manifests)
    for pkg in packages:
        for provider in _providers_from_package(pkg):
            ev = _ensure(by_provider, provider)
            ev.dependency = True
            ev.raise_level(LEVEL_DEPENDENCY)
            if pkg not in ev.packages:
                ev.packages.append(pkg)
            ev.note(None, f"dependency:{pkg}")

    file_imports = _collect_file_imports(facts, by_provider)
    _collect_clients(facts, file_imports, by_provider)
    _collect_invocations(facts, file_imports, by_provider)
    _collect_mentions(contents, submission_context, by_provider)

    imported_packages = sorted(
        {
            pkg
            for ev in by_provider.values()
            for pkg in ev.packages
            if ev.imported and pkg
        }
    )
    all_packages = sorted({pkg for ev in by_provider.values() for pkg in ev.packages} | set(packages))

    providers = [p for p in PROVIDER_ORDER if p in by_provider and by_provider[p].level > LEVEL_NONE]
    for extra in sorted(by_provider):
        if extra not in providers and by_provider[extra].level > LEVEL_NONE:
            providers.append(extra)

    level = max((ev.level for ev in by_provider.values()), default=LEVEL_NONE)
    evidence_rows = _public_evidence(by_provider)
    files: list[str] = []
    for ev in by_provider.values():
        for path in ev.files:
            if path not in files:
                files.append(path)

    return AiEvidence(
        integration_level=level,
        providers=providers,
        dependency_detected=any(ev.dependency for ev in by_provider.values()),
        import_detected=any(ev.imported for ev in by_provider.values()),
        client_detected=any(ev.client for ev in by_provider.values()),
        model_invocation_detected=any(ev.invocation for ev in by_provider.values()),
        packages=all_packages,
        imported_packages=imported_packages,
        evidence_files=files,
        by_provider=by_provider,
        evidence=evidence_rows,
    )


def _ensure(by_provider: dict[str, ProviderEvidence], provider: str) -> ProviderEvidence:
    if provider not in by_provider:
        by_provider[provider] = ProviderEvidence(provider=provider)
    return by_provider[provider]


def _manifest_packages(
    manifests: dict[str, str] | None,
    parsed_manifests: dict[str, Any] | None,
) -> list[str]:
    found: set[str] = set()
    if manifests:
        raw, _ = scan_manifests(manifests)
        found.update(raw)
    parsed = parsed_manifests or {}
    for key in ("npm", "python", "go"):
        for pkg in parsed.get(key) or []:
            name = str(pkg).lower()
            if name in AI_PACKAGES:
                found.add(name)
            found.update(_providers_from_package(name) and [name] if name in _PACKAGE_TO_PROVIDER else [])
    return sorted(found)


def _providers_from_package(pkg: str) -> tuple[str, ...]:
    key = pkg.lower()
    if key in _PACKAGE_TO_PROVIDER:
        return _PACKAGE_TO_PROVIDER[key]
    return _providers_from_module(key)


def _providers_from_module(module: str) -> tuple[str, ...]:
    text = (module or "").strip().strip("\"'").lower().replace("-", "_")
    raw = (module or "").strip().strip("\"'").lower()
    for prefix, providers in _IMPORT_PREFIXES:
        needle = prefix.lower()
        if raw == needle or raw.startswith(needle + "/") or raw.startswith(needle + "."):
            return providers
        if text == needle.replace("-", "_") or text.startswith(needle.replace("-", "_") + "."):
            return providers
    return ()


def _collect_file_imports(
    facts: list[dict[str, Any]],
    by_provider: dict[str, ProviderEvidence],
) -> dict[str, dict[str, Any]]:
    """file -> {providers, names, name_to_provider}."""
    per_file: dict[str, dict[str, Any]] = {}
    for item in facts:
        if item.get("fact_type") != "import":
            continue
        file = str(item.get("file") or "")
        module = str(item.get("module") or "")
        names = [str(n) for n in (item.get("names") or []) if n]
        alias = item.get("alias")
        providers = _providers_from_module(module)
        if not providers:
            continue
        slot = per_file.setdefault(
            file,
            {"providers": set(), "names": set(), "name_to_provider": {}},
        )
        slot["providers"].update(providers)
        for provider in providers:
            ev = _ensure(by_provider, provider)
            ev.imported = True
            ev.raise_level(LEVEL_SDK)
            pkg = _package_hint(module, provider)
            if pkg and pkg not in ev.packages:
                ev.packages.append(pkg)
            ev.note(file, f"import:{module}")
        for name in names:
            slot["names"].add(name)
            for provider in providers:
                slot["name_to_provider"].setdefault(name, provider)
        if alias:
            slot["names"].add(str(alias))
            if len(providers) == 1:
                slot["name_to_provider"].setdefault(str(alias), providers[0])
        if not item.get("is_from"):
            namespace = str(alias or module.split(".")[-1])
            if namespace:
                slot["names"].add(namespace)
                if len(providers) == 1:
                    slot["name_to_provider"].setdefault(namespace, providers[0])
    return per_file


def _package_hint(module: str, provider: str) -> str | None:
    raw = module.strip().strip("\"'").lower()
    for pkg, providers in _PACKAGE_TO_PROVIDER.items():
        if provider in providers and (raw == pkg or raw.startswith(pkg.replace("-", "_")) or raw.startswith(pkg)):
            return pkg
    return None


def _collect_clients(
    facts: list[dict[str, Any]],
    file_imports: dict[str, dict[str, Any]],
    by_provider: dict[str, ProviderEvidence],
) -> None:
    for item in facts:
        kind = item.get("fact_type")
        if kind not in {"assignment", "function_call"}:
            continue
        file = str(item.get("file") or "")
        token = str(item.get("value") if kind == "assignment" else item.get("callee") or "")
        ctor = _normalize_ctor(token)
        if not ctor:
            continue
        providers = _client_providers(ctor, file_imports.get(file) or {})
        if not providers:
            continue
        if kind == "function_call" and _is_invocation_callee(token):
            continue
        for provider in providers:
            ev = _ensure(by_provider, provider)
            ev.client = True
            ev.raise_level(LEVEL_SDK)
            ev.note(file, f"client:{ctor}")


def _collect_invocations(
    facts: list[dict[str, Any]],
    file_imports: dict[str, dict[str, Any]],
    by_provider: dict[str, ProviderEvidence],
) -> None:
    for item in facts:
        if item.get("fact_type") != "function_call":
            continue
        file = str(item.get("file") or "")
        callee = str(item.get("callee") or "")
        if not callee:
            continue
        imported = file_imports.get(file) or {"providers": set(), "name_to_provider": {}}
        providers = _invocation_providers(callee, imported)
        for provider in providers:
            ev = _ensure(by_provider, provider)
            ev.invocation = True
            ev.raise_level(LEVEL_INVOCATION)
            ev.note(file, f"invoke:{callee}")


def _collect_mentions(
    file_contents: dict[str, str],
    submission_context: dict[str, Any] | None,
    by_provider: dict[str, ProviderEvidence],
) -> None:
    for path, content in file_contents.items():
        if not content or not _is_mention_file(path):
            continue
        for provider, pattern in _MENTION_PATTERNS:
            if not pattern.search(content):
                continue
            ev = _ensure(by_provider, provider)
            if ev.level == LEVEL_NONE:
                ev.raise_level(LEVEL_MENTION)
            ev.note(path, "mention")
    context = submission_context or {}
    blob = " ".join(
        str(context.get(key) or "")
        for key in ("provided_context", "project_context", "description")
    )
    if blob.strip():
        for provider, pattern in _MENTION_PATTERNS:
            if pattern.search(blob):
                ev = _ensure(by_provider, provider)
                if ev.level == LEVEL_NONE:
                    ev.raise_level(LEVEL_MENTION)
                ev.note(None, "mention:context")


def _normalize_ctor(value: str) -> str:
    text = value.strip()
    if text.startswith("new "):
        text = text[4:].strip()
    text = text.split("(")[0].strip()
    return text


def _client_providers(ctor: str, imported: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    if ctor in _QUALIFIED_CLIENTS:
        found.add(_QUALIFIED_CLIENTS[ctor])
    leaf = ctor.split(".")[-1]
    imported_providers = set(imported.get("providers") or [])
    name_map: dict[str, str] = imported.get("name_to_provider") or {}
    if leaf in name_map:
        found.add(name_map[leaf])
    if ctor in name_map:
        found.add(name_map[ctor])
    for provider, names in _CLIENT_NAMES.items():
        if leaf not in names and ctor not in names:
            continue
        if provider in imported_providers or leaf in name_map:
            found.add(provider)
        elif ctor in _QUALIFIED_CLIENTS:
            found.add(_QUALIFIED_CLIENTS[ctor])
    # genai.Client without a too-generic bare Client
    if ctor.endswith(".Client") and "gemini" in imported_providers:
        found.add("gemini")
    if leaf == "Client" and imported_providers == {"gemini"}:
        found.add("gemini")
    if leaf == "Client" and imported_providers == {"ollama"}:
        found.add("ollama")
    return found


def _is_invocation_callee(callee: str) -> bool:
    return bool(_invocation_providers(callee, {"providers": set(), "name_to_provider": {}})) or any(
        _ends_with(callee, suffix) for suffixes in _CONTEXT_INVOCATIONS.values() for suffix in suffixes
    )


def _invocation_providers(callee: str, imported: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    imported_providers = set(imported.get("providers") or [])
    for suffix, provider in _SPECIFIC_INVOCATIONS:
        if _ends_with(callee, suffix):
            found.add(provider)
    for provider, suffixes in _CONTEXT_INVOCATIONS.items():
        if provider not in imported_providers:
            continue
        if any(_ends_with(callee, suffix) for suffix in suffixes):
            found.add(provider)
    return found


def _ends_with(callee: str, suffix: str) -> bool:
    return callee == suffix or callee.endswith("." + suffix)


def _is_mention_file(path: str) -> bool:
    norm = path.replace("\\", "/")
    parts = norm.lower().split("/")
    if any(part in _SKIP_DIRS for part in parts[:-1]):
        return False
    name = parts[-1]
    if name.startswith("readme"):
        return True
    if name.endswith(_MENTION_EXTS):
        return True
    if name.endswith(_UI_EXTS):
        return True
    return False


def _public_evidence(by_provider: dict[str, ProviderEvidence]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider in PROVIDER_ORDER:
        ev = by_provider.get(provider)
        if not ev or ev.level == LEVEL_NONE:
            continue
        kind = "mention"
        if ev.invocation:
            kind = "invocation"
        elif ev.client or ev.imported:
            kind = "sdk"
        elif ev.dependency:
            kind = "dependency"
        rows.append(
            {
                "provider": provider,
                "level": ev.level,
                "kind": kind,
                "files": ev.files[:8],
                "details": ev.details[:8],
            }
        )
    return rows


def llm_providers_from_evidence(evidence: AiEvidence) -> dict[str, Any]:
    """Scoring-compatible llm_providers block. Mentions-only is not uses_llm."""
    active = [
        p
        for p in evidence.providers
        if evidence.by_provider.get(p) and evidence.by_provider[p].level >= LEVEL_DEPENDENCY
    ]
    uses_llm = any(
        evidence.by_provider[p].level >= LEVEL_SDK and p in LLM_PROVIDERS
        for p in evidence.providers
        if p in evidence.by_provider
    ) or any(
        evidence.by_provider[p].invocation and p in {"langchain", "llamaindex"}
        for p in evidence.providers
        if p in evidence.by_provider
    )
    detected = []
    for provider in evidence.providers:
        ev = evidence.by_provider[provider]
        if ev.level < LEVEL_DEPENDENCY and not ev.imported:
            continue
        detected.append(
            {
                "provider": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "dependencies": list(ev.packages),
                "evidence_files": ev.files[:8],
                "model_hints": [],
                "level": ev.level,
            }
        )
    names = [row["label"] for row in detected]
    if uses_llm:
        reasoning = (
            f"AI evidence level {evidence.integration_level} — "
            + ", ".join(f"{row['label']} (L{row['level']})" for row in detected[:5])
        )
    elif evidence.integration_level == LEVEL_MENTION:
        reasoning = "AI mentioned in README/UI/context only; no SDK usage."
    elif evidence.dependency_detected:
        reasoning = "AI packages declared but no SDK import or invocation in source facts."
    else:
        reasoning = "No AI evidence in manifests, imports, or invocations."
    return {
        "uses_llm": uses_llm,
        "providers_detected": detected,
        "provider_names": names,
        "model_hints": [],
        "reasoning": reasoning,
        "integration_level": evidence.integration_level,
    }


def integration_type_from_evidence(evidence: AiEvidence) -> str:
    if evidence.integration_level <= LEVEL_MENTION:
        return "none"
    if evidence.integration_level == LEVEL_DEPENDENCY:
        return "none"
    imported_agents = [
        pkg
        for pkg in evidence.packages
        if is_agent_framework(pkg) and pkg in evidence.imported_packages
    ]
    if evidence.model_invocation_detected and imported_agents:
        return "agentic"
    if evidence.model_invocation_detected and any(
        AI_PACKAGES.get(pkg) == "vector_db" for pkg in evidence.packages
    ):
        return "rag"
    if evidence.import_detected or evidence.client_detected or evidence.model_invocation_detected:
        return "wrapper"
    return "none"


def confidence_from_level(level: int) -> str:
    if level in {LEVEL_NONE, LEVEL_MENTION, LEVEL_INVOCATION}:
        return "high"
    return "medium"
