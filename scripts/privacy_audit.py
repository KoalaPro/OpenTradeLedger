#!/usr/bin/env python3
"""Open-source privacy & secret audit gate for OpenTradeLedger."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "node_modules",
    "data",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    ".vite",
}

TEXT_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".env",
    ".example",
    ".css",
    ".html",
    ".sh",
}

PRIVATE_IP_RE = re.compile(
    r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)

CREDENTIAL_ASSIGN_RE = re.compile(
    r"""(?i)\b[A-Z0-9_]*(?:API_KEY|API_SECRET|PASSPHRASE|BARK_PUSH_KEY|BOT_TOKEN|PRIVATE_KEY)\s*[:=]\s*['"]?([A-Za-z0-9_\-]{20,})['"]?"""
)

HOME_PATH_RE = re.compile(r"/Users/[A-Za-z0-9._-]+/")

FORBIDDEN_LITERALS = (
    "data_strategy_workbench",
    "workbench_v2_llm_credentials",
    "gate_manual_private",
)

PLACEHOLDER_HINTS = ("your_", "example", "placeholder", "dummy", "test_key", "mock_", "xxxx")


def _should_scan(path: Path) -> bool:
    if path.name in {"privacy_audit.py", "package-lock.json"}:
        return False
    if path.name.startswith("test_"):
        return False
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    if path.name in {"Dockerfile", "Makefile", ".env", ".env.example"}:
        return True
    return False


def run_privacy_audit(root: Path) -> list[str]:
    findings: list[str] = []
    root = root.resolve()
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file() or not _should_scan(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel = str(path.relative_to(root))
        for lineno, line in enumerate(content.splitlines(), start=1):
            if PRIVATE_IP_RE.search(line):
                findings.append(f"{rel}:{lineno}: private IP address detected")
            if HOME_PATH_RE.search(line):
                findings.append(f"{rel}:{lineno}: local macOS user home path detected")
            for match in CREDENTIAL_ASSIGN_RE.finditer(line):
                val = match.group(1)
                if not any(h in val.lower() for h in PLACEHOLDER_HINTS):
                    findings.append(f"{rel}:{lineno}: possible hardcoded credential detected")
            for literal in FORBIDDEN_LITERALS:
                if literal in line:
                    findings.append(f"{rel}:{lineno}: personal/private literal '{literal}' detected")
    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    findings = run_privacy_audit(repo_root)
    if findings:
        print("PRIVACY AUDIT FAILED:", file=sys.stderr)
        for item in findings:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("privacy_audit: OK (0 findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
