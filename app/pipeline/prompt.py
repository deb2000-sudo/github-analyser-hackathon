from __future__ import annotations

from typing import Any


AI_USAGE_SECTION = '''
"ai_usage": {
  "ai_integration_type": "none" | "wrapper" | "rag" | "agentic",
  "llm_providers_used": [string],
  "confidence": "low" | "medium" | "high",
  "evidence_files": [string],
  "reasoning": string
}
'''.strip()

AGENT_ANALYSIS_SECTION = '''
"agent_analysis": {
  "agent_count": int,
  "agents": [ { "role_guess": string, "file": string, "evidence": string } ],
  "has_real_orchestration": bool,
  "confidence": "low" | "medium" | "high",
  "reasoning": string
}
'''.strip()

SOLUTION_FIT_SECTION = '''
"solution_fit": {
  "context_relevant": bool,    // false if repo is a different product/domain than PROJECT CONTEXT
  "relevance_score": number,   // 0-10 how relevant the repo is to PROJECT CONTEXT (0 = unrelated)
  "alignment_score": number,   // 0-10 how well code implements PROJECT CONTEXT (must be 0 if context_relevant is false)
  "implements_claimed_solution": bool,  // true only if repo actually builds what PROJECT CONTEXT describes
  "context_requirements_met": [ { "requirement": string, "met": bool, "evidence": string } ],
  "readme_quality_score": number,
  "readme_has_local_setup": bool,
  "readme_reasoning": string,
  "gaps": [string],
  "strengths": [string],
  "confidence": "low" | "medium" | "high",
  "reasoning": string
}
'''.strip()

SECTION_TEMPLATES = {
    "ai_usage": AI_USAGE_SECTION,
    "agent_analysis": AGENT_ANALYSIS_SECTION,
    "solution_fit": SOLUTION_FIT_SECTION,
}


def format_submission_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    parts = ["=== PROJECT CONTEXT ==="]
    if context.get("track"):
        parts.append(f"Track: {context['track']}")
    parts.append((context.get("provided_context") or "").strip())
    if context.get("rubrics"):
        parts.append("Judging rubrics / must-haves:")
        for r in context["rubrics"]:
            parts.append(f"- {r}")
    extra = context.get("extra") or {}
    if extra:
        parts.append(f"Additional organizer context: {extra}")
    parts.append("=== END PROJECT CONTEXT ===")
    return "\n".join(parts)


def build_system_prompt(metrics: list[str]) -> str:
    """Render only the schema sections needed for the selected LLM metrics."""
    sections = [SECTION_TEMPLATES[m] for m in metrics if m in SECTION_TEMPLATES]
    if not sections:
        raise ValueError("No LLM sections requested for prompt")

    joined = ",\n".join(sections)
    return f"""You are a hackathon submission evaluator.
You will receive: (1) a project context paragraph describing what the submission should do,
(2) curated source files from the team's GitHub repo (not the full repo).

Evaluate the code against that project context. Only report what is directly
evidenced in the provided code — do not invent features, files, or functions.

Return ONLY valid JSON matching this schema. Omit sections not requested.

{{
{joined}
}}

Rules:
- Ground every claim in the provided files and/or the submission context.
- Never invent files or functions not present in the provided context.
- If evidence is ambiguous, use "confidence": "low" rather than guessing high.
- Distinguish a framework being imported from a framework being meaningfully used.
- For ai_usage: list every LLM provider evidenced in code (OpenAI, Anthropic, Google Gemini, Groq, Mistral, Cohere, etc.) in llm_providers_used.
- Only claim a dependency or agent framework if it appears in verified_ai_dependencies or code_evidence_by_package hints — never infer LangChain from unrelated @scope/core packages.
- Frontend files that only call a backend /api for AI scoring are "wrapper" — not agentic — unless agent orchestration code is present.
- has_real_orchestration is true only if there is actual handoff/planning/tool-routing logic,
  not just multiple classes named "Agent".
- For solution_fit — STRICT rules:
  1. Read ONLY the PROJECT CONTEXT paragraph (ignore any "Judging rubrics" list — those are scored elsewhere).
  2. Ask: "Is this repository actually building the product described in PROJECT CONTEXT?"
     If it is a different product/domain (e.g. monitoring tool vs study planner), set context_relevant=false,
     relevance_score=0, alignment_score=0, implements_claimed_solution=false.
  3. context_requirements_met: extract 3-5 concrete requirements FROM PROJECT CONTEXT only, then check each against code.
  4. alignment_score measures code implementation of PROJECT CONTEXT — NOT README quality, NOT generic code quality.
  5. Do not give alignment_score above 2 unless the repo's stated purpose in README/code matches PROJECT CONTEXT domain.
  6. README fields are separate — do not inflate alignment_score because README is well written.
"""


def build_user_prompt(
    *,
    metrics: list[str],
    files: dict[str, str],
    hints: dict[str, Any] | None = None,
    submission_context: dict[str, Any] | None = None,
) -> str:
    parts = [
        f"Requested metric sections: {', '.join(metrics)}",
    ]
    ctx_block = format_submission_context(submission_context)
    if ctx_block:
        parts.append(ctx_block)
    else:
        parts.append(
            "(No project context provided — judge only from code evidence.)"
        )
    if hints:
        parts.append(f"Static pre-check hints: {hints}")
    parts.append("Curated files:\n")
    for path, content in files.items():
        parts.append(f"===== FILE: {path} =====\n{content}\n")
    return "\n".join(parts)
