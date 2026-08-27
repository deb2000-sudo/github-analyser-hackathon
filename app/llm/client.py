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

            # Explicit ADC (gcloud user locally / Cloud Run SA in prod).
            # Do not omit credentials — google-auth may otherwise pick up the
            # Firebase Admin SA from the process and Vertex returns 403.
            self._client = genai.Client(
                vertexai=True,
                project=self.settings.resolved_project_id,
                location=self.settings.gcp_location,
                credentials=self.settings.vertex_credentials(),
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def judge_json(self, *, system: str, user: str) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError(
                "Vertex AI is not configured (set GOOGLE_CLOUD_PROJECT / GCP_PROJECT_ID and enable Vertex AI)"
            )
        return await asyncio.to_thread(self._judge_sync, system, user)

    def _judge_sync(self, system: str, user: str) -> dict[str, Any]:
        from google.genai import types

        assert self._client is not None
        chat = self._client.chats.create(
            model=self.settings.vertex_model,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                system_instruction=system,
            ),
        )
        resp = chat.send_message(user)
        content = (resp.text or "").strip() or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                return json.loads(match.group(0))
            raise
