"""
Unit tests for polyglot workspace inspector and test runner detection.
Tests Python, TypeScript/Node, Go, Rust, Java, and Taskfile detection.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from src.workspace_inspector import WorkspaceInspector


def test_detect_python_backend_workspace(tmp_path: Path):
    backend = tmp_path / "backend" / "app"
    backend.mkdir(parents=True)
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "backend" / "tests").mkdir(parents=True)

    profile = WorkspaceInspector.inspect(tmp_path)
    assert profile.primary_language == "python"
    assert profile.has_backend_dir is True
    assert profile.test_command == "pytest -v backend/tests"
    assert profile.package_markers_needed is True


def test_detect_typescript_node_workspace(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "index.ts").write_text("export const x = 1;")

    profile = WorkspaceInspector.inspect(tmp_path)
    assert "typescript" in profile.detected_languages
    assert profile.test_command == "npm test"
    assert profile.package_markers_needed is False
    assert profile.install_command == "npm install"


def test_detect_go_workspace(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")

    profile = WorkspaceInspector.inspect(tmp_path)
    assert profile.primary_language == "go"
    assert profile.test_command == "go test ./..."
    assert profile.package_markers_needed is False
    assert profile.install_command == "go mod tidy"


def test_detect_rust_workspace(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n')
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")

    profile = WorkspaceInspector.inspect(tmp_path)
    assert profile.primary_language == "rust"
    assert profile.test_command == "cargo test"
    assert profile.install_command == "cargo check"


def test_detect_taskfile_priority_when_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "Taskfile.yml").write_text("version: '3'\ntasks:\n  test:\n    cmds:\n      - pytest -v\n")
    (tmp_path / "requirements.txt").write_text("pytest\n")
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/task" if cmd == "task" else None)

    profile = WorkspaceInspector.inspect(tmp_path)
    assert profile.test_command == "task test"


def test_detect_taskfile_fallback_when_task_binary_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "Taskfile.yml").write_text("version: '3'\ntasks:\n  test:\n    cmds:\n      - pytest -v\n")
    (tmp_path / "requirements.txt").write_text("pytest\n")
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    profile = WorkspaceInspector.inspect(tmp_path)
    # When 'task' is not installed, must fall back to native language test runner (pytest)
    assert profile.test_command == "pytest -v"
