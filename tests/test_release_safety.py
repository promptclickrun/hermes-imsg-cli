"""Release metadata and fail-closed prerequisite checks."""

from pathlib import Path
from types import SimpleNamespace

from hermes_imsg_cli import __version__
from hermes_imsg_cli.attachments import validate_outbound_attachment_path
from hermes_imsg_cli.config import check_imsg_requirements
from hermes_imsg_cli.rules import parse_imsg_rules


REPO_ROOT = Path(__file__).resolve().parent.parent


def _quoted_assignment(path: Path, key: str) -> str:
    prefix = f"{key} = \""
    for line in path.read_text().splitlines():
        if line.startswith(prefix) and line.endswith('"'):
            return line[len(prefix):-1]
    raise AssertionError(f"{key} assignment not found in {path}")


def _manifest_version(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("version: "):
            return line.removeprefix("version: ").strip()
    raise AssertionError(f"version field not found in {path}")


def test_runtime_version_matches_release_manifests():
    project_version = _quoted_assignment(REPO_ROOT / "pyproject.toml", "version")
    manifest_version = _manifest_version(REPO_ROOT / "hermes_imsg_cli" / "plugin.yaml")
    assert __version__ == project_version == manifest_version


def test_status_probe_rejects_malformed_json(monkeypatch):
    monkeypatch.setattr("hermes_imsg_cli.config.sys.platform", "darwin")
    monkeypatch.setattr(
        "hermes_imsg_cli.config.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not-json"),
    )
    assert check_imsg_requirements("/opt/homebrew/bin/imsg") is False


def test_invalid_rule_action_is_redacted_in_logs(caplog):
    sensitive = "confidential-contact-label +12025550100"
    with caplog.at_level("WARNING", logger="hermes_imsg_cli.rules"):
        rules = parse_imsg_rules([{"name": "invalid", "action": sensitive}])
    assert rules[0].action == "silent_drop"
    assert sensitive not in caplog.text
    assert "confidential-contact-label" not in caplog.text
    assert "defaulting to silent_drop" in caplog.text


def test_attachment_validation_error_does_not_disclose_local_path():
    private_path = "/Volumes/Private/client-name/report.pdf"
    error = validate_outbound_attachment_path(private_path)
    assert error is not None
    assert "not found" in error
    assert private_path not in error
    assert "client-name" not in error
