"""
Query Plan Models (app/modules/chat/query_plan.py)
Pydantic v2 data models for structured query plans produced by the AI.
Uses fixed property types with JSON-encoded strings for values to support strict schema generation across providers.
"""

from typing import Any, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class QueryPlanFilter(BaseModel):
    field: str = Field(..., description="Field name in collection")
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "between", "contains"] = Field(
        ..., description="Comparison or containment operator"
    )
    value: str = Field(
        ..., description="JSON-encoded filter value or array (e.g. '\"completed\"', '100', '[10, 50]')"
    )


class QueryPlanMetric(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fn: Literal["sum", "avg", "min", "max", "count"] = Field(..., description="Aggregation function")
    field: Optional[str] = Field(None, description="Field to aggregate (null for count)")
    as_name: str = Field(..., alias="as", description="Output column alias name")


class QueryPlanDateBucket(BaseModel):
    field: str = Field(..., description="Date field name to bucket by")
    unit: Literal["day", "week", "month", "quarter", "year"] = Field(
        ..., description="Date truncation interval"
    )


class QueryPlanSort(BaseModel):
    field: str = Field(..., description="Field name to sort by")
    dir: Literal["asc", "desc"] = Field("asc", description="Sort direction")


class QueryPlan(BaseModel):
    decision: Literal["answer", "refuse", "clarify", "text_search"] = Field(
        "answer", description="Routing decision"
    )
    collection: Optional[str] = Field(
        None, description="Target collection name from schema catalog"
    )
    filters: List[QueryPlanFilter] = Field(
        default_factory=list, max_length=8, description="Filter conditions combined with logical AND"
    )
    group_by: List[str] = Field(
        default_factory=list, max_length=2, description="Fields to group by"
    )
    metrics: List[QueryPlanMetric] = Field(
        default_factory=list, max_length=4, description="Metric aggregations"
    )
    date_bucket: Optional[QueryPlanDateBucket] = Field(
        None, description="Optional date truncation bucket"
    )
    select: List[str] = Field(
        default_factory=list, max_length=10, description="Fields to select for flat row listings"
    )
    sort: List[QueryPlanSort] = Field(
        default_factory=list, max_length=2, description="Sort order specifications"
    )
    limit: int = Field(20, ge=1, le=100, description="Maximum rows to return")
    clarification_question: Optional[str] = Field(
        None, description="Question asked if decision is clarify"
    )
    options: List[str] = Field(
        default_factory=list, description="Clarification answer options"
    )
    headline_template: Optional[str] = Field(
        None, description="Template string for headline with row placeholders"
    )
    detail_template: Optional[str] = Field(
        None, description="Template string for detail explanation"
    )
    follow_ups: List[str] = Field(
        default_factory=list, description="Suggested follow-up natural language questions"
    )
