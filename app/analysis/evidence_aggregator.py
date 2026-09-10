"""Phase 11: combine detector signals into conclusions.

Individual detectors do not determine final truth. Classification is derived
from atomic evidence IDs. Confidence is separate from rubric score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.ai_fake import (
    RULE_DISCARDED,
    RULE_FALLBACK,
    RULE_HARDCODED,
    RULE_POOL,
    RULE_TRANSFORM,
    RULE_UNUSED_DEP,
)

NO_AI = "NO_AI"
AI_MENTION_ONLY = "AI_MENTION_ONLY"
AI_DEPENDENCY_ONLY = "AI_DEPENDENCY_ONLY"
AI_CODE_PRESENT = "AI_CODE_PRESENT"
AI_INVOCATION_PRESENT = "AI_INVOCATION_PRESENT"
PARTIAL_AI_INTEGRATION = "PARTIAL_AI_INTEGRATION"
GENUINE_AI_INTEGRATION = "GENUINE_AI_INTEGRATION"
SUSPICIOUS_AI_IMPLEMENTATION = "SUSPICIOUS_AI_IMPLEMENTATION"

LEVEL_NONE = 0
LEVEL_MENTION = 1
LEVEL_DEPENDENCY = 2
LEVEL_CODE = 3
LEVEL_INVOCATION = 4
LEVEL_INPUT_FLOW = 5
LEVEL_OUTPUT_USED = 6

_FAKE_IMPL_RULES = frozenset({RULE_HARDCODED, RULE_POOL, RULE_TRANSFORM})
_BLOCKS_GENUINE = frozenset({RULE_DISCARDED, RULE_FALLBACK})


@dataclass
class EvidenceItem:
    id: str
    value: Any
    source: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"id": self.id, "value": self.value, "source": self.source}
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class Conclusion:
    id: str
    classification: str
    confidence: float
    evidence_ids: list[str]
    summary: str
    ai_level: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "classification": self.classification,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "summary": self.summary,
        }
        if self.ai_level is not None:
            data["ai_level"] = self.ai_level
        return data


@dataclass
class EvidenceAggregator:
    """Derive verdicts from atomic signals. Does not score and does not call Gemini."""

    items: dict[str, EvidenceItem] = field(default_factory=dict)

    def aggregate(
        self,
        *,
        metrics: dict[str, Any] | None = None,
        code_facts: Any | None = None,
    ) -> dict[str, Any]:
        self.items = {}
        metrics = metrics or {}
        self._collect_structure(code_facts)
        self._collect_fullstack(metrics.get("fullstack") or {})
        self._collect_connectivity(metrics.get("frontend_backend") or {})
        self._collect_ai(metrics.get("ai_usage") or {})

        app = self._conclude_application()
        conn = self._conclude_connectivity()
        ai = self._conclude_ai()
        conclusions = [app, conn, ai]
        return {
            "ai_level": ai.ai_level,
            "ai_classification": ai.classification,
            "confidence": ai.confidence,
            "conclusions": [c.to_dict() for c in conclusions],
            "evidence": [item.to_dict() for item in self.items.values()],
        }

    def _add(self, evidence_id: str, value: Any, source: str, detail: str | None = None) -> None:
        self.items[evidence_id] = EvidenceItem(
            id=evidence_id, value=value, source=source, detail=detail
        )

    def _flag(self, evidence_id: str) -> bool:
        item = self.items.get(evidence_id)
        return bool(item and item.value)

    def _collect_structure(self, code_facts: Any) -> None:
        structure = getattr(code_facts, "structure", None) or {}
        inventory = getattr(code_facts, "inventory", None) or {}
        if not isinstance(structure, dict):
            structure = {}
        if not isinstance(inventory, dict):
            inventory = {}
        layout = structure.get("layout") or inventory.get("layout")
        if layout:
            self._add("STR.LAYOUT", layout, "structure")
        if "is_monorepo" in structure or "is_monorepo" in inventory:
            self._add(
                "STR.MONOREPO",
                bool(structure.get("is_monorepo", inventory.get("is_monorepo"))),
                "structure",
            )
        if inventory.get("file_count") is not None:
            self._add("STR.FILE_COUNT", inventory.get("file_count"), "inventory")
        if structure.get("has_frontend_paths"):
            self._add("STR.FRONTEND_PATHS", True, "structure")
        if structure.get("has_backend_paths"):
            self._add("STR.BACKEND_PATHS", True, "structure")

    def _collect_fullstack(self, data: dict[str, Any]) -> None:
        frontend = data.get("frontend") if isinstance(data.get("frontend"), dict) else {}
        backend = data.get("backend") if isinstance(data.get("backend"), dict) else {}
        if frontend.get("detected"):
            self._add("FS.FRONTEND_DETECTED", True, "fullstack", frontend.get("framework"))
        if backend.get("detected"):
            self._add("FS.BACKEND_DETECTED", True, "fullstack", backend.get("framework"))

    def _collect_connectivity(self, data: dict[str, Any]) -> None:
        if "connected" not in data:
            return
        connected = data.get("connected")
        if connected is True:
            self._add("FB.CONNECTED", True, "frontend_backend")
        elif connected == "unknown":
            self._add("FB.UNKNOWN", True, "frontend_backend")
        elif connected is False:
            self._add("FB.DISCONNECTED", True, "frontend_backend")

    def _collect_ai(self, data: dict[str, Any]) -> None:
        invocation = bool(
            data.get("model_invocation_detected")
            or (data.get("ai_verification") or {}).get("model_invocation_detected")
        )
        dependency = bool(data.get("dependency_detected") or data.get("ai_dependencies_found"))
        imported = bool(data.get("import_detected"))
        client = bool(data.get("client_detected"))
        detected = bool(data.get("detected"))
        if detected and not dependency and not imported and not client and not invocation:
            self._add(
                "AI.MENTION",
                True,
                "ai_usage",
                ",".join(data.get("providers") or []),
            )
        if dependency:
            pkgs = data.get("ai_dependencies_found") or data.get("providers") or []
            self._add("AI.DEPENDENCY", True, "ai_usage", ",".join(str(p) for p in pkgs[:5]))
        if imported:
            self._add("AI.IMPORT", True, "ai_usage")
        if client:
            self._add("AI.CLIENT", True, "ai_usage")
        if invocation:
            self._add("AI.INVOCATION", True, "ai_usage")
        if data.get("user_input_reaches_model"):
            self._add("AI.INPUT_REACHES_MODEL", True, "ai_usage")
        if data.get("model_output_used"):
            self._add("AI.OUTPUT_USED", True, "ai_usage")
        elif invocation:
            self._add("AI.OUTPUT_NOT_USED", True, "ai_usage")
        for finding in data.get("ai_findings") or []:
            rule = str(finding.get("rule_id") or "")
            status = str(finding.get("status") or "")
            if rule:
                self._add(rule, status, "ai_fake", f"confidence={finding.get('confidence')}")

    def _conclude_application(self) -> Conclusion:
        fe = self._flag("FS.FRONTEND_DETECTED")
        be = self._flag("FS.BACKEND_DETECTED")
        ids = [eid for eid in ("FS.FRONTEND_DETECTED", "FS.BACKEND_DETECTED") if eid in self.items]
        layout_item = self.items.get("STR.LAYOUT")
        layout = layout_item.value if layout_item else None
        if layout:
            ids.append("STR.LAYOUT")
        if fe and be:
            extra = ["FB.CONNECTED"] if self._flag("FB.CONNECTED") else []
            return Conclusion(
                id="APPLICATION_TYPE",
                classification="full_stack",
                confidence=0.96 if extra else 0.9,
                evidence_ids=[*ids, *extra],
                summary="Frontend and backend stacks are both evidenced.",
            )
        if fe:
            support = ids or ([eid for eid in ("STR.FRONTEND_PATHS", "STR.LAYOUT") if eid in self.items])
            return Conclusion(
                id="APPLICATION_TYPE",
                classification="frontend",
                confidence=0.72 if layout == "fullstack" else 0.88,
                evidence_ids=support or ["STR.NO_STACK_SIGNALS"],
                summary="Frontend stack evidenced without a matching backend stack.",
            )
        if be:
            support = ids or ([eid for eid in ("STR.BACKEND_PATHS", "STR.LAYOUT") if eid in self.items])
            return Conclusion(
                id="APPLICATION_TYPE",
                classification="backend",
                confidence=0.72 if layout == "fullstack" else 0.88,
                evidence_ids=support or ["STR.NO_STACK_SIGNALS"],
                summary="Backend stack evidenced without a matching frontend stack.",
            )
        fallback = [eid for eid in ("STR.LAYOUT", "STR.FILE_COUNT") if eid in self.items]
        classification = str(layout or "unknown")
        if classification not in {"frontend", "backend", "library", "cli", "unknown"}:
            classification = "unknown"
        if not fallback:
            self._add("STR.NO_STACK_SIGNALS", True, "structure")
            fallback = ["STR.NO_STACK_SIGNALS"]
        return Conclusion(
            id="APPLICATION_TYPE",
            classification=classification,
            confidence=0.4 if layout else 0.3,
            evidence_ids=fallback,
            summary="No deterministic frontend/backend stack evidence.",
        )

    def _conclude_connectivity(self) -> Conclusion:
        fe = self._flag("FS.FRONTEND_DETECTED")
        be = self._flag("FS.BACKEND_DETECTED")
        if not (fe and be):
            ids = [eid for eid in ("FS.FRONTEND_DETECTED", "FS.BACKEND_DETECTED") if eid in self.items]
            if not ids:
                self._add("FB.NOT_APPLICABLE", True, "frontend_backend")
                ids = ["FB.NOT_APPLICABLE"]
            return Conclusion(
                id="FRONTEND_BACKEND",
                classification="not_applicable",
                confidence=0.9,
                evidence_ids=ids,
                summary="Frontend↔backend matching requires both sides.",
            )
        if self._flag("FB.CONNECTED"):
            return Conclusion(
                id="FRONTEND_BACKEND",
                classification="connected",
                confidence=0.95,
                evidence_ids=["FB.CONNECTED", "FS.FRONTEND_DETECTED", "FS.BACKEND_DETECTED"],
                summary="Frontend requests match backend routes.",
            )
        if self._flag("FB.UNKNOWN"):
            return Conclusion(
                id="FRONTEND_BACKEND",
                classification="unknown",
                confidence=0.55,
                evidence_ids=["FB.UNKNOWN", "FS.FRONTEND_DETECTED", "FS.BACKEND_DETECTED"],
                summary="Connectivity cannot be proven from static URLs.",
            )
        if self._flag("FB.DISCONNECTED"):
            return Conclusion(
                id="FRONTEND_BACKEND",
                classification="disconnected",
                confidence=0.86,
                evidence_ids=["FB.DISCONNECTED", "FS.FRONTEND_DETECTED", "FS.BACKEND_DETECTED"],
                summary="Frontend and backend HTTP paths do not match.",
            )
        return Conclusion(
            id="FRONTEND_BACKEND",
            classification="unknown",
            confidence=0.4,
            evidence_ids=["FS.FRONTEND_DETECTED", "FS.BACKEND_DETECTED"],
            summary="Both stacks exist but connectivity was not evaluated.",
        )

    def _conclude_ai(self) -> Conclusion:
        invocation = self._flag("AI.INVOCATION")
        input_reaches = self._flag("AI.INPUT_REACHES_MODEL")
        output_used = self._flag("AI.OUTPUT_USED")
        failed_rules = {
            eid
            for eid, item in self.items.items()
            if item.source == "ai_fake" and item.value == "failed"
        }
        blocks_genuine = bool(failed_rules & _BLOCKS_GENUINE)
        fake_impl = bool(failed_rules & _FAKE_IMPL_RULES)

        if invocation and input_reaches and output_used and not blocks_genuine:
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=GENUINE_AI_INTEGRATION,
                ai_level=LEVEL_OUTPUT_USED,
                confidence=0.95,
                evidence_ids=["AI.INVOCATION", "AI.INPUT_REACHES_MODEL", "AI.OUTPUT_USED"],
                summary="User data reaches a model invocation and the output is used.",
            )

        if fake_impl and not invocation:
            ids = sorted(failed_rules & _FAKE_IMPL_RULES)
            if self._flag("AI.MENTION"):
                ids.append("AI.MENTION")
            if self._flag("AI.DEPENDENCY"):
                ids.append("AI.DEPENDENCY")
            level = LEVEL_NONE
            if self._flag("AI.DEPENDENCY"):
                level = LEVEL_DEPENDENCY
            elif self._flag("AI.MENTION"):
                level = LEVEL_MENTION
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=SUSPICIOUS_AI_IMPLEMENTATION,
                ai_level=level,
                confidence=0.9,
                evidence_ids=ids,
                summary="AI-looking behavior is canned or transformed without a model invocation.",
            )

        if invocation and (input_reaches or output_used):
            ids = ["AI.INVOCATION"]
            level = LEVEL_INVOCATION
            if input_reaches:
                ids.append("AI.INPUT_REACHES_MODEL")
                level = LEVEL_INPUT_FLOW
            if output_used:
                ids.append("AI.OUTPUT_USED")
            elif "AI.OUTPUT_NOT_USED" in self.items:
                ids.append("AI.OUTPUT_NOT_USED")
            ids.extend(sorted(failed_rules & {RULE_DISCARDED, RULE_FALLBACK}))
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=PARTIAL_AI_INTEGRATION,
                ai_level=level,
                confidence=0.86,
                evidence_ids=ids,
                summary="Model invocation exists but input flow and output use are not both proven.",
            )

        if invocation:
            ids = ["AI.INVOCATION"]
            ids.extend(sorted(failed_rules))
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=AI_INVOCATION_PRESENT,
                ai_level=LEVEL_INVOCATION,
                confidence=0.82,
                evidence_ids=ids,
                summary="A model invocation exists without proven application data flow.",
            )

        if self._flag("AI.IMPORT") or self._flag("AI.CLIENT"):
            ids = [eid for eid in ("AI.IMPORT", "AI.CLIENT", "AI.DEPENDENCY") if eid in self.items]
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=AI_CODE_PRESENT,
                ai_level=LEVEL_CODE,
                confidence=0.8,
                evidence_ids=ids,
                summary="AI SDK import or client exists without a proven invocation.",
            )

        if self._flag("AI.DEPENDENCY"):
            ids = ["AI.DEPENDENCY"]
            if RULE_UNUSED_DEP in self.items:
                ids.append(RULE_UNUSED_DEP)
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=AI_DEPENDENCY_ONLY,
                ai_level=LEVEL_DEPENDENCY,
                confidence=0.78,
                evidence_ids=ids,
                summary="An AI package is declared without invocation evidence.",
            )

        if self._flag("AI.MENTION"):
            return Conclusion(
                id="AI_CLASSIFICATION",
                classification=AI_MENTION_ONLY,
                ai_level=LEVEL_MENTION,
                confidence=0.7,
                evidence_ids=["AI.MENTION"],
                summary="AI is mentioned without dependency, SDK code, or invocation.",
            )

        self._add("AI.NONE", True, "ai_usage")
        return Conclusion(
            id="AI_CLASSIFICATION",
            classification=NO_AI,
            ai_level=LEVEL_NONE,
            confidence=0.9,
            evidence_ids=["AI.NONE"],
            summary="No AI mention, dependency, code, or invocation was proven.",
        )
