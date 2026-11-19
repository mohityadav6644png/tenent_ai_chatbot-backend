"""
Record Search Repository (app/infrastructure/db/record_search_repo.py)
Provides safe text search over company_records with strict company_id scoping.
"""

from typing import Any, Dict, List
from app.core.config import settings
from app.infrastructure.db.mongodb import company_records_collection
from app.modules.chat.interfaces import RecordSearchRepository


class MongoRecordSearchRepository(RecordSearchRepository):
    """Searches company_records using MongoDB text indexes under tenant isolation."""

    def __init__(self, collection=company_records_collection):
        self.collection = collection

    async def search_records(
        self, company_id: str, query: str, limit: int = 3, min_score: float = 0.2
    ) -> List[Dict[str, Any]]:
        if not company_id or not query:
            return []

        max_snippet = settings.TEXT_SEARCH_SNIPPET_CHARS
        pipeline = [
            {
                "$match": {
                    "company_id": str(company_id),
                    "$text": {"$search": query},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "title": 1,
                    "content": 1,
                    "score": {"$meta": "textScore"},
                }
            },
            {"$match": {"score": {"$gte": min_score}}},
            {"$sort": {"score": -1}},
            {"$limit": limit},
        ]

        try:
            cursor = await self.collection.aggregate(pipeline)
            docs = await cursor.to_list(limit)
            results = []
            for doc in docs:
                results.append({
                    "title": doc.get("title", ""),
                    "snippet": (doc.get("content") or "")[:max_snippet],
                })
            return results
        except Exception:
            return []
