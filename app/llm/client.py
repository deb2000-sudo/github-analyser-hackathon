from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.config import Settings, get_settings


class LLMClient:
    """Vertex AI Gemini judge — returns structured JSON."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None
        if self.settings.llm_enabled:
            from google import genai

            self._client = genai.Client(
                vertexai=True,
                project=self.settings.gcp_project_id,
                location=self.settings.gcp_location,
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def judge_json(self, *, system: str, user: str) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError(
                "Vertex AI is not configured (set GCP_PROJECT_ID and enable Vertex AI)"
            )
        return await asyncio.to_thread(self._judge_sync, system, user)

    def _judge_sync(self, system: str, user: str) -> dict[str, Any]:
        from google.genai import types

        assert self._client is not None
        resp = self._client.models.generate_content(
            model=self.settings.vertex_model,
            contents=user,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                system_instruction=system,
            ),
        )
        content = (resp.text or "").strip() or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                return json.loads(match.group(0))
            raise
