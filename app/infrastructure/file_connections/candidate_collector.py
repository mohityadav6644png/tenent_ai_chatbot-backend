"""
Candidate Collector (app/infrastructure/file_connections/candidate_collector.py)
Streams column values and evaluates candidate connections across ready files.
"""

import time
from typing import Any, Dict, List, Tuple
from app.core.config import settings
from app.core.logging import logger
from app.modules.file_connections.detection import evaluate_candidate_pair


async def collect_file_candidates(
    company_id: str,
    ready_files: List[Dict[str, Any]],
    conn_repo: Any,
    start_time: float,
) -> List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]]:
    file_vals_map: Dict[str, Dict[str, List[Any]]] = {}
    for rf in ready_files:
        rf_id = str(rf.get("_id") or rf.get("id"))
        file_vals_map[rf_id] = {}
        for col in rf.get("columns", []):
            if not col.get("hidden") and col.get("type") not in ("date", "boolean"):
                vals = await conn_repo.stream_raw_column_values(company_id, rf_id, col["key"])
                file_vals_map[rf_id][col["key"]] = vals

    candidates = []
    for i, left_file in enumerate(ready_files):
        l_id = str(left_file.get("_id") or left_file.get("id"))
        for j, right_file in enumerate(ready_files):
            if i == j:
                continue
            r_id = str(right_file.get("_id") or right_file.get("id"))
            for l_col in left_file.get("columns", []):
                if l_col.get("hidden") or l_col.get("type") in ("date", "boolean"):
                    continue
                for r_col in right_file.get("columns", []):
                    if r_col.get("hidden") or r_col.get("type") in ("date", "boolean"):
                        continue
                    if l_col.get("type") != r_col.get("type"):
                        continue
                    if time.monotonic() - start_time > settings.AUTO_CONNECT_TIME_BUDGET_SECONDS:
                        logger.warning("Auto-connect scan time budget reached.")
                        return candidates

                    l_vals = file_vals_map.get(l_id, {}).get(l_col["key"], [])
                    r_vals = file_vals_map.get(r_id, {}).get(r_col["key"], [])
                    cand = evaluate_candidate_pair(
                        left_file, right_file, l_col, r_col, l_vals, r_vals,
                        high_threshold=settings.AUTO_CONNECT_HIGH_CONFIDENCE,
                        min_containment=settings.AUTO_CONNECT_MIN_CONTAINMENT,
                        suggest_threshold=settings.AUTO_CONNECT_SUGGEST_CONFIDENCE,
                    )
                    if cand:
                        candidates.append((left_file, right_file, l_col, r_col, cand))
    return candidates
