"""
Export OpenAPI Specification (scripts/export_openapi.py)
Generates and writes docs/openapi.json from the active FastAPI application.
"""

import json
from pathlib import Path
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app


def export_openapi() -> None:
    openapi_schema = app.openapi()
    root = Path(__file__).resolve().parent.parent.parent
    paths = [
        root / "docs" / "backend-openapi.json",
        root / "frontend" / "docs" / "backend-openapi.json",
        Path(__file__).resolve().parent.parent / "docs" / "openapi.json",
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(openapi_schema, f, indent=2)
        print(f"OpenAPI spec successfully written to {p}")


if __name__ == "__main__":
    export_openapi()
