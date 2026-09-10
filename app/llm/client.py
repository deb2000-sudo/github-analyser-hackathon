from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.config import Settings, get_settings
from app.llm.reasoner import LLMReasoner, ReasoningRequest


class LLMClient(LLMReasoner):
    """Vertex AI Gemini implementation of LLMReasoner — structured JSON only."""

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
                project=self.settings.vertex_project_id,
                location=self.settings.gcp_location,
                credentials=self.settings.vertex_credentials(),
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def reason(self, request: ReasoningRequest) -> dict[str, Any]:
        from app.pipeline.prompt import build_system_prompt, build_user_prompt

        system = build_system_prompt(request.metrics, questions=request.questions)
        user = build_user_prompt(
            metrics=request.metrics,
            evidence_pack=request.evidence,
            questions=request.questions,
            submission_context=request.submission_context,
        )
        return await self.judge_json(system=system, user=user)

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
