"""
Join & Correlation Key Detector (app/modules/multidoc/join_detector.py)
Pure functions to identify common or semantically aligned join keys across schemas.
No motor, redis, google-genai, or fastapi imports.
"""

import re
from typing import Any, Dict, List, Optional, Set

SYSTEM_EXCLUDED_KEYS = {
    "_id",
    "company_id",
    "file_id",
    "import_id",
    "created_at",
    "updated_at",
    "password",
    "token",
    "token_hash",
    "secret",
    "api_key",
}

KEY_PRIORITY_PATTERNS = [
    re.compile(r"^[a-z0-9_]*_id$"),
    re.compile(r"^id$"),
    re.compile(r"^sku$"),
    re.compile(r"^[a-z0-9_]*_code$"),
    re.compile(r"^code$"),
    re.compile(r"^[a-z0-9_]*_name$"),
    re.compile(r"^name$"),
    re.compile(r"^category$"),
    re.compile(r"^department$"),
    re.compile(r"^region$"),
]


def normalize_field_name(name: str) -> str:
    """Normalize field name to snake_case alphanumeric identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return cleaned


def detect_join_keys(
    schemas: List[List[str]],
    hidden_fields: Optional[Set[str]] = None,
) -> List[str]:
    """
    Given field lists from 2+ documents, identify common candidate join keys.
    Excludes internal/system fields and hidden columns.
    Prioritizes strong identifiers (_id, sku, code) over general attributes.
    """
    if len(schemas) < 2:
        return []

    hidden_norm = {normalize_field_name(f) for f in (hidden_fields or set())}
    excluded = SYSTEM_EXCLUDED_KEYS | hidden_norm

    doc_field_sets = [
        {normalize_field_name(f) for f in fields if normalize_field_name(f) not in excluded}
        for fields in schemas
    ]

    # Find keys present in at least 2 document schemas
    common_keys: Set[str] = set()
    for i in range(len(doc_field_sets)):
        for j in range(i + 1, len(doc_field_sets)):
            common_keys.update(doc_field_sets[i].intersection(doc_field_sets[j]))

    if not common_keys:
        return []

    def key_rank(k: str) -> int:
        for idx, pat in enumerate(KEY_PRIORITY_PATTERNS):
            if pat.match(k):
                return idx
        return 999

    sorted_keys = sorted(common_keys, key=lambda k: (key_rank(k), k))
    return sorted_keys
