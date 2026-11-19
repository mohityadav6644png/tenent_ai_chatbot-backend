"""
Chat Models (app/modules/chat/models.py)
Document model definitions for conversations, messages, queries, and feedback in MongoDB.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional
from bson import ObjectId


def create_conversation_document(
    company_id: str,
    user_id: str,
    title: str,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "company_id": str(company_id),
        "user_id": str(user_id),
        "title": title[:100],
        "created_at": now,
        "updated_at": now,
    }


def create_message_document(
    conversation_id: str,
    company_id: str,
    user_id: str,
    question: str,
    status: str,
    answer: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "conversation_id": str(conversation_id),
        "company_id": str(company_id),
        "user_id": str(user_id),
        "question": question,
        "status": status,
        "answer": answer or {},
        "meta": meta or {},
        "created_at": now,
    }


def create_generated_query_document(
    company_id: str,
    question: str,
    query: Dict[str, Any],
    sql: Optional[str] = None,
) -> dict:
    """Generated query model with 'sql' (JSON text) and 'query' (dict)."""
    return {
        "company_id": str(company_id),
        "question": question,
        "query": query,
        "sql": sql or json.dumps(query),
        "created_at": datetime.now(timezone.utc),
    }


def create_feedback_document(
    message_id: str,
    user_id: str,
    helpful: bool,
) -> dict:
    return {
        "_id": ObjectId(),
        "message_id": str(message_id),
        "user_id": str(user_id),
        "helpful": helpful,
        "created_at": datetime.now(timezone.utc),
    }
