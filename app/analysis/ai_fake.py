"""Phase 10: independent rules for AI-looking implementations that do not use AI.

Does not invent control flow. Hardcoded system prompts are not hardcoded responses.
Naming such as mock_ai is suspicious evidence only and never proves fake AI by itself.
"""

from __future__ import annotations

import re
from typing import Any

from app.analysis.ai_evidence import AiEvidence, list_model_invocations
from app.analysis.call_graph import CallGraph
from app.analysis.data_flow import extract_refs
from app.analysis.source_models import MODULE_SCOPE

RULE_HARDCODED = "AI.HARDCODED_RESPONSE"
RULE_POOL = "AI.STATIC_RESPONSE_POOL"
RULE_TRANSFORM = "AI.DETERMINISTIC_TEXT_TRANSFORMATION"
RULE_UNUSED_DEP = "AI.UNUSED_AI_DEPENDENCY"
RULE_MOCK = "AI.MOCK_AI_IMPLEMENTATION"
RULE_DISCARDED = "AI.AI_OUTPUT_DISCARDED"
RULE_FALLBACK = "AI.FAKE_FALLBACK_DOMINATES"

_AI_NAME_RE = re.compile(
    r"(chat|assistant|mentor|llm|openai|gemini|anthropic|generate|completion|ask_ai|\bai\b)",
    re.I,
)
_MOCK_RE = re.compile(
    r"mock_ai|fake_ai|dummy_llm|mock_openai|fake_llm|dummy_ai|fake_openai|stub_llm|stub_ai",
    re.I,
)
_FALLBACK_RE = re.compile(r"^(FALLBACK|DEFAULT|CANNED|STATIC).*$|^(fallback|default_answer|canned_reply)$", re.I)
_POOL_CALLEES = (
    "random.choice",
    "random.sample",
    "np.random.choice",
    "numpy.random.choice",
    "choice",
)
_TRANSFORM_LEAVES = frozenset(
    {
        "upper",
        "lower",
        "title",
        "capitalize",
        "casefold",
        "swapcase",
        "strip",
        "lstrip",
        "rstrip",
        "replace",
        "toUpperCase",
        "toLowerCase",
        "toLocaleUpperCase",
        "toLocaleLowerCase",
        "trim",
    }
)
_SOURCE_ROOTS = frozenset(
    {"request", "body", "req", "payload", "form", "message", "prompt", "query", "data", "input"}
)


