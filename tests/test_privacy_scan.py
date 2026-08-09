"""Tests for scripts/privacy_scan.py — no gateway/Hermes checkout required."""

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCANNER_PATH = _REPO_ROOT / "scripts" / "privacy_scan.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("privacy_scan", _SCANNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["privacy_scan"] = module
    spec.loader.exec_module(module)
    return module


scanner = _load_scanner()


def test_scanner_flags_non_reserved_phone_number(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("call +14155551212 for support")
    findings = scanner.scan_file(bad)
    assert any("phone-number-shaped" in f for f in findings)


def test_scanner_allows_reserved_nanp_range(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("example: +12025550142 and 2025550100")
    findings = scanner.scan_file(good)
    assert findings == []


def test_scanner_ignores_numeric_run_inside_commit_sha_but_still_flags_phone(tmp_path):
    mixed = tmp_path / "mixed.yml"
    mixed.write_text(
        "uses: action@fc324d3547104276b827a68afc52ff2a11cc49c9\n"
        "support: +14155551212\n"
    )
    findings = scanner.scan_file(mixed)
    assert len(findings) == 1
    assert "+14155551212" in findings[0]


def test_scanner_flags_personal_home_path(tmp_path):
    bad = tmp_path / "bad.sh"
    bad.write_text("PY=/Users/realperson/venv/bin/python3")
    findings = scanner.scan_file(bad)
    assert any("personal home-directory path" in f for f in findings)


def test_scanner_allows_placeholder_home_path(tmp_path):
    good = tmp_path / "good.py"
    good.write_text('assert "/Users/exampleuser" not in text')
    findings = scanner.scan_file(good)
    assert findings == []


def test_scanner_does_not_flag_its_own_regex_source_pattern(tmp_path):
    # A literal regex character class shaped like `/Users/[^/\s"']+` (as
    # appears in rules.py's own redaction pattern) must not be mistaken for
    # a real per-user path — the bracketed class doesn't look like a
    # plausible username and is skipped naturally by the username shape.
    good = tmp_path / "good.py"
    good.write_text(r'_HOME_PATH_RE = re.compile(r"/Users/[^/\s\"\']+")')
    findings = scanner.scan_file(good)
    assert findings == []


def test_scanner_flags_aws_key():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write("AWS_KEY=AKIAABCDEFGHIJKLMNOP\n")
        path = Path(f.name)
    try:
        findings = scanner.scan_file(path)
        assert any("AWS access key" in f for f in findings)
    finally:
        path.unlink()


def test_scanner_flags_private_key_header(tmp_path):
    bad = tmp_path / "id_rsa"
    bad.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----\n")
    findings = scanner.scan_file(bad)
    assert any("private key header" in f for f in findings)


def test_scanner_flags_non_example_email(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("contact realperson@gmail.com for help")
    findings = scanner.scan_file(bad)
    assert any("non-example domain" in f for f in findings)


def test_scanner_allows_example_domain_email(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("contact test@example.com for help")
    findings = scanner.scan_file(good)
    assert findings == []


def test_scanner_skips_binary_files(tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\x00\x01+14155551212\x00")
    findings = scanner.scan_file(bad)
    assert findings == []


@pytest.mark.skipif(not (_REPO_ROOT / ".git").exists(), reason="requires a git checkout")
def test_full_repository_scan_passes():
    """End-to-end regression: the actual tracked repository content must be
    clean. This is the same check CI runs via `python scripts/privacy_scan.py`."""
    exit_code = scanner.main([])
    assert exit_code == 0
