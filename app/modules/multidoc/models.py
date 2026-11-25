"""
Multi-document Domain Models (app/modules/multidoc/models.py)
Pydantic data models for multi-document synthesis and querying.
Pure Python - no motor, redis, google-genai, or fastapi imports.
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

MultiDocIntent = Literal["single_doc", "cross_doc_union", "cross_doc_join", "cross_doc_compare"]


class DocumentSummary(BaseModel):
    document_id: str
    source_name: str
    record_count: int
    fields: List[str] = Field(default_factory=list)


class MultiDocQueryPlan(BaseModel):
    intent: MultiDocIntent = "single_doc"
    target_document_ids: List[str] = Field(default_factory=list)
    join_keys: List[str] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    aggregations: List[Dict[str, Any]] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    sort_by: Optional[str] = None
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = 20
    requires_multi_doc: bool = False


class MultiDocAnswerMeta(BaseModel):
    used_multi_doc: bool = False
    document_ids: List[str] = Field(default_factory=list)
    source_names: List[str] = Field(default_factory=list)
    join_keys_used: List[str] = Field(default_factory=list)
    partial_coverage_note: Optional[str] = None
