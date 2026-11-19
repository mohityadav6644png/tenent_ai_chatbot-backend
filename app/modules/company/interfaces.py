"""
Company Interfaces (app/modules/company/interfaces.py)
Abstract protocols for data storage, CSV parsing, ingestion, tracking, and cache.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class CompanyRecordsRepoProtocol(Protocol):
    """Protocol for tenant company_records free-text storage."""

    async def insert_record(self, doc: Dict[str, Any]) -> str:
        """Insert a single record and return the inserted string ID."""
        ...

    async def bulk_insert_records(self, docs: List[Dict[str, Any]]) -> int:
        """Insert multiple records in batch and return the count of inserted documents."""
        ...


@runtime_checkable
class DataIngestionRepoProtocol(Protocol):
    """Protocol for multi-tenant business collection ingestion (customers, products, orders, etc.)."""

    async def get_dataset_stats(
        self, company_id: str, collection_name: str
    ) -> Tuple[int, Optional[datetime]]:
        """Return (count_of_records, last_updated_datetime)."""
        ...

    async def upsert_records(
        self,
        company_id: str,
        collection_name: str,
        natural_key: List[str],
        records: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        """Upsert records matching natural keys scoped to company_id. Return (inserted_count, updated_count)."""
        ...

    async def replace_records(
        self,
        company_id: str,
        collection_name: str,
        import_id: str,
        records: List[Dict[str, Any]],
    ) -> int:
        """Atomically replace all records for company_id in the collection. Return inserted_count."""
        ...


@runtime_checkable
class DataImportTrackingRepoProtocol(Protocol):
    """Protocol for import history, concurrency control, and rate limiting."""

    async def has_active_import(
        self, company_id: str, dataset: str, timeout_minutes: int = 15
    ) -> bool:
        """Return True if an import for this tenant and dataset is currently running."""
        ...

    async def count_recent_imports(self, company_id: str, hours: int = 1) -> int:
        """Count completed or in-progress imports for company_id within the past X hours."""
        ...

    async def create_import(self, doc: Dict[str, Any]) -> str:
        """Record a new import attempt."""
        ...

    async def update_import(self, import_id: str, updates: Dict[str, Any]) -> None:
        """Update status, counts, or error messages for an import."""
        ...


@runtime_checkable
class CsvParserProtocol(Protocol):
    """Protocol for parsing and validating CSV data without pandas."""

    def parse_csv_stream(
        self,
        content_bytes: bytes,
        max_file_mb: int,
        max_rows: int,
        max_columns: int,
        max_cell_chars: int,
    ) -> Tuple[List[str], List[Dict[str, str]], int]:
        """
        Parses raw bytes using standard library csv.
        Returns: (headers, rows_as_dicts, skipped_empty_count).
        Raises domain/validation exceptions if limits or formats are violated.
        """
        ...

    def generate_template_csv(self, columns: List[str]) -> str:
        """Generate a CSV string with only the header row."""
        ...


@runtime_checkable
class TenantSchemaProtocol(Protocol):
    """Protocol for tenant lifecycle and schema version bumping."""

    async def bump_schema_version(self, tenant_id: str) -> int:
        """Increment tenant schema_version and return new version integer."""
        ...


@runtime_checkable
class CacheClientProtocol(Protocol):
    """Protocol for distributed or in-memory cache invalidation."""

    async def delete(self, key: str) -> int:
        ...
