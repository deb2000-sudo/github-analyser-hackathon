"""Known AI / agent / vector-DB packages for static dependency scanning."""

# Canonical package name (lowercase) -> category
AI_PACKAGES: dict[str, str] = {
    # LLM providers / SDKs
    "openai": "llm_sdk",
    "anthropic": "llm_sdk",
    "google-generativeai": "llm_sdk",
    "google-genai": "llm_sdk",
    "cohere": "llm_sdk",
    "mistralai": "llm_sdk",
    "groq": "llm_sdk",
    "together": "llm_sdk",
    "fireworks-ai": "llm_sdk",
    "replicate": "llm_sdk",
    "huggingface-hub": "llm_sdk",
    "transformers": "ml",
    "sentence-transformers": "ml",
    "torch": "ml",
    "tensorflow": "ml",
    "instructor": "llm_sdk",
    "litellm": "llm_sdk",
    "@anthropic-ai/sdk": "llm_sdk",
    "ai": "llm_sdk",
    "@ai-sdk/openai": "llm_sdk",
    "@ai-sdk/anthropic": "llm_sdk",
    # Agent / orchestration frameworks
    "langchain": "agent_framework",
    "langchain-core": "agent_framework",
    "langchain-community": "agent_framework",
    "langchain-openai": "agent_framework",
    "langchain-anthropic": "agent_framework",
    "langgraph": "agent_framework",
    "langsmith": "agent_framework",
    "llama-index": "agent_framework",
    "llama_index": "agent_framework",
    "crewai": "agent_framework",
    "autogen": "agent_framework",
    "ag2": "agent_framework",
    "pyautogen": "agent_framework",
    "semantic-kernel": "agent_framework",
    "haystack-ai": "agent_framework",
    "haystack": "agent_framework",
    "dspy": "agent_framework",
    "dspy-ai": "agent_framework",
    "phidata": "agent_framework",
    "agno": "agent_framework",
    "smolagents": "agent_framework",
    "openai-agents": "agent_framework",
    "pydantic-ai": "agent_framework",
    "@langchain/core": "agent_framework",
    "@langchain/langgraph": "agent_framework",
    # Vector DBs / RAG infra
    "chromadb": "vector_db",
    "chroma": "vector_db",
    "pinecone": "vector_db",
    "pinecone-client": "vector_db",
    "weaviate-client": "vector_db",
    "weaviate": "vector_db",
    "qdrant-client": "vector_db",
    "faiss-cpu": "vector_db",
    "faiss-gpu": "vector_db",
    "pgvector": "vector_db",
    "pymilvus": "vector_db",
    "@pinecone-database/pinecone": "vector_db",
}

AGENT_FRAMEWORK_CATEGORIES = {"agent_framework"}


def is_agent_framework(package: str) -> bool:
    return AI_PACKAGES.get(package.lower()) in AGENT_FRAMEWORK_CATEGORIES
