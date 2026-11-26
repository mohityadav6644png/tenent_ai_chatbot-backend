"""
Preview Relationship Detector (app/infrastructure/file_connections/preview_detector.py)
Computes potential connections against existing ready files without saving anything.
Pure read-only operation during file preview.
"""

from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.modules.file_connections.detection import evaluate_candidate_pair


async def detect_connections_for_preview(
    company_id: str,
    preview_file_name: str,
    preview_columns: List[Dict[str, Any]],
    raw_rows: List[List[str]],
    files_repo: Any,
    conn_repo: Any,
) -> List[Dict[str, Any]]:
    """Evaluates candidate connections for preview file against ready files without persisting."""
    ready_files = await files_repo.list_files(company_id)
    ready_files = [f for f in ready_files if f.get("status") == "ready"]
    if not ready_files:
        return []

    preview_vals_by_col = {}
    for idx, c in enumerate(preview_columns):
        if c.get("suggested_hidden") or c.get("hidden") or c.get("type") in ("date", "boolean"):
            continue
        vals = [r[idx].strip() for r in raw_rows if idx < len(r) and r[idx].strip()]
        preview_vals_by_col[c["key"]] = vals

    mock_preview_file = {
        "_id": "preview_file",
        "name": preview_file_name,
        "slug": preview_file_name.lower().replace(" ", "_"),
        "columns": preview_columns,
    }

    results: List[Dict[str, Any]] = []

    for rf in ready_files:
        rf_id = str(rf.get("_id") or rf.get("id"))
        rf_cols = [
            c for c in rf.get("columns", [])
            if not c.get("hidden") and c.get("type") not in ("date", "boolean")
        ]
        for rc in rf_cols:
            rk = rc["key"]
            r_vals = await conn_repo.stream_raw_column_values(company_id, rf_id, rk)
            if not r_vals:
                continue

            for pc in preview_columns:
                pk = pc["key"]
                p_vals = preview_vals_by_col.get(pk, [])
                if not p_vals or pc.get("type") != rc.get("type"):
                    continue

                # Case 1: Preview file is left (many), Existing file is right (one)
                cand1 = evaluate_candidate_pair(
                    mock_preview_file, rf, pc, rc, p_vals, r_vals,
                    high_threshold=settings.AUTO_CONNECT_HIGH_CONFIDENCE,
                    min_containment=settings.AUTO_CONNECT_MIN_CONTAINMENT,
                    suggest_threshold=settings.AUTO_CONNECT_SUGGEST_CONFIDENCE,
                )
                if cand1:
                    results.append({
                        "other_file_id": rf_id,
                        "other_file_name": rf.get("name", ""),
                        "left_file_name": preview_file_name,
                        "left_key": pk,
                        "right_file_name": rf.get("name", ""),
                        "right_key": rk,
                        "confidence": cand1["confidence"],
                        "label": cand1["confidence_label"],
                        "row_match_rate": cand1["evidence"]["row_match_rate"],
                        "reason_codes": cand1["evidence"]["reason_codes"],
                    })

                # Case 2: Existing file is left (many), Preview file is right (one)
                cand2 = evaluate_candidate_pair(
                    rf, mock_preview_file, rc, pc, r_vals, p_vals,
                    high_threshold=settings.AUTO_CONNECT_HIGH_CONFIDENCE,
                    min_containment=settings.AUTO_CONNECT_MIN_CONTAINMENT,
                    suggest_threshold=settings.AUTO_CONNECT_SUGGEST_CONFIDENCE,
                )
                if cand2:
                    results.append({
                        "other_file_id": rf_id,
                        "other_file_name": rf.get("name", ""),
                        "left_file_name": rf.get("name", ""),
                        "left_key": rk,
                        "right_file_name": preview_file_name,
                        "right_key": pk,
                        "confidence": cand2["confidence"],
                        "label": cand2["confidence_label"],
                        "row_match_rate": cand2["evidence"]["row_match_rate"],
                        "reason_codes": cand2["evidence"]["reason_codes"],
                    })

    # Sort by confidence descending, take top 5
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:5]
