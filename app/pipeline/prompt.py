from __future__ import annotations

from typing import Any


AI_USAGE_SECTION = '''
"ai_usage": {
  "ai_integration_type": "none" | "wrapper" | "rag" | "agentic",
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
  "alignment_score": number,   // 0-10 how well the code addresses the problem statement
  "implements_claimed_solution": bool,
  "requirements_met": [ { "requirement": string, "met": bool, "evidence": string } ],
  "gaps": [string],            // missing pieces relative to problem / claimed solution
  "strengths": [string],
  "confidence": "low" | "medium" | "high",
  "reasoning": string          // 2-4 sentences citing concrete files/behaviors
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
    parts = ["=== HACKATHON SUBMISSION CONTEXT ==="]
    if context.get("team_name"):
        parts.append(f"Team: {context['team_name']}")
    if context.get("track"):
        parts.append(f"Track: {context['track']}")
    parts.append(f"Problem statement:\n{context.get('problem_statement', '').strip()}")
    if context.get("solution_description"):
        parts.append(f"Claimed solution:\n{str(context['solution_description']).strip()}")
    if context.get("rubrics"):
        parts.append("Judging rubrics / must-haves:")
        for r in context["rubrics"]:
            parts.append(f"- {r}")
    extra = context.get("extra") or {}
    if extra:
        parts.append(f"Additional organizer context: {extra}")
    parts.append("=== END CONTEXT ===")
    return "\n".join(parts)


def build_system_prompt(metrics: list[str]) -> str:
    """Render only the schema sections needed for the selected LLM metrics."""
    sections = [SECTION_TEMPLATES[m] for m in metrics if m in SECTION_TEMPLATES]
    if not sections:
        raise ValueError("No LLM sections requested for prompt")

    joined = ",\n".join(sections)
    return f"""You are a hackathon submission evaluator.
You will receive: (1) the problem statement and claimed solution context,
(2) curated source files from the team's GitHub repo (not the full repo).

Evaluate the code against the submission context. Only report what is directly
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
- has_real_orchestration is true only if there is actual handoff/planning/tool-routing logic,
  not just multiple classes named "Agent".
- For solution_fit: score alignment to the problem statement; call out gaps honestly;
  do not reward README marketing that is unsupported by code.
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
            "(No problem/solution context provided — judge only from code evidence.)"
        )
    if hints:
        parts.append(f"Static pre-check hints: {hints}")
    parts.append("Curated files:\n")
    for path, content in files.items():
        parts.append(f"===== FILE: {path} =====\n{content}\n")
    return "\n".join(parts)
