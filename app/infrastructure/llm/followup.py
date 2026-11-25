"""
OpenAI-Compatible Follow-up Resolver (app/infrastructure/llm/followup.py)
Follow-up resolver using OpenAI client against generic OpenAI-compatible endpoints.
"""

import json
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.exceptions import LlmUnavailableError
from app.modules.chat.interfaces import FollowUpResolver
from app.infrastructure.azure.followup import SYSTEM_PROMPT


class OpenAICompatFollowUpResolver(FollowUpResolver):
    def __init__(self):
        self.api_key = settings.OPENAI_COMPAT_API_KEY.get_secret_value() if hasattr(settings.OPENAI_COMPAT_API_KEY, "get_secret_value") else str(settings.OPENAI_COMPAT_API_KEY or "")
        self.base_url = settings.OPENAI_COMPAT_BASE_URL
        self.model = settings.OPENAI_COMPAT_MODEL

        if not self.api_key or not self.base_url or not self.model:
            self.client = None
        else:
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=settings.FOLLOWUP_TIMEOUT_SECONDS,
                max_retries=1,
            )

    async def resolve(
        self,
        new_message: str,
        turns: List[Dict[str, Any]],
        catalog: Dict[str, Any],
        company_id: str,
    ) -> Dict[str, Any]:
        if not self.client:
            raise LlmUnavailableError("OpenAI-compatible client not configured.")

        user_prompt = {
            "conversation_history": [
                {
                    "turn_index": idx + 1,
                    "resolved_question": t.get("resolved_question", ""),
                    "file_slug": t.get("file_slug"),
                    "output_columns": t.get("output_columns", []),
                }
                for idx, t in enumerate(turns)
            ],
            "untrusted_new_message": new_message,
        }

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_prompt)},
        ]

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
