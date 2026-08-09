#!/usr/bin/env python3
"""Privacy/security scanner for the hermes-imsg-cli repository.

Scans every git-tracked text file for patterns that should never ship in a
public repo: phone numbers outside the NANP reserved-for-fiction range
(+1 202-555-01xx), personal home-directory paths, and common secret shapes
(API keys, private key headers, cloud/service tokens).

Usage:
    python scripts/privacy_scan.py            # scan tracked files (git ls-files)
    python scripts/privacy_scan.py path1 ...   # scan specific files instead

Exit code is non-zero if any finding is reported.  This is a heuristic tool,
not a guarantee — it complements, not replaces, code review.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# This scanner's own source unavoidably contains the pattern literals it
# looks for, and its test file deliberately contains "bad" example strings to
# verify detection works; scanning either would only produce self-matches.
SELF_EXCLUDED_PATHS = {
    Path(__file__).resolve(),
    (REPO_ROOT / "tests" / "test_privacy_scan.py").resolve(),
}

# Binary / generated / vendored paths never worth scanning.
EXCLUDED_DIR_PARTS = {".git", "__pycache__", ".pytest_cache", "dist", "build", "*.egg-info"}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dylib", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".pdf", ".whl", ".zip", ".tar", ".gz",
}

# ---------------------------------------------------------------------------
# Phone numbers: only the NANP fictional-use reserved block is allowed.
# ---------------------------------------------------------------------------

# +1 202-555-01xx (or any NPA-555-01xx) is reserved by NANP for fiction/
# examples and is guaranteed to never be a real, routable number.
_RESERVED_PHONE_RE = re.compile(r"^\+?1?\d{3}5550(1\d{2})$")

# Matches a plausible phone-shaped digit run: optional country code, area
# code, exchange, subscriber number (10-11 consecutive digits), optionally
# with a leading '+'. Full 40-character Git commit SHAs are excluded below so
# an internal numeric run in an immutable Actions pin is not misclassified.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\d.])\+?1?\d{10}(?![\d.])")
_GIT_COMMIT_SHA_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])")

# ---------------------------------------------------------------------------
# Home-directory paths.  Only flag ones that look like a real per-user
# directory name; deliberately excludes generic placeholders used in tests
# and the regex source that matches this exact shape.
# ---------------------------------------------------------------------------

_HOME_PATH_RE = re.compile(r"/(?:Users|home)/([A-Za-z0-9][A-Za-z0-9_.-]{1,32})(?=/|[\s\"'`)]|$)")
_ALLOWED_PLACEHOLDER_USERS = {
    "exampleuser", "example", "you", "user", "someone", "yourname", "username",
}

# ---------------------------------------------------------------------------
# Secret shapes.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "assigned secret-looking value",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key)\b"
            r"\s*[:=]\s*[\"']([A-Za-z0-9/+_=-]{20,})[\"']"
        ),
    ),
]

# Non-example email domains are a weaker signal (many are legitimate, e.g.
# in LICENSE/CONTRIBUTING), so this is reported as an info-level note, not a
# failure, unless combined with other findings.
_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com"}


def _iter_tracked_files(paths: list[str]) -> list[Path]:
    """Return files to scan: explicit *paths*, or every git-tracked file plus
    any new file that isn't yet tracked but also isn't gitignored (so a file
    added just before the first commit is still covered, not only what has
    already been `git add`ed)."""
    if paths:
        return [Path(p) for p in paths]
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except Exception as exc:  # pragma: no cover - defensive
        print(f"warning: `git ls-files` failed ({exc}); scanning working tree instead", file=sys.stderr)
        return [p for p in REPO_ROOT.rglob("*") if p.is_file()]
    lines = [line for line in (tracked + untracked).splitlines() if line.strip()]
    return [REPO_ROOT / line for line in lines]


def _should_scan(path: Path) -> bool:
    if path.resolve() in SELF_EXCLUDED_PATHS:
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    parts = set(path.parts)
    if parts & EXCLUDED_DIR_PARTS:
        return False
    return True


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None  # binary
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_file(path: Path) -> list[str]:
    text = _read_text(path)
    if text is None:
        return []
    try:
        rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
    except ValueError:
        rel = path  # outside the repo (e.g. a test's own tmp_path fixture)
    findings: list[str] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        commit_sha_spans = [match.span() for match in _GIT_COMMIT_SHA_RE.finditer(line)]
        for match in _PHONE_CANDIDATE_RE.finditer(line):
            if any(start <= match.start() and match.end() <= end for start, end in commit_sha_spans):
                continue
            candidate = match.group(0)
            if not _RESERVED_PHONE_RE.match(candidate):
                findings.append(
                    f"{rel}:{line_no}: phone-number-shaped value outside the NANP reserved "
                    f"202-555-01xx fictional range: {candidate!r}"
                )

        for match in _HOME_PATH_RE.finditer(line):
            username = match.group(1)
            if username.lower() not in _ALLOWED_PLACEHOLDER_USERS:
                findings.append(
                    f"{rel}:{line_no}: personal home-directory path (user {username!r}); "
                    "use a generic placeholder (e.g. 'exampleuser') or $HOME instead"
                )

        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(f"{rel}:{line_no}: possible {label}")

        for match in _EMAIL_RE.finditer(line):
            domain = match.group(1).lower()
            if domain not in _ALLOWED_EMAIL_DOMAINS:
                findings.append(
                    f"{rel}:{line_no}: email address with a non-example domain: "
                    f"{match.group(0)!r} (use @example.com in docs/tests)"
                )

    return findings


def main(argv: list[str]) -> int:
    files = [p for p in _iter_tracked_files(argv) if _should_scan(p)]
    all_findings: list[str] = []
    for path in sorted(files):
        all_findings.extend(scan_file(path))

    if all_findings:
        print(f"privacy/security scan: {len(all_findings)} finding(s)\n")
        for finding in all_findings:
            print(f"  - {finding}")
        print(f"\nscanned {len(files)} file(s); FAIL")
        return 1

    print(f"privacy/security scan: scanned {len(files)} file(s); no findings; PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
