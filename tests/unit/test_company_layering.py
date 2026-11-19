"""
Layering Tests for Company Module (tests/unit/test_company_layering.py)
Enforces architectural boundaries: app/modules/company/ must not import
motor, pymongo, pandas, fastapi, or starlette.
"""

import ast
from pathlib import Path

FORBIDDEN_IMPORTS = {"motor", "pymongo", "pandas", "fastapi", "starlette"}


def test_company_module_has_no_forbidden_imports():
    company_module_dir = Path(__file__).resolve().parent.parent.parent / "app" / "modules" / "company"
    assert company_module_dir.is_dir(), f"Directory not found: {company_module_dir}"

    for py_file in company_module_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    assert pkg not in FORBIDDEN_IMPORTS, (
                        f"Forbidden import '{pkg}' found in {py_file.name}:{node.lineno}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    pkg = node.module.split(".")[0]
                    assert pkg not in FORBIDDEN_IMPORTS, (
                        f"Forbidden import '{pkg}' found in {py_file.name}:{node.lineno}"
                    )


def test_pyproject_toml_does_not_contain_pandas():
    pyproject_file = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    content = pyproject_file.read_text(encoding="utf-8")
    assert "pandas" not in content.lower(), "pandas dependency found in pyproject.toml"
