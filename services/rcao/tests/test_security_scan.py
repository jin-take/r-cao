"""High-confidence secret-material scan for tracked text files.

This is intentionally narrower than a commercial secret scanner: ordinary
words such as ``secret`` appear in policy tests and documentation.  The scan
only rejects credential formats that should never be committed, while the
runtime tests cover policy-level secret and wallet rejection.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
)


def _tracked_text_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "-C", str(REPOSITORY_ROOT), "ls-files", "-z"],
        text=False,
    )
    return [
        REPOSITORY_ROOT / item
        for item in output.decode("utf-8").split("\0")
        if item and (REPOSITORY_ROOT / item).is_file()
    ]


def test_tracked_files_do_not_contain_high_confidence_credentials() -> None:
    findings: list[str] = []
    for path in _tracked_text_files():
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(content):
                findings.append(f"{path.relative_to(REPOSITORY_ROOT)}: {pattern.pattern}")

    assert not findings, "credential-like material found in tracked files:\n" + "\n".join(findings)
