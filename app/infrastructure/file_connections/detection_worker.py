"""
Relationship Detection Background Worker (app/infrastructure/file_connections/detection_worker.py)
Orchestrates background connection scanning, time budgets, auto-connection, and re-validation.
"""

import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.file_connections.candidate_collector import collect_file_candidates
from app.modules.file_connections.models import compute_connection_signature, create_connection_document


class ConnectionDetectionWorker:
    def __init__(self, conn_repo: Any, files_repo: Any, enrichment_worker: Any, llm_reviewer: Any, app_db: Any = None):
        self.conn_repo = conn_repo
        self.files_repo = files_repo
        self.enrichment_worker = enrichment_worker
        self.llm_reviewer = llm_reviewer
        self.app_db = app_db
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, company_id: str) -> asyncio.Lock:
        if company_id not in self._locks:
            self._locks[company_id] = asyncio.Lock()
        return self._locks[company_id]

    async def get_scan_state(self, company_id: str) -> Dict[str, Any]:
        if self.app_db is not None:
            doc = await self.app_db["file_scan_state"].find_one({"company_id": str(company_id)})
            if doc:
                return {
                    "status": doc.get("status", "idle"),
                    "last_scan_at": doc.get("last_scan_at"),
                    "counts": doc.get("counts", {"created": 0, "suggested": 0}),
                }
        return {"status": "idle", "last_scan_at": None, "counts": {"created": 0, "suggested": 0}}

    async def scan_company_connections(self, company_id: str, trigger: str = "auto") -> Dict[str, Any]:
        if settings.AUTO_CONNECT_MODE == "off":
            return {"status": "skipped", "message": "AUTO_CONNECT_MODE is off"}

        lock = self._get_lock(company_id)
        if lock.locked():
            return {"status": "already_running"}

        async with lock:
            t0 = time.monotonic()
            now = datetime.now(timezone.utc)
            if self.app_db is not None:
                await self.app_db["file_scan_state"].update_one(
                    {"company_id": str(company_id)},
                    {"$set": {"company_id": str(company_id), "status": "running", "updated_at": now}},
                    upsert=True,
                )

            created_count = 0
            suggested_count = 0
            try:
                ready_files = await self.files_repo.list_files(company_id)
                ready_files = [f for f in ready_files if f.get("status") == "ready"]
                if len(ready_files) < 2:
                    if self.app_db is not None:
                        await self.app_db["file_scan_state"].update_one(
                            {"company_id": str(company_id)},
                            {"$set": {"status": "idle", "last_scan_at": now, "counts": {"created": 0, "suggested": 0}}},
                        )
                    return {"status": "done", "counts": {"created": 0, "suggested": 0}}

                existing_conns = await self.conn_repo.list_connections(company_id)
                existing_signatures = {c.get("signature") for c in existing_conns if c.get("signature")}
                for c in existing_conns:
                    sig = compute_connection_signature(c["left_file_id"], c["left_key"], c["right_file_id"], c["right_key"])
                    existing_signatures.add(sig)

                candidates = await collect_file_candidates(company_id, ready_files, self.conn_repo, t0)

                for left_file, right_file, l_col, r_col, cand in candidates:
                    sig = compute_connection_signature(cand["left_file_id"], cand["left_key"], cand["right_file_id"], cand["right_key"])
                    if sig in existing_signatures:
                        continue

                    if settings.AUTO_CONNECT_LLM_REVIEW and 0.60 <= cand["confidence"] < 0.85:
                        review = await self.llm_reviewer.review_candidate(
                            left_file.get("name", ""), left_file.get("description", ""),
                            cand["left_key"], l_col.get("type", "text"), l_col.get("meaning"),
                            right_file.get("name", ""), right_file.get("description", ""),
                            cand["right_key"], r_col.get("type", "text"), r_col.get("meaning"),
                        )
                        if review:
                            cand["evidence"]["llm_review"] = review
                            if review.get("related") and review.get("confidence", 0) >= 0.8:
                                cand["evidence"]["reason_codes"].append("ai_reviewed")
                                if cand["evidence"]["containment"] >= 0.95:
                                    cand["confidence_label"] = "high"
                                    cand["confidence"] = max(cand["confidence"], settings.AUTO_CONNECT_HIGH_CONFIDENCE)

                    is_auto_high = (settings.AUTO_CONNECT_MODE == "auto_high" and cand["confidence_label"] == "high")
                    status = "refreshing" if is_auto_high else "suggested"
                    alias = f"{right_file.get('slug', 'right')}_link"

                    doc = create_connection_document(
                        company_id=company_id,
                        name=f"{cand['left_file_name']} -> {cand['right_file_name']}",
                        left_file_id=cand["left_file_id"],
                        left_key=cand["left_key"],
                        right_file_id=cand["right_file_id"],
                        right_key=cand["right_key"],
                        alias=alias,
                        cardinality=cand["cardinality"],
                        status=status,
                        match_rate=cand["evidence"]["row_match_rate"],
                        origin="auto",
                        confidence=cand["confidence"],
                        confidence_label=cand["confidence_label"],
                        review_needed=is_auto_high,
                        normalization_level=cand["normalization_level"],
                        evidence=cand["evidence"],
                        signature=sig,
                    )
                    conn_id = await self.conn_repo.create_connection(doc)
                    existing_signatures.add(sig)

                    if is_auto_high:
                        created_count += 1
                        asyncio.create_task(self.enrichment_worker.run_enrichment(company_id, str(conn_id)))
                    else:
                        suggested_count += 1

                if self.app_db is not None:
                    await self.app_db["file_scan_state"].update_one(
                        {"company_id": str(company_id)},
                        {"$set": {"status": "completed", "last_scan_at": datetime.now(timezone.utc), "counts": {"created": created_count, "suggested": suggested_count}}},
                        upsert=True,
                    )

            except Exception as exc:
                logger.error(f"Error during connection scanning: {exc}")
                if self.app_db is not None:
                    await self.app_db["file_scan_state"].update_one(
                        {"company_id": str(company_id)},
                        {"$set": {"status": "failed", "updated_at": datetime.now(timezone.utc)}},
                    )
                return {"status": "failed", "error": str(exc)}

            return {"status": "done", "counts": {"created": created_count, "suggested": suggested_count}}
