"""
File Connection Enrichment Worker (app/infrastructure/file_connections/enrichment_worker.py)
Background worker streaming right rows, batching left rows, and writing joined.<alias>.
"""

import asyncio
from typing import Any, Dict, Optional, Set
from app.core.logging import logger
from app.modules.file_connections.interfaces import FileConnectionsRepoProtocol
from app.modules.company_files.interfaces import AuditServiceProtocol, CacheProtocol, CompanyFilesRepoProtocol, TenantSchemaProtocol
from app.modules.file_connections.validation import evaluate_right_key_uniqueness, normalize_key_value


class EnrichmentWorker:
    def __init__(
        self,
        conn_repo: FileConnectionsRepoProtocol,
        files_repo: CompanyFilesRepoProtocol,
        tenant_repo: TenantSchemaProtocol,
        cache: CacheProtocol,
        audit: Optional[AuditServiceProtocol] = None,
    ):
        self.conn_repo = conn_repo
        self.files_repo = files_repo
        self.tenant_repo = tenant_repo
        self.cache = cache
        self.audit = audit
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, conn_id: str) -> asyncio.Lock:
        if conn_id not in self._locks:
            self._locks[conn_id] = asyncio.Lock()
        return self._locks[conn_id]

    async def run_enrichment(self, company_id: str, connection_id: str) -> bool:
        lock = self._get_lock(connection_id)
        async with lock:
            conn = await self.conn_repo.get_connection_by_id(company_id, connection_id)
            if not conn:
                return False

            alias = conn.get("alias")
            left_file_id = conn.get("left_file_id")
            right_file_id = conn.get("right_file_id")
            left_key = conn.get("left_key")
            right_key = conn.get("right_key")

            # Check files exist and are ready
            left_file = await self.files_repo.get_file_by_id(company_id, left_file_id)
            right_file = await self.files_repo.get_file_by_id(company_id, right_file_id)
            if not left_file or not right_file or left_file.get("status") != "ready" or right_file.get("status") != "ready":
                await self.conn_repo.update_connection(company_id, connection_id, {"status": "needs_attention"})
                await self.conn_repo.unset_joined_alias(company_id, left_file_id, alias)
                return False

            # Check right key type and hidden keys
            right_cols = {c["key"]: c for c in right_file.get("columns", [])}
            left_cols = {c["key"]: c for c in left_file.get("columns", [])}
            if left_key not in left_cols or right_key not in right_cols or left_cols[left_key].get("hidden") or right_cols[right_key].get("hidden"):
                await self.conn_repo.update_connection(company_id, connection_id, {"status": "needs_attention"})
                await self.conn_repo.unset_joined_alias(company_id, left_file_id, alias)
                return False

            right_col_type = right_cols[right_key].get("type", "text")
            left_col_type = left_cols[left_key].get("type", "text")
            hidden_right_keys: Set[str] = {c["key"] for c in right_file.get("columns", []) if c.get("hidden")}
            norm_level = int(conn.get("normalization_level", 0))

            # Stream right values to verify uniqueness
            raw_right_vals = await self.conn_repo.stream_raw_column_values(company_id, right_file_id, right_key)
            is_unique, _, _ = evaluate_right_key_uniqueness(raw_right_vals, right_col_type, level=norm_level)
            if not is_unique:
                await self.conn_repo.update_connection(company_id, connection_id, {"status": "needs_attention"})
                await self.conn_repo.unset_joined_alias(company_id, left_file_id, alias)
                return False

            # Stream right rows into normalized map (excluding hidden fields)
            right_map = await self.conn_repo.stream_right_rows_dict(
                company_id, right_file_id, right_key, right_col_type, hidden_right_keys, level=norm_level
            )

            # Stream left file rows and bulk update in batches of 1,000
            total_left = 0
            matched_left = 0
            distinct_left_matched: Set[Any] = set()
            distinct_left_all: Set[Any] = set()

            async for batch in self.conn_repo.stream_left_rows_batch(company_id, left_file_id, batch_size=1000):
                set_ops = []
                unset_ids = []
                for row in batch:
                    total_left += 1
                    raw_val = row.get("data", {}).get(left_key)
                    norm = normalize_key_value(raw_val, left_col_type, level=norm_level)
                    if norm is not None:
                        distinct_left_all.add(norm)
                        if norm in right_map:
                            matched_left += 1
                            distinct_left_matched.add(norm)
                            set_ops.append({"_id": row["_id"], "data": right_map[norm]})
                        else:
                            unset_ids.append(row["_id"])
                    else:
                        unset_ids.append(row["_id"])

                await self.conn_repo.bulk_update_joined(company_id, left_file_id, set_ops, unset_ids, alias)

            right_rows = len(raw_right_vals)
            match_rate = len(distinct_left_matched) / len(distinct_left_all) if distinct_left_all else 0.0

            # Update connection status to active
            await self.conn_repo.update_connection(
                company_id,
                connection_id,
                {
                    "status": "active",
                    "matched_rows": matched_left,
                    "left_rows": total_left,
                    "right_rows": right_rows,
                    "match_rate": round(match_rate, 4),
                },
            )

            # Post maintenance: bump schema_version, clear tenant cache, log audit
            await self.tenant_repo.bump_schema_version(company_id)
            await self.cache.clear_tenant(company_id)
            try:
                from app.core.cache import invalidate_company_cache
                await invalidate_company_cache(company_id)
            except Exception:
                pass

            if self.audit:
                try:
                    await self.audit.record_event(
                        actor={"id": "system", "role": "system", "company_id": company_id},
                        action="file_connection.enriched",
                        target=str(connection_id),
                        result="success",
                    )
                except Exception:
                    pass
            return True

    async def on_file_deleted(self, company_id: str, file_id: str) -> None:
        conns = await self.conn_repo.get_connections_by_file(company_id, file_id)
        for c in conns:
            conn_id = str(c["_id"])
            await self.conn_repo.update_connection(company_id, conn_id, {"status": "needs_attention"})
            if c.get("left_file_id") != file_id:
                await self.conn_repo.unset_joined_alias(company_id, c["left_file_id"], c.get("alias", ""))

    async def on_file_updated(self, company_id: str, file_id: str) -> None:
        conns = await self.conn_repo.get_connections_by_file(company_id, file_id)
        for c in conns:
            if c.get("status") in ("active", "refreshing"):
                await self.conn_repo.update_connection(company_id, str(c["_id"]), {"status": "refreshing"})
                asyncio.create_task(self.run_enrichment(company_id, str(c["_id"])))
