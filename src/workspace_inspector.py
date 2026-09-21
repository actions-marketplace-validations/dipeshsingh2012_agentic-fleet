"""
Dynamic polyglot workspace inspector and test runner detector.
Detects programming languages, root manifests, test runners, and package managers
without assuming fixed directory layouts (Python, TypeScript/Node, Go, Rust, Java, Taskfile, Make).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentic-fleet.workspace_inspector")


@dataclass
class WorkspaceProfile:
    """Detailed profile of an inspected workspace."""

    root_dir: Path
    primary_language: str = "unknown"
    detected_languages: List[str] = field(default_factory=list)
    has_backend_dir: bool = False
    has_frontend_dir: bool = False
    backend_path: Optional[Path] = None
    frontend_path: Optional[Path] = None
    test_command: str = "pytest -v"
    install_command: Optional[str] = None
    dependency_manifests: List[str] = field(default_factory=list)
    package_markers_needed: bool = True  # e.g., __init__.py for Python


class WorkspaceInspector:
    """Inspects workspace manifests and returns a tailored execution profile."""

    @classmethod
    def inspect(cls, workspace_dir: Path) -> WorkspaceProfile:
        """Inspects directory structure and returns a rich WorkspaceProfile."""
        workspace_dir = Path(workspace_dir).resolve()
        profile = WorkspaceProfile(root_dir=workspace_dir)

        detected_langs: List[str] = []
        manifests: List[str] = []

        # 1. Check for subdirectories (backend/frontend monorepo layouts)
        backend_dir = workspace_dir / "backend"
        frontend_dir = workspace_dir / "frontend"
        if backend_dir.exists() and backend_dir.is_dir():
            profile.has_backend_dir = True
            profile.backend_path = backend_dir
        if frontend_dir.exists() and frontend_dir.is_dir():
            profile.has_frontend_dir = True
            profile.frontend_path = frontend_dir

        search_roots = [workspace_dir]
        if profile.has_backend_dir and profile.backend_path:
            search_roots.append(profile.backend_path)

        # 2. Check for Taskfile or Makefile at root
        if (workspace_dir / "Taskfile.yml").exists() or (workspace_dir / "Taskfile.yaml").exists():
            manifests.append("Taskfile.yml")
        if (workspace_dir / "Makefile").exists():
            manifests.append("Makefile")

        # 3. Detect Python
        has_python = False
        for root in search_roots:
            if (root / "pyproject.toml").exists():
                has_python = True
                manifests.append(str(root.relative_to(workspace_dir) / "pyproject.toml") if root != workspace_dir else "pyproject.toml")
            if (root / "requirements.txt").exists():
                has_python = True
                manifests.append(str(root.relative_to(workspace_dir) / "requirements.txt") if root != workspace_dir else "requirements.txt")
            if (root / "setup.py").exists() or (root / "Pipfile").exists():
                has_python = True

        if has_python or any(workspace_dir.glob("**/*.py")):
            detected_langs.append("python")

        # 4. Detect Node / TypeScript / JavaScript
        has_node = False
        for root in [workspace_dir, frontend_dir] if profile.has_frontend_dir else [workspace_dir]:
            if (root / "package.json").exists():
                has_node = True
                manifests.append(str(root.relative_to(workspace_dir) / "package.json") if root != workspace_dir else "package.json")
            if (root / "tsconfig.json").exists():
                has_node = True

        if has_node or any(workspace_dir.glob("**/*.ts")) or any(workspace_dir.glob("**/*.js")):
            detected_langs.append("typescript" if any(workspace_dir.glob("**/*.ts")) else "javascript")

        # 5. Detect Go
        if (workspace_dir / "go.mod").exists() or any(workspace_dir.glob("**/*.go")):
            detected_langs.append("go")
            if (workspace_dir / "go.mod").exists():
                manifests.append("go.mod")

        # 6. Detect Rust
        if (workspace_dir / "Cargo.toml").exists() or any(workspace_dir.glob("**/*.rs")):
            detected_langs.append("rust")
            if (workspace_dir / "Cargo.toml").exists():
                manifests.append("Cargo.toml")

        # 7. Detect Java / Kotlin
        if (workspace_dir / "pom.xml").exists() or (workspace_dir / "build.gradle").exists():
            detected_langs.append("java")
            if (workspace_dir / "pom.xml").exists():
                manifests.append("pom.xml")
            if (workspace_dir / "build.gradle").exists():
                manifests.append("build.gradle")

        profile.detected_languages = detected_langs or ["python"]
        profile.primary_language = detected_langs[0] if detected_langs else "python"
        profile.dependency_manifests = manifests
        profile.package_markers_needed = ("python" in profile.detected_languages)

        # 8. Determine Default Test & Install Commands
        profile.test_command = cls._resolve_test_command(workspace_dir, profile)
        profile.install_command = cls._resolve_install_command(workspace_dir, profile)

        return profile

    @classmethod
    def _resolve_test_command(cls, workspace_dir: Path, profile: WorkspaceProfile) -> str:
        """Determines the most accurate test execution command based on manifests."""
        # Check Taskfile runner only if 'task' executable is present on PATH
        if (workspace_dir / "Taskfile.yml").exists() or (workspace_dir / "Taskfile.yaml").exists():
            taskfile_content = (workspace_dir / "Taskfile.yml").read_text(encoding="utf-8", errors="ignore") if (workspace_dir / "Taskfile.yml").exists() else ""
            if "test:" in taskfile_content and shutil.which("task"):
                return "task test"

        if profile.primary_language == "rust":
            return "cargo test"

        if profile.primary_language == "go":
            return "go test ./..."

        if profile.primary_language in ["typescript", "javascript"]:
            pkg_path = workspace_dir / "package.json"
            if pkg_path.exists():
                try:
                    pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
                    scripts = pkg_data.get("scripts", {})
                    if "test" in scripts:
                        return "npm test"
                except Exception:
                    pass
            return "npm test"

        if profile.primary_language == "java":
            if (workspace_dir / "pom.xml").exists():
                return "mvn test"
            if (workspace_dir / "build.gradle").exists():
                return "./gradlew test"

        if profile.has_backend_dir and profile.backend_path and (profile.backend_path / "tests").exists():
            return "pytest -v backend/tests"
        return "pytest -v"

    @classmethod
    def _resolve_install_command(cls, workspace_dir: Path, profile: WorkspaceProfile) -> Optional[str]:
        """Determines dependency installation command."""
        if "rust" in profile.detected_languages:
            return "cargo check"
        if "go" in profile.detected_languages:
            return "go mod tidy"
        if "typescript" in profile.detected_languages or "javascript" in profile.detected_languages:
            if (workspace_dir / "pnpm-lock.yaml").exists():
                return "pnpm install"
            if (workspace_dir / "yarn.lock").exists():
                return "yarn install"
            return "npm install"
        if "python" in profile.detected_languages:
            req_path = "backend/requirements.txt" if profile.has_backend_dir and (workspace_dir / "backend" / "requirements.txt").exists() else "requirements.txt"
            if (workspace_dir / req_path).exists():
                return f"pip install -r {req_path}"
        return None
