"""Whether the detected frontend communicates with the detected backend."""

from __future__ import annotations

from typing import Any

from app.analysis.facts import build_code_facts
from app.analysis.http_connect import match_frontend_backend
from app.analysis.http_io import detect_http_io
from app.metrics.base import Metric, MetricContext, MetricResult


class FrontendBackendMetric(Metric):
    name = "frontend_backend"
    tier = "static"
    description = (
        "Matches frontend HTTP clients to backend routes using method + normalized path. "
        "Dynamic unresolved URLs are unknown, not disconnected."
    )
    depends_on = []
    output_schema = {
        "type": "object",
        "properties": {
            "connected": {"type": ["boolean", "string"]},
            "connections": {"type": "array"},
            "backend_routes": {"type": "array"},
            "frontend_requests": {"type": "array"},
            "unmatched_frontend": {"type": "array"},
            "unmatched_backend": {"type": "array"},
            "method_mismatches": {"type": "array"},
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        facts = ctx.extras.get("code_facts")
        if facts is None:
            facts = build_code_facts(ctx.snapshot)
        source_facts = getattr(facts, "source_facts", None) or []
        endpoints = detect_http_io(list(source_facts))
        matched = match_frontend_backend(
            endpoints["backend_routes"],
            endpoints["frontend_requests"],
        )
        data: dict[str, Any] = {
            "connected": matched["connected"],
            "connections": matched["connections"],
            "backend_routes": endpoints["backend_routes"],
            "frontend_requests": endpoints["frontend_requests"],
            "unmatched_frontend": matched["unmatched_frontend"],
            "unmatched_backend": matched["unmatched_backend"],
            "method_mismatches": matched["method_mismatches"],
        }
        return MetricResult(name=self.name, status="ok", data=data)
