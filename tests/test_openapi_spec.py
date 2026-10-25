"""
tests/test_openapi_spec.py

Verifies IncuBrix Integration Standard #8:
- Export openapi.yaml from the running app (script + CI check that it is up to date)
- Everything under /api/v1 (except /health and /ready liveness/readiness probes)
"""

import os
from pathlib import Path
import pytest
import yaml

from promptcut.api.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_YAML_PATH = REPO_ROOT / "openapi.yaml"


def test_openapi_yaml_exists_and_valid():
    """openapi.yaml must exist in the root directory and parse as valid YAML."""
    assert OPENAPI_YAML_PATH.is_file(), "openapi.yaml must exist in repository root"
    content = OPENAPI_YAML_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert "openapi" in parsed
    assert "paths" in parsed
    assert "info" in parsed


def test_all_api_endpoints_under_api_v1():
    """All API endpoints in the OpenAPI spec must be under /api/v1 (except /health and /ready)."""
    app = create_app()
    openapi_schema = app.openapi()

    allowed_root_paths = {"/health", "/ready", "/ui", "/"}
    paths = openapi_schema.get("paths", {})

    for path in paths:
        if path in allowed_root_paths or path.startswith("/ui/"):
            continue
        assert path.startswith("/api/v1/"), f"Endpoint {path} must be prefixed with /api/v1"


def test_openapi_yaml_is_up_to_date():
    """openapi.yaml in repository root must match the running application schema."""
    app = create_app()
    current_schema = app.openapi()

    content = OPENAPI_YAML_PATH.read_text(encoding="utf-8")
    saved_schema = yaml.safe_load(content)

    assert saved_schema.get("paths") == current_schema.get("paths"), (
        "openapi.yaml paths do not match running app. Run 'python scripts/export_openapi.py' to update."
    )
    assert saved_schema.get("info") == current_schema.get("info"), (
        "openapi.yaml info does not match running app. Run 'python scripts/export_openapi.py' to update."
    )
