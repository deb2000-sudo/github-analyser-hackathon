"""Whether the detected frontend communicates with the detected backend."""

from __future__ import annotations

from typing import Any

from app.analysis.context import get_analysis
from app.analysis.http_connect import match_frontend_backend
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
        analysis = get_analysis(ctx)
        routes = list(analysis.routes)
        requests = list(analysis.http_calls)
        matched = match_frontend_backend(routes, requests)
        data: dict[str, Any] = {
            "connected": matched["connected"],
            "connections": matched["connections"],
            "backend_routes": routes,
            "frontend_requests": requests,
            "unmatched_frontend": matched["unmatched_frontend"],
            "unmatched_backend": matched["unmatched_backend"],
            "method_mismatches": matched["method_mismatches"],
        }
        return MetricResult(name=self.name, status="ok", data=data)
