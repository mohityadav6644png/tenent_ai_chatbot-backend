"""
Azure OpenAI & Fake SQL Generator (app/infrastructure/azure/openai_client.py)
SQLGenerator now generates a MongoDB query spec.
Generates structured MongoDB aggregation query specs, handles refusals, clarifications, and offline fallback.
"""

import json
from typing import Any, Dict, List, Optional, Protocol
from openai import AsyncAzureOpenAI
from app.core.config import settings
from app.core.exceptions import LlmUnavailableError
from app.core.logging import logger
from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator


class SQLGenerator(Protocol):
    async def generate(
        self, question: str, catalog: Dict[str, Any], history: List[Dict[str, str]], company_id: str
    ) -> Dict[str, Any]: ...


class AzureSQLGenerator:
    """Async Azure OpenAI generator producing structured MongoDB aggregation query specs."""

    def __init__(self):
        self.deployment = settings.AZURE_OPENAI_DEPLOYMENT
        if settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_KEY:
            self.client = AsyncAzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
                timeout=15.0,
                max_retries=2,
            )
        else:
            self.client = None

    async def generate(
        self, question: str, catalog: Dict[str, Any], history: List[Dict[str, str]], company_id: str
    ) -> Dict[str, Any]:
        if not self.client:
            raise LlmUnavailableError("Azure OpenAI client not configured.")
        try:
            sys_prompt = f"MongoDB Aggregation Catalog:\n{json.dumps(catalog)}\nGenerate valid MongoDB query specs with decision, query (collection, pipeline), templates."
            messages = [{"role": "system", "content": sys_prompt}]
            for h in history[-settings.HISTORY_TURNS:]:
                messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
            messages.append({"role": "user", "content": f"User question:\n{question}"})

            response = await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            if "query" in parsed and "sql" not in parsed:
                parsed["sql"] = json.dumps(parsed["query"])
            return parsed
        except Exception as exc:
            logger.error(f"Azure OpenAI call failed: {exc}")
            raise LlmUnavailableError("Failed to reach Azure OpenAI service.")


__all__ = ["SQLGenerator", "FakeSQLGenerator", "AzureSQLGenerator"]
