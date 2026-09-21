"""
Test harness for running and evaluating automated test suites with automatic PYTHONPATH configuration and failure extraction.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class TestResult:
    """Structured test suite execution outcome."""
    __test__ = False
    command: str
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0

    def __post_init__(self):
        if self.total_tests == 0 and self.passed_tests == 0 and self.failed_tests == 0:
            full_text = (self.stdout or "") + ("\n" + self.stderr if self.stderr else "")
            passed_m = re.search(r"(\d+)\s+passed", full_text, re.IGNORECASE)
            failed_m = re.search(r"(\d+)\s+failed", full_text, re.IGNORECASE)
            skipped_m = re.search(r"(\d+)\s+skipped", full_text, re.IGNORECASE)
            if passed_m or failed_m or skipped_m:
                self.passed_tests = int(passed_m.group(1)) if passed_m else 0
                self.failed_tests = int(failed_m.group(1)) if failed_m else 0
                self.skipped_tests = int(skipped_m.group(1)) if skipped_m else 0
                self.total_tests = self.passed_tests + self.failed_tests + self.skipped_tests

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0 and self.failed_tests == 0

    @property
    def failure_summary(self) -> str:
        """Extract the failure tracebacks and summary from output."""
        full_text = self.stdout + ("\n" + self.stderr if self.stderr else "")
        if "=== FAILURES ===" in full_text:
            failures_section = full_text.split("=== FAILURES ===")[1]
            return "=== FAILURES ===\n" + failures_section[-3500:]
        elif "=== short test summary info ===" in full_text:
            summary_section = full_text.split("=== short test summary info ===")[1]
            return "=== short test summary info ===\n" + summary_section[-3500:]
        return full_text[-3000:] if len(full_text) > 3000 else full_text


class TestHarness:
    """Runner harness for automated test and linter execution."""
    __test__ = False  # Prevent pytest from attempting to collect this as a test class

    def __init__(self, cwd: Optional[str] = None):
        self.cwd = cwd or os.getcwd()

    async def run_command(self, command: str, timeout: float = 300.0) -> TestResult:
        """Execute a test or lint command and parse standard metrics."""
        start_time = time.time()

        # Configure environment with automatic PYTHONPATH resolution for target repos and subfolders (e.g. backend/)
        env = os.environ.copy()
        target_ws = os.getenv("TARGET_WORKSPACE", self.cwd)
        extra_paths = [target_ws]
        backend_dir = os.path.join(target_ws, "backend")
        if os.path.isdir(backend_dir):
            extra_paths.append(backend_dir)

        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = ":".join(extra_paths) + (f":{current_pythonpath}" if current_pythonpath else "")

        # Default standard test environment variables if not present
        if "DATABASE_URL" not in env:
            env["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/rfpengine"
        if "TESTING" not in env:
            env["TESTING"] = "true"
        if "ENV" not in env:
            env["ENV"] = "test"

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            duration = time.time() - start_time
            return TestResult(
                command=command,
                exit_code=-1,
                duration_seconds=round(duration, 2),
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=f"Execution timed out after {timeout} seconds",
            )

        duration = round(time.time() - start_time, 2)
        stdout = self._redact_secrets(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = self._redact_secrets(stderr_bytes.decode("utf-8", errors="replace"))
        safe_command = self._redact_secrets(command)

        total, passed, failed, skipped = self._parse_pytest_counts(stdout + "\n" + stderr)

        # If pytest exit code is non-zero (e.g. collection error or test failure), ensure failed count is at least 1
        exit_code = process.returncode if process.returncode is not None else 0
        if exit_code not in [0, 5] and failed == 0:
            failed = 1
            total = max(total, passed + failed)

        return TestResult(
            command=safe_command,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
        )

    def _redact_secrets(self, text: str) -> str:
        """Redacts sensitive tokens, API keys, and authorization headers from logs."""
        if not text:
            return ""
        # Redact GitHub token URLs: https://x-access-token:ghs_...@github.com
        text = re.sub(r"(https://[^:]+:)[^@]+(@github\.com)", r"\1***\2", text)
        # Redact generic bearer tokens
        text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{16,}", r"\1***", text)
        return text

    def _parse_pytest_counts(self, output: str) -> tuple[int, int, int, int]:
        """Extract test counts from pytest or vitest output."""
        passed = 0
        failed = 0
        skipped = 0

        passed_match = re.search(r"(\d+)\s+passed", output, re.IGNORECASE)
        if passed_match:
            passed = int(passed_match.group(1))

        failed_match = re.search(r"(\d+)\s+failed", output, re.IGNORECASE)
        if failed_match:
            failed = int(failed_match.group(1))

        errors_match = re.search(r"(\d+)\s+errors?", output, re.IGNORECASE)
        if errors_match:
            failed += int(errors_match.group(1))

        skipped_match = re.search(r"(\d+)\s+skipped", output, re.IGNORECASE)
        if skipped_match:
            skipped = int(skipped_match.group(1))

        total = passed + failed + skipped
        return total, passed, failed, skipped