def detect_fake_ai(
    source_facts: list[dict[str, Any]],
    *,
    evidence: AiEvidence,
    verification: dict[str, Any] | None = None,
    call_graph: CallGraph | None = None,
    manifests: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return independent findings. Only proven fakes are status=failed."""
    del call_graph
    facts = list(source_facts or [])
    verification = verification or {}
    ctx = _context(facts, evidence, verification)
    findings: list[dict[str, Any]] = []
    findings.extend(_rule_hardcoded(ctx))
    findings.extend(_rule_pool(ctx))
    findings.extend(_rule_transform(ctx))
    findings.extend(_rule_unused_dep(ctx, evidence, manifests))
    findings.extend(_rule_mock(ctx, evidence, verification))
    findings.extend(_rule_discarded(ctx))
    findings.extend(_rule_fallback(ctx))
    return findings


def _finding(rule_id: str, status: str, confidence: float, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "status": status,
        "confidence": confidence,
        "evidence": evidence[:12],
    }


def _hit(file: str, line: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"file": file or "<unknown>"}
    if isinstance(line, int):
        item["line"] = line
    elif line is not None:
        try:
            item["line"] = int(line)
        except (TypeError, ValueError):
            item["line"] = 1
    else:
        item["line"] = 1
    return item


def _context(
    facts: list[dict[str, Any]],
    evidence: AiEvidence,
    verification: dict[str, Any],
) -> dict[str, Any]:
    functions = [f for f in facts if f.get("fact_type") == "function"]
    assigns = [f for f in facts if f.get("fact_type") == "assignment"]
    calls = [f for f in facts if f.get("fact_type") == "function_call"]
    returns = [f for f in facts if f.get("fact_type") == "return"]
    routes = [f for f in facts if f.get("fact_type") == "route"]
    invocations = list_model_invocations(facts)
    handlers = {(str(r.get("file") or ""), str(r.get("handler") or "")) for r in routes if r.get("handler")}
    producer_names = {str(item.get("scope") or "") for item in invocations if item.get("scope")}
    producer_funcs = {(str(item.get("file") or ""), str(item.get("scope") or "")) for item in invocations}
    consumer_funcs: set[tuple[str, str]] = set(producer_funcs)
    for call in calls:
        leaf = str(call.get("callee") or "").split(".")[-1]
        if leaf in producer_names:
            consumer_funcs.add((str(call.get("file") or ""), str(call.get("scope") or "")))
    endpoints = _ai_endpoints(functions, routes, handlers)
    literal_names = _literal_names(assigns)
    pool_names = _pool_names(assigns)
    return {
        "facts": facts,
        "functions": functions,
        "assigns": assigns,
        "calls": calls,
        "returns": returns,
        "routes": routes,
        "invocations": invocations,
        "handlers": handlers,
        "producer_names": producer_names,
        "producer_funcs": producer_funcs,
        "consumer_funcs": consumer_funcs,
        "endpoints": endpoints,
        "literal_names": literal_names,
        "pool_names": pool_names,
        "ai_claimed": bool(evidence.detected or endpoints),
        "invocation": bool(invocations or verification.get("model_invocation_detected")),
        "output_used": bool(verification.get("model_output_used")),
    }


def _ai_endpoints(
    functions: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    handlers: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in routes:
        file = str(route.get("file") or "")
        handler = str(route.get("handler") or "")
        path = str(route.get("path") or "")
        if handler and (_AI_NAME_RE.search(handler) or _AI_NAME_RE.search(path)):
            found.add((file, handler))
        elif handler and (file, handler) in handlers and _AI_NAME_RE.search(file):
            found.add((file, handler))
    for fn in functions:
        file = str(fn.get("file") or "")
        name = str(fn.get("name") or "")
        if _AI_NAME_RE.search(name) or _MOCK_RE.search(name):
            found.add((file, name))
    return found


def _literal_names(assigns: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    names: set[tuple[str, str, str]] = set()
    for item in assigns:
        value = item.get("value")
        if _is_static_text(value) or _is_string_pool(value):
            names.add((str(item.get("file") or ""), str(item.get("scope") or ""), str(item.get("target") or "")))
    return names


def _pool_names(assigns: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in assigns:
        if _is_string_pool(item.get("value")):
            key = (str(item.get("file") or ""), str(item.get("scope") or ""), str(item.get("target") or ""))
            found[key] = item
    return found


def _is_static_text(value: str | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    refs = [ref for ref in extract_refs(text) if not _is_ignored_ref(ref)]
    if refs:
        return False
    if _looks_like_number(text):
        return False
    if text in {"True", "False", "None", "true", "false", "null", "undefined", "{}", "[]"}:
        return False
    return True


def _is_string_pool(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if not (text.startswith("[") or text.startswith("(")):
        return False
    strings = re.findall(r"(['\"])(?:\\.|.)+?\1", text)
    if len(strings) >= 2:
        return True
    parts = [p.strip() for p in re.split(r"[,\n]", text.strip("[]()")) if p.strip()]
    return len(parts) >= 2 and all(_is_static_text(p.strip("\"'")) or (p.startswith(("'", '"')) ) for p in parts)


def _is_ignored_ref(ref: str) -> bool:
    return ref in {"True", "False", "None", "true", "false", "null", "undefined"}


def _looks_like_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _return_refs(value: str | None, file: str, scope: str, ctx: dict[str, Any]) -> list[str]:
    refs = [ref for ref in extract_refs(value) if not _is_ignored_ref(ref)]
    if value and not refs and str(value).isidentifier():
        refs = [str(value)]
    return refs


def _refs_are_literals(refs: list[str], file: str, scope: str, ctx: dict[str, Any]) -> bool:
    literals = ctx["literal_names"]
    if not refs:
        return True
    for ref in refs:
        root = ref.split(".")[0]
        if (file, scope, root) in literals or (file, MODULE_SCOPE, root) in literals:
            continue
        return False
    return True


def _uses_ai_output(value: str | None, file: str, scope: str, ctx: dict[str, Any]) -> bool:
    refs = _return_refs(value, file, scope, ctx)
    tokens = set(ctx["producer_names"])
    for assign in ctx["assigns"]:
        if assign.get("file") != file or assign.get("scope") != scope:
            continue
        raw = str(assign.get("value") or "").split(".")[-1]
        if raw in ctx["producer_names"] or str(assign.get("value") or "") in {
            str(i.get("callee") or "") for i in ctx["invocations"]
        }:
            tokens.add(str(assign.get("target") or ""))
    for call in ctx["calls"]:
        if call.get("file") != file or call.get("scope") != scope:
            continue
        leaf = str(call.get("callee") or "").split(".")[-1]
        if leaf in ctx["producer_names"]:
            tokens.add(leaf)
            tokens.add(str(call.get("callee") or ""))
    if not refs:
        return False
    for ref in refs:
        leaf = ref.split(".")[-1]
        if ref in tokens or leaf in tokens:
            return True
    return False


def _is_transform(value: str | None) -> bool:
    if not value:
        return False
    text = str(value)
    leaf = text.split("(")[0].split(".")[-1]
    if leaf in _TRANSFORM_LEAVES:
        return True
    if any(text.endswith("." + name) or f".{name}" in text for name in _TRANSFORM_LEAVES):
        return True
    has_plus = "+" in text or "${" in text or (text.startswith("f") and "{" in text)
    has_source = any(root in extract_refs(text) or text.startswith(root + ".") or f".{root}" in text for root in _SOURCE_ROOTS)
    has_string = bool(re.search(r"['\"]", text)) or (not extract_refs(text) and " " in text and any(k in text.lower() for k in ("improved", "answer", "prefix")))
    if has_plus and has_source:
        return True
    if has_string and has_source and ("+" in text or text.startswith("f")):
        return True
    return False


def _is_fallback_value(value: str | None, file: str, scope: str, ctx: dict[str, Any]) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if _FALLBACK_RE.match(text.split(".")[-1]):
        return True
    refs = _return_refs(text, file, scope, ctx)
    for ref in refs:
        root = ref.split(".")[0]
        if _FALLBACK_RE.match(root):
            return True
        if (file, scope, root) in ctx["literal_names"] or (file, MODULE_SCOPE, root) in ctx["literal_names"]:
            if _FALLBACK_RE.match(root):
                return True
    return False


def _consider_return(ret: dict[str, Any], ctx: dict[str, Any]) -> bool:
    file = str(ret.get("file") or "")
    scope = str(ret.get("scope") or "")
    if (file, scope) in ctx["endpoints"]:
        return True
    if (file, scope) in ctx["handlers"] and ctx["ai_claimed"]:
        return _AI_NAME_RE.search(scope) is not None or (file, scope) in ctx["endpoints"]
    return False


def _rule_hardcoded(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for ret in ctx["returns"]:
        if not _consider_return(ret, ctx):
            continue
        if _uses_ai_output(ret.get("value"), str(ret.get("file") or ""), str(ret.get("scope") or ""), ctx):
            continue
        value = ret.get("value")
        file = str(ret.get("file") or "")
        scope = str(ret.get("scope") or "")
        refs = _return_refs(value, file, scope, ctx)
        static = _is_static_text(value) or (refs and _refs_are_literals(refs, file, scope, ctx) and not _is_transform(value))
        if not static:
            continue
        if _is_transform(value):
            continue
        hits.append(_hit(file, ret.get("line")))
    if not hits:
        return []
    return [_finding(RULE_HARDCODED, "failed", 0.97, hits)]


def _rule_pool(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    pool_vars = {name for (_f, _s, name) in ctx["pool_names"]}
    for call in ctx["calls"]:
        callee = str(call.get("callee") or "")
        file = str(call.get("file") or "")
        scope = str(call.get("scope") or "")
        if (file, scope) not in ctx["endpoints"] and (file, scope) not in ctx["handlers"]:
            continue
        if not any(callee == name or callee.endswith("." + name) for name in _POOL_CALLEES):
            continue
        args = [str(a) for a in (call.get("arguments") or [])]
        if not any(arg.split(".")[-1] in pool_vars or _is_string_pool(arg) for arg in args):
            if not pool_vars:
                continue
            if not any(arg.split(".")[-1] in pool_vars for arg in args):
                continue
        # The choice must be returned (or assigned then returned) without AI output.
        if _endpoint_returns_ai(file, scope, ctx):
            continue
        hits.append(_hit(file, call.get("line")))
    for ret in ctx["returns"]:
        if not _consider_return(ret, ctx):
            continue
        if _uses_ai_output(ret.get("value"), str(ret.get("file") or ""), str(ret.get("scope") or ""), ctx):
            continue
        value = str(ret.get("value") or "")
        leaf = value.split(".")[0].split("[")[0]
        key = (str(ret.get("file") or ""), str(ret.get("scope") or ""), leaf)
        mod_key = (str(ret.get("file") or ""), MODULE_SCOPE, leaf)
        if key in ctx["pool_names"] or mod_key in ctx["pool_names"] or "[" in value and leaf in {n for *_rest, n in ctx["pool_names"]}:
            hits.append(_hit(str(ret.get("file") or ""), ret.get("line")))
    if not hits:
        return []
    return [_finding(RULE_POOL, "failed", 0.95, hits)]


def _endpoint_returns_ai(file: str, scope: str, ctx: dict[str, Any]) -> bool:
    for ret in ctx["returns"]:
        if ret.get("file") == file and ret.get("scope") == scope:
            if _uses_ai_output(ret.get("value"), file, scope, ctx):
                return True
    return False


def _rule_transform(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for ret in ctx["returns"]:
        if not _consider_return(ret, ctx):
            continue
        file = str(ret.get("file") or "")
        scope = str(ret.get("scope") or "")
        if _uses_ai_output(ret.get("value"), file, scope, ctx):
            continue
        value = ret.get("value")
        if not _is_transform(value):
            continue
        hits.append(_hit(file, ret.get("line")))
    if not hits:
        return []
    return [_finding(RULE_TRANSFORM, "failed", 0.9, hits)]


def _rule_unused_dep(
    ctx: dict[str, Any],
    evidence: AiEvidence,
    manifests: dict[str, str] | None,
) -> list[dict[str, Any]]:
    if not evidence.dependency_detected:
        return []
    if ctx["invocation"] or evidence.model_invocation_detected:
        return []
    hits: list[dict[str, Any]] = []
    for path in (manifests or {}):
        hits.append(_hit(path, 1))
    if not hits:
        hits.append(_hit("requirements.txt" if evidence.packages else "<manifest>", 1))
    return [_finding(RULE_UNUSED_DEP, "failed", 0.93, hits)]


def _rule_mock(
    ctx: dict[str, Any],
    evidence: AiEvidence,
    verification: dict[str, Any],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for fn in ctx["functions"]:
        name = str(fn.get("name") or "")
        file = str(fn.get("file") or "")
        if _MOCK_RE.search(name) or _MOCK_RE.search(file):
            hits.append(_hit(file, fn.get("line")))
    for item in ctx["assigns"]:
        target = str(item.get("target") or "")
        if _MOCK_RE.search(target):
            hits.append(_hit(str(item.get("file") or ""), item.get("line")))
    if not hits:
        return []
    genuine = bool(
        (ctx["invocation"] or evidence.model_invocation_detected)
        and (verification.get("model_output_used") or _any_return_uses_ai(ctx))
    )
    if genuine:
        return []
    # Suspicious evidence only — never a failed proof by naming.
    return [_finding(RULE_MOCK, "warning", 0.55, hits)]


def _any_return_uses_ai(ctx: dict[str, Any]) -> bool:
    for ret in ctx["returns"]:
        if _uses_ai_output(ret.get("value"), str(ret.get("file") or ""), str(ret.get("scope") or ""), ctx):
            return True
    return False


def _rule_discarded(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    if not ctx["invocation"] and not ctx["consumer_funcs"]:
        return []
    hits: list[dict[str, Any]] = []
    for ret in ctx["returns"]:
        file = str(ret.get("file") or "")
        scope = str(ret.get("scope") or "")
        if (file, scope) not in ctx["endpoints"] and (file, scope) not in ctx["handlers"]:
            continue
        if (file, scope) in ctx["producer_funcs"]:
            continue
        if not _function_calls_ai(file, scope, ctx):
            continue
        if _uses_ai_output(ret.get("value"), file, scope, ctx):
            continue
        hits.append(_hit(file, ret.get("line")))
    if not hits:
        return []
    return [_finding(RULE_DISCARDED, "failed", 0.94, hits)]


def _function_calls_ai(file: str, scope: str, ctx: dict[str, Any]) -> bool:
    if (file, scope) in ctx["consumer_funcs"]:
        return True
    for call in ctx["calls"]:
        if call.get("file") != file or call.get("scope") != scope:
            continue
        leaf = str(call.get("callee") or "").split(".")[-1]
        if leaf in ctx["producer_names"]:
            return True
        if any(
            str(inv.get("callee") or "") == str(call.get("callee") or "")
            for inv in ctx["invocations"]
        ):
            return True
    return False


def _rule_fallback(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for ret in ctx["returns"]:
        file = str(ret.get("file") or "")
        scope = str(ret.get("scope") or "")
        if (file, scope) not in ctx["endpoints"] and (file, scope) not in ctx["handlers"]:
            continue
        if (file, scope) in ctx["producer_funcs"]:
            continue
        if not _function_calls_ai(file, scope, ctx):
            continue
        if _endpoint_returns_ai(file, scope, ctx):
            continue
        if not _is_fallback_value(ret.get("value"), file, scope, ctx):
            continue
        hits.append(_hit(file, ret.get("line")))
    if not hits:
        return []
    return [_finding(RULE_FALLBACK, "failed", 0.9, hits)]
