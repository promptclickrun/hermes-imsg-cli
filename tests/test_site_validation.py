"""Deterministic release-site validation tests."""

import re
from pathlib import Path

from scripts import validate_site


REPO_ROOT = Path(__file__).resolve().parent.parent
REQUESTED_VIEWPORTS = ((1440, 900), (768, 1024), (390, 844), (320, 700))


def test_repository_site_passes_release_validation():
    assert validate_site.validate_repository(REPO_ROOT) == []


def test_repository_site_passes_requested_viewport_contracts():
    html = (REPO_ROOT / "site" / "index.html").read_text()
    css = (REPO_ROOT / "site" / "styles.css").read_text()
    assert validate_site.VIEWPORTS == REQUESTED_VIEWPORTS
    assert validate_site.validate_viewport_contracts(html, css) == []


def test_viewport_audit_rejects_default_figure_inline_margin():
    html = (REPO_ROOT / "site" / "index.html").read_text()
    css = (REPO_ROOT / "site" / "styles.css").read_text()
    css_without_reset = re.sub(
        r"(?m)^\s*margin-inline:\s*0;\s*$",
        "",
        css,
    )
    errors = validate_site.validate_viewport_contracts(html, css_without_reset)
    assert any("hero route must reset figure inline margins" in error for error in errors)


def test_html_audit_requires_local_favicon_link():
    html = (REPO_ROOT / "site" / "index.html").read_text()
    html_without_favicon = re.sub(
        r'\n\s*<link\s+rel="icon"[^>]*>',
        "",
        html,
    )
    errors = validate_site.validate_html_text(html_without_favicon, REPO_ROOT / "site")
    assert any("local favicon link" in error for error in errors)


def test_action_reference_requires_full_commit_sha():
    assert validate_site.is_pinned_official_action(
        "actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d"
    )
    assert not validate_site.is_pinned_official_action("actions/configure-pages@v5")
    assert not validate_site.is_pinned_official_action(
        "third-party/configure-pages@983d7736d9b0ae728b81ab479565c72886d7745b"
    )


def test_html_audit_rejects_remote_runtime_dependency(tmp_path):
    html = """<!doctype html>
    <html lang="en"><head><title>Test</title>
    <link rel="stylesheet" href="https://cdn.example.com/site.css">
    </head><body><main id="main-content"><h1>Test</h1></main></body></html>
    """
    errors = validate_site.validate_html_text(html, tmp_path)
    assert any("remote runtime dependency" in error for error in errors)


def test_css_audit_rejects_gradients_and_missing_reduced_motion():
    errors = validate_site.validate_css_text(
        "body { background: linear-gradient(red, blue); }"
    )
    assert any("gradients" in error for error in errors)
    assert any("reduced-motion" in error for error in errors)


def test_css_audit_rejects_discarded_treatments():
    css = """
    .note {
      border-radius: 50%;
      box-shadow: 5px 6px 0 #111;
      transform: rotate(-2deg);
    }
    """
    errors = validate_site.validate_css_text(css)
    assert any("tilt" in error for error in errors)
    assert any("shadows" in error for error in errors)
    assert any("modest corner radii" in error for error in errors)


def test_visual_language_audit_rejects_superseded_theme():
    errors = validate_site.validate_visual_language(
        "A hand-drawn paper ticket with a doodle.", "DESIGN.md"
    )
    assert errors == [
        "site/DESIGN.md contains rejected visual language: "
        "doodle, hand-drawn, paper, ticket"
    ]


def test_pages_workflow_must_not_initialize_pages_settings():
    workflow = """
    workflow_dispatch:
    branches: [main]
    permissions:
      contents: read
      pages: write
      id-token: write
    concurrency:
      group: pages
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d
      - run: python3 scripts/validate_site.py
      - uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9
        with:
          path: site
      - uses: actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128
        id: deployment
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    """
    errors = validate_site.validate_pages_workflow_text(workflow)
    assert any("must not attempt to initialize" in error for error in errors)


def test_pages_deployment_permissions_must_not_be_global():
    errors = validate_site.validate_pages_workflow_text(
        """name: Unsafe Pages\npermissions:\n  pages: write\n  id-token: write\njobs:\n"""
    )
    assert any("job-scoped" in error for error in errors)
