"""
Answer Writer Implementation (app/infrastructure/llm/answer_writer.py)
Generates natural language summaries of query results using LLM.
"""

import asyncio
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.logging import logger
from app.modules.chat.answer_writer import sanitize_for_writer
from app.modules.chat.interfaces import AnswerWriterProtocol


class LlmAnswerWriter(AnswerWriterProtocol):
    """Summarizes query results in natural language with strict timeout and sanitization."""

    async def write_answer(
        self, question: str, columns: List[Dict[str, str]], rows: List[List[Any]]
    ) -> Optional[str]:
        if not settings.ANSWER_WRITER_ENABLED:
            return None
        if not rows:
            return None

        safe_cols, safe_rows = sanitize_for_writer(
            columns, rows, max_rows=settings.ANSWER_WRITER_MAX_ROWS
        )
        if not safe_cols or not safe_rows:
            return None

        prompt = (
            f"Question: {question}\n"
            f"Columns: {', '.join(safe_cols)}\n"
            f"Data (up to {len(safe_rows)} rows): {safe_rows}\n\n"
            "Provide a concise, direct 1-2 sentence plain text summary of this data answering the user's question. "
            "Do not mention row IDs, MongoDB, or system internals."
        )

        try:
            return await asyncio.wait_for(self._call_llm(prompt), timeout=5.0)
        except Exception as exc:
            logger.info(f"Answer writer skipped or timed out: {type(exc).__name__}")
            return None

    async def _call_llm(self, prompt: str) -> Optional[str]:
        provider = settings.LLM_PROVIDER.lower()
        if provider == "gemini":
            from app.infrastructure.gemini.client import get_gemini_client
            client = get_gemini_client()
            res = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
            )
            from app.core.metrics import extract_and_record_tokens
            extract_and_record_tokens(res, "answer_writer", settings.GEMINI_MODEL or "gemini-3.5-flash-lite")
            return res.text.strip() if res and res.text else None
        elif provider == "openai_compat":
            from app.infrastructure.llm.openai_compat_client import get_openai_compat_client
            client = get_openai_compat_client()
            resp = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.2,
            )
            return resp.choices[0].message.content.strip() if resp.choices else None
        return None
