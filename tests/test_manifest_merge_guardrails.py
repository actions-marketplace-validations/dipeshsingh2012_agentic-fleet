"""
Unit tests for dependency manifest merge guardrails and base manifest context enrichment.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.event_router import EventRouter, AgentContextBuilder


def test_merge_requirements_txt_preserves_existing():
    existing = (
        "# Core Dependencies\n"
        "fastapi>=0.115,<1.0\n"
        "sqlalchemy[asyncio]>=2.0,<3.0\n"
        "pydantic>=2.8,<3.0\n"
        "pytest>=8.0,<9.0\n"
    )
    # Dev agent outputs only pytest and PyJWT
    new = "pytest==8.0.0\nPyJWT>=2.8.0\n"

    merged = EventRouter._merge_requirements_txt(existing, new)

    assert "fastapi>=0.115,<1.0" in merged
    assert "sqlalchemy[asyncio]>=2.0,<3.0" in merged
    assert "pydantic>=2.8,<3.0" in merged
    assert "pytest==8.0.0" in merged
    assert "PyJWT>=2.8.0" in merged
    assert "# Core Dependencies" in merged


def test_merge_package_json_preserves_dependencies():
    existing = json.dumps({
        "name": "frontend",
        "dependencies": {"react": "^18.2.0", "next": "^14.0.0"},
        "scripts": {"build": "next build"}
    })
    new = json.dumps({
        "dependencies": {"lucide-react": "^0.300.0"},
        "scripts": {"lint": "eslint"}
    })

    merged = EventRouter._merge_package_json(existing, new)
    data = json.loads(merged)

    assert data["dependencies"]["react"] == "^18.2.0"
    assert data["dependencies"]["next"] == "^14.0.0"
    assert data["dependencies"]["lucide-react"] == "^0.300.0"
    assert data["scripts"]["build"] == "next build"
    assert data["scripts"]["lint"] == "eslint"


def test_materialize_code_files_merges_requirements(tmp_path: Path):
    backend_req = tmp_path / "backend" / "requirements.txt"
    backend_req.parent.mkdir(parents=True)
    backend_req.write_text("fastapi>=0.115\npydantic>=2.8\n")

    router = EventRouter(dry_run=True)
    content = "```text:backend/requirements.txt\npytest==8.0.0\n```"

    materialized = router._materialize_code_files(tmp_path, content)

    result_text = backend_req.read_text()
    assert "fastapi>=0.115" in result_text
    assert "pydantic>=2.8" in result_text
    assert "pytest==8.0.0" in result_text


def test_context_builder_includes_existing_dependencies(tmp_path: Path):
    req_file = tmp_path / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True)
    req_file.write_text("fastapi>=0.115\nsqlalchemy>=2.0\n")

    ws_info = AgentContextBuilder.inspect_workspace(tmp_path)
    assert "backend/requirements.txt" in ws_info.get("existing_manifests", {})
    assert "fastapi>=0.115" in ws_info["existing_manifests"]["backend/requirements.txt"]

    context_block = AgentContextBuilder.format_context_block(
        workspace_info=ws_info,
        issue_info={"number": 1, "title": "Test Issue", "body": "Description"},
    )
    assert "### 📦 Active Project Dependencies (MUST PRESERVE)" in context_block
    assert "backend/requirements.txt" in context_block
    assert "fastapi>=0.115" in context_block
