#!/usr/bin/env python3
"""Deterministically validate the static release site and Pages workflows."""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import DefaultDict, Iterable
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_SITE_FILES = ("index.html", "styles.css", "script.js", "favicon.svg", "DESIGN.md")
REQUIRED_SECTION_IDS = {
    "hero",
    "routing",
    "capabilities",
    "install",
    "security",
    "testing",
    "docs",
}
REQUIRED_DOC_LINKS = {
    "https://github.com/promptclickrun/hermes-imsg-cli",
    "https://github.com/promptclickrun/hermes-imsg-cli/blob/main/README.md",
    "https://github.com/promptclickrun/hermes-imsg-cli/blob/main/docs/ARCHITECTURE.md",
    "https://github.com/promptclickrun/hermes-imsg-cli/blob/main/docs/OPERATIONS.md",
    "https://github.com/promptclickrun/hermes-imsg-cli/blob/main/docs/PARITY.md",
    "https://github.com/promptclickrun/hermes-imsg-cli/blob/main/docs/VERIFICATION.md",
    "https://github.com/promptclickrun/hermes-imsg-cli/blob/main/SECURITY.md",
}
REQUIRED_ACTIONS = {
    "actions/checkout",
    "actions/configure-pages",
    "actions/upload-pages-artifact",
    "actions/deploy-pages",
}
REQUIRED_ACTION_REFS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/configure-pages": "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
    "actions/upload-pages-artifact": "fc324d3547104276b827a68afc52ff2a11cc49c9",
    "actions/deploy-pages": "cd2ce8fcbc39b97be8ca5fce6e763baed58fa128",
}
VIEWPORTS = ((1440, 900), (768, 1024), (390, 844), (320, 700))
REJECTED_VISUAL_TERMS = (
    "doodle",
    "field note",
    "field-note",
    "hand-drawn",
    "handmade",
    "marker",
    "paper",
    "playful",
    "scribble",
    "sketch",
    "slip",
    "tape",
    "taped",
    "ticket",
    "wobbl",
)


def _attrs_dict(attrs: Iterable[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value or "" for name, value in attrs}


class SiteHTMLAudit(HTMLParser):
    """Collect the structural and runtime dependency surface from one page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.ids: list[str] = []
        self.fragments: list[str] = []
        self.runtime_urls: list[tuple[str, str]] = []
        self.favicon_urls: list[str] = []
        self.anchor_urls: list[tuple[str, set[str]]] = []
        self.meta: dict[str, str] = {}
        self.html_lang = ""
        self.title_parts: list[str] = []
        self.in_title = False
        self.inline_script_text: list[str] = []
        self.external_script_depth = 0
        self.inline_event_attributes: list[str] = []
        self.button_ids: set[str] = set()
        self.buttons_without_type = 0
        self.chat_choice_count = 0
        self.aria_live_values: list[str] = []
        self.images_without_alt = 0
        self.nav_labels: list[str] = []
        self.regions: DefaultDict[str, list[str]] = defaultdict(list)
        self.region_links: DefaultDict[str, list[str]] = defaultdict(list)
        self.open_elements: list[tuple[str, str]] = []

    def _active_regions(self) -> list[str]:
        return [element_id for _, element_id in self.open_elements if element_id]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = _attrs_dict(attrs)
        self.tags[tag] += 1
        element_id = values.get("id", "")
        if element_id:
            self.ids.append(element_id)
        self.open_elements.append((tag, element_id))

        if tag == "html":
            self.html_lang = values.get("lang", "")
        if tag == "nav":
            self.nav_labels.append(values.get("aria-label") or values.get("aria-labelledby", ""))
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = values.get("name") or values.get("http-equiv")
            if key:
                self.meta[key.lower()] = values.get("content", "")

        for name in values:
            if name.startswith("on"):
                self.inline_event_attributes.append(f"{tag}[{name}]")

        if tag == "a":
            href = values.get("href", "")
            rel = set(values.get("rel", "").lower().split())
            if href.startswith("#") and len(href) > 1:
                self.fragments.append(href[1:])
            if href:
                self.anchor_urls.append((href, rel))
                for region in self._active_regions():
                    self.region_links[region].append(href)

        if tag == "script":
            src = values.get("src", "")
            if src:
                self.runtime_urls.append(("script", src))
                self.external_script_depth += 1
        if tag == "link":
            rel_values = set(values.get("rel", "").lower().split())
            if rel_values.intersection({"stylesheet", "icon", "preload", "modulepreload"}):
                self.runtime_urls.append(("link", values.get("href", "")))
            if "icon" in rel_values:
                self.favicon_urls.append(values.get("href", ""))
        if tag in {"img", "source", "video", "audio"}:
            for attribute in ("src", "srcset", "poster"):
                if values.get(attribute):
                    self.runtime_urls.append((tag, values[attribute]))
        if tag == "img" and "alt" not in values:
            self.images_without_alt += 1
        if tag == "button":
            if values.get("type") != "button":
                self.buttons_without_type += 1
            if element_id:
                self.button_ids.add(element_id)
            if values.get("data-chat-id"):
                self.chat_choice_count += 1
        if values.get("aria-live"):
            self.aria_live_values.append(values["aria-live"].lower())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.external_script_depth:
            self.external_script_depth -= 1
        for index in range(len(self.open_elements) - 1, -1, -1):
            if self.open_elements[index][0] == tag:
                del self.open_elements[index:]
                break

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.open_elements and self.open_elements[-1][0] == "script" and not self.external_script_depth:
            if data.strip():
                self.inline_script_text.append(data)
        if data.strip():
            for region in self._active_regions():
                self.regions[region].append(data)


def _is_remote_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return bool(parsed.scheme or parsed.netloc or value.strip().startswith("//"))


def _local_asset_error(site_dir: Path, value: str) -> str | None:
    clean = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean or clean.startswith("#"):
        return None
    if clean.startswith("/"):
        return f"root-relative runtime asset is not project-Pages safe: {value}"
    candidate = (site_dir / clean).resolve()
    try:
        candidate.relative_to(site_dir.resolve())
    except ValueError:
        return f"runtime asset escapes site/: {value}"
    if not candidate.is_file():
        return f"local runtime asset does not exist: {value}"
    return None


def validate_html_text(text: str, site_dir: Path) -> list[str]:
    errors: list[str] = []
    audit = SiteHTMLAudit()
    try:
        audit.feed(text)
        audit.close()
    except Exception as exc:
        return [f"index.html could not be parsed: {exc}"]

    if audit.html_lang.lower() != "en":
        errors.append("index.html must declare html lang=\"en\"")
    if not "".join(audit.title_parts).strip():
        errors.append("index.html must have a non-empty title")
    for meta_name in ("description", "viewport", "referrer", "content-security-policy"):
        if not audit.meta.get(meta_name):
            errors.append(f"index.html is missing {meta_name} metadata")
    csp = audit.meta.get("content-security-policy", "").lower()
    for directive in ("default-src 'self'", "connect-src 'none'", "object-src 'none'", "base-uri 'none'"):
        if directive not in csp:
            errors.append(f"content security policy is missing {directive}")

    for tag in ("header", "main", "footer"):
        if audit.tags[tag] != 1:
            errors.append(f"index.html must contain exactly one {tag} landmark")
    if not audit.tags["nav"]:
        errors.append("index.html must contain at least one nav landmark")
    if any(not label for label in audit.nav_labels):
        errors.append("every nav landmark must have an accessible label")
    if audit.tags["h1"] != 1:
        errors.append("index.html must contain exactly one h1")
    if "main-content" not in audit.ids:
        errors.append("main landmark must expose id=\"main-content\" for the skip link")

    duplicate_ids = sorted(element_id for element_id, count in Counter(audit.ids).items() if count > 1)
    if duplicate_ids:
        errors.append("duplicate HTML ids: " + ", ".join(duplicate_ids))
    missing_fragments = sorted(set(audit.fragments) - set(audit.ids))
    if missing_fragments:
        errors.append("fragment links have no target: " + ", ".join(missing_fragments))
    missing_sections = sorted(REQUIRED_SECTION_IDS - set(audit.ids))
    if missing_sections:
        errors.append("required site sections are missing: " + ", ".join(missing_sections))

    hero_text = " ".join(audit.regions.get("hero", [])).lower()
    for phrase in ("native imessage", "sms", "stock hermes", "exact messages chat rowid", "macos"):
        if phrase not in hero_text:
            errors.append(f"first viewport is missing required phrase: {phrase}")
    hero_links = set(audit.region_links.get("hero", []))
    if "#install" not in hero_links:
        errors.append("first viewport must contain a visible install action")
    if "https://github.com/promptclickrun/hermes-imsg-cli" not in hero_links:
        errors.append("first viewport must contain a visible source action")

    if "route-demo-run" not in audit.button_ids:
        errors.append("routing demo must provide the route-demo-run button")
    if audit.chat_choice_count < 3:
        errors.append("routing demo must provide at least three exact chat choices")
    if "polite" not in audit.aria_live_values:
        errors.append("routing demo must expose a polite live region")
    if audit.buttons_without_type:
        errors.append("every button must declare type=\"button\"")
    if audit.images_without_alt:
        errors.append("every image must provide an alt attribute")
    if "favicon.svg" not in audit.favicon_urls:
        errors.append("index.html must provide a local favicon link to favicon.svg")
    if audit.inline_script_text:
        errors.append("inline scripts are forbidden by the self-contained CSP")
    if audit.inline_event_attributes:
        errors.append("inline event handlers are forbidden: " + ", ".join(audit.inline_event_attributes))

    for kind, url in audit.runtime_urls:
        if _is_remote_url(url):
            errors.append(f"remote runtime dependency in {kind}: {url}")
            continue
        asset_error = _local_asset_error(site_dir, url)
        if asset_error:
            errors.append(asset_error)

    anchors = {url for url, _ in audit.anchor_urls}
    missing_docs = sorted(REQUIRED_DOC_LINKS - anchors)
    if missing_docs:
        errors.append("repository documentation links are missing: " + ", ".join(missing_docs))
    for url, rel in audit.anchor_urls:
        if url.startswith(("https://", "http://")) and "noreferrer" not in rel:
            errors.append(f"external link must use rel=noreferrer: {url}")
        if url.startswith("http://"):
            errors.append(f"insecure external link is not allowed: {url}")

    return errors


def validate_css_text(text: str) -> list[str]:
    errors: list[str] = []
    lowered = text.lower()
    if "gradient(" in lowered:
        errors.append("styles.css must not use gradients")
    if "backdrop-filter" in lowered or re.search(r"filter\s*:\s*blur", lowered):
        errors.append("styles.css must not use glass or blur effects")
    if re.search(r"transform\s*:[^;{}]*rotate\s*\(", lowered):
        errors.append("styles.css must not tilt elements")
    if "box-shadow" in lowered:
        errors.append("styles.css must not use handmade or ornamental shadows")
    for declaration in re.findall(r"border-radius\s*:\s*([^;{}]+)", lowered):
        pixel_values = [float(value) for value in re.findall(r"([0-9]+(?:\.[0-9]+)?)px", declaration)]
        rem_values = [float(value) for value in re.findall(r"([0-9]+(?:\.[0-9]+)?)rem", declaration)]
        if "%" in declaration or any(value > 12 for value in pixel_values) or any(value > 0.75 for value in rem_values):
            errors.append("styles.css must use only square or modest corner radii")
            break
    if "@import" in lowered or re.search(r"url\(\s*['\"]?https?://", lowered):
        errors.append("styles.css must not load remote runtime dependencies")
    if "@media (prefers-reduced-motion: reduce)" not in lowered:
        errors.append("styles.css is missing reduced-motion support")
    if ":focus-visible" not in lowered:
        errors.append("styles.css is missing keyboard focus-visible styling")
    if "--touch-target: 44px" not in lowered or "min-height: var(--touch-target)" not in lowered:
        errors.append("styles.css must enforce 44px touch targets")
    if "overflow-x: clip" not in lowered and "overflow-x: hidden" not in lowered:
        errors.append("styles.css must guard against page-level horizontal overflow")
    if "minmax(0, 1fr)" not in lowered:
        errors.append("responsive grids must use minmax(0, 1fr) to prevent overflow")
    if "@media (max-width:" not in lowered:
        errors.append("styles.css must include a narrow-screen responsive breakpoint")
    return errors


def _braced_body(text: str, marker: str) -> str:
    marker_index = text.find(marker)
    if marker_index < 0:
        return ""
    open_index = text.find("{", marker_index + len(marker))
    if open_index < 0:
        return ""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:index]
    return ""


def _rule_body(scope: str, selector: str) -> str:
    bodies: list[str] = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", scope):
        selectors = [value.strip() for value in match.group(1).split(",")]
        if selector in selectors:
            bodies.append(match.group(2))
    return "\n".join(bodies)


def _has_declarations(scope: str, selector: str, *fragments: str) -> bool:
    body = _rule_body(scope, selector)
    normalized = re.sub(r"\s+", " ", body).strip()
    return bool(body) and all(fragment in normalized for fragment in fragments)


def validate_viewport_contracts(html: str, css: str) -> list[str]:
    """Audit deterministic DOM/CSS containment contracts at release viewports."""
    errors: list[str] = []
    media_980 = _braced_body(css, "@media (max-width: 980px)")
    media_720 = _braced_body(css, "@media (max-width: 720px)")
    media_520 = _braced_body(css, "@media (max-width: 520px)")
    media_350 = _braced_body(css, "@media (max-width: 350px)")

    common_checks = (
        (_has_declarations(css, "body", "overflow-x: clip"), "page-level horizontal overflow is not clipped"),
        (_has_declarations(css, ".page-width", "width: min(100%, var(--max-width))", "padding-inline: var(--page-pad)"), "content width is not fluid and bounded"),
        (_has_declarations(css, ".hero-route", "margin-inline: 0", "margin-bottom: 0"), "hero route must reset figure inline margins while preserving its bottom margin"),
        (_has_declarations(css, ".button", "min-height: var(--touch-target)"), "primary controls do not retain the 44px target"),
        (_has_declarations(css, ".code-block pre", "overflow-x: auto"), "installation code cannot contain its own overflow"),
        (_has_declarations(css, ".route-output", "min-width: 0"), "routing output can force its grid wider than the viewport"),
        (_has_declarations(css, ".footer-content a", "overflow-wrap: anywhere"), "repository footer link cannot wrap"),
        ('<meta name="viewport" content="width=device-width, initial-scale=1">' in html, "responsive viewport metadata is missing"),
        ('id="route-demo-run"' in html and 'aria-live="polite"' in html, "routing interaction state is incomplete"),
    )

    for width, height in VIEWPORTS:
        label = f"viewport {width}x{height}"
        for passed, message in common_checks:
            if not passed:
                errors.append(f"{label}: {message}")

        if width > 980:
            if not _has_declarations(
                css,
                ".hero",
                "grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr)",
                "min-height: calc(100svh - 4.25rem)",
            ):
                errors.append(f"{label}: wide hero does not preserve its two-column first view")

        if width <= 980:
            if not _has_declarations(media_980, ".hero", "grid-template-columns: minmax(0, 1fr)", "min-height: auto"):
                errors.append(f"{label}: hero does not reflow to one column")
            if not _has_declarations(media_980, ".routing-interface", "grid-template-columns: minmax(0, 1fr)"):
                errors.append(f"{label}: routing interface does not reflow to one column")

        if 720 < width <= 980:
            if not _has_declarations(media_980, ".chat-choice-list", "grid-template-columns: repeat(3, minmax(0, 1fr))"):
                errors.append(f"{label}: routing choices do not use the tablet layout")

        if width <= 720:
            if not _has_declarations(media_720, ".capability-table thead", "clip-path: inset(50%)"):
                errors.append(f"{label}: capability headers do not collapse accessibly")
            if not _has_declarations(media_720, ".capability-table td", "grid-template-columns: 7.5rem minmax(0, 1fr)"):
                errors.append(f"{label}: capability rows do not reflow for narrow screens")
            if not _has_declarations(media_720, ".security-list > div", "grid-template-columns: minmax(0, 1fr)"):
                errors.append(f"{label}: security rows do not reflow for narrow screens")

        if width <= 520:
            if not _has_declarations(media_520, ".hero-actions", "flex-direction: column"):
                errors.append(f"{label}: hero actions do not stack")
            if not _has_declarations(media_520, ".configuration-note", "border-radius: 0"):
                errors.append(f"{label}: configuration region can exceed the content edge")
            normalized_520 = re.sub(r"\s+", " ", media_520)
            if ".code-block," not in normalized_520 or "margin-left: 0" not in normalized_520:
                errors.append(f"{label}: installation code retains a desktop offset")

        if width <= 350:
            if not _has_declarations(media_350, ".chat-choice", "grid-template-columns: minmax(0, 1fr)"):
                errors.append(f"{label}: routing choices do not collapse at 320px")
            if not _has_declarations(media_350, ".support-links", "grid-template-columns: minmax(0, 1fr)"):
                errors.append(f"{label}: support links do not collapse at 320px")

    return errors


def validate_javascript_text(text: str) -> list[str]:
    errors: list[str] = []
    for pattern, label in (
        (r"\bfetch\s*\(", "fetch"),
        (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
        (r"\bWebSocket\b", "WebSocket"),
        (r"\bEventSource\b", "EventSource"),
        (r"\.innerHTML\b", "innerHTML"),
        (r"\binsertAdjacentHTML\b", "insertAdjacentHTML"),
        (r"\beval\s*\(", "eval"),
    ):
        if re.search(pattern, text):
            errors.append(f"script.js must not use {label}")
    for required in ("addEventListener", "textContent", "aria-pressed"):
        if required not in text:
            errors.append(f"script.js is missing accessible interaction primitive: {required}")
    return errors


def is_pinned_official_action(reference: str) -> bool:
    match = re.fullmatch(r"(actions/[a-z0-9-]+)@([0-9a-f]{40})", reference.strip())
    return bool(match)


def validate_pages_workflow_text(text: str) -> list[str]:
    errors: list[str] = []
    lowered = text.lower()
    workflow_header = lowered.split("\njobs:", 1)[0]
    if "workflow_dispatch:" not in text:
        errors.append("Pages workflow must support workflow_dispatch")
    if not re.search(r"branches:\s*\[main\]", text):
        errors.append("Pages workflow must deploy pushes from main")
    if "pull_request_target" in lowered:
        errors.append("Pages workflow must not use pull_request_target")
    for permission in ("contents: read", "pages: read", "pages: write", "id-token: write"):
        if permission not in text:
            errors.append(f"Pages workflow is missing permission: {permission}")
    if "pages: write" in workflow_header or "id-token: write" in workflow_header:
        errors.append("Pages write and id-token permissions must be job-scoped to deploy")
    def job_body(name: str) -> str:
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\s*\n(?P<body>.*?)(?=^  [a-z0-9_-]+:\s*\n|\Z)",
            lowered,
        )
        return match.group("body") if match else ""

    build_body = job_body("build")
    if not re.search(r"^    permissions:\s*$", build_body, re.MULTILINE):
        errors.append("Pages build job must declare its own permissions")
    for permission in ("contents: read", "pages: read"):
        if permission not in build_body:
            errors.append(f"Pages build job is missing permission: {permission}")

    deploy_body = job_body("deploy")
    if not re.search(r"^    permissions:\s*$", deploy_body, re.MULTILINE):
        errors.append("Pages deploy job must declare its own permissions")
    for permission in ("pages: write", "id-token: write"):
        if permission not in deploy_body:
            errors.append(f"Pages deploy job is missing permission: {permission}")
    if "group: pages" not in text or "cancel-in-progress: false" not in lowered:
        errors.append("Pages workflow must serialize deployments without cancelling in-flight releases")
    if "python3 scripts/validate_site.py" not in text:
        errors.append("Pages workflow must run deterministic site validation before upload")
    if not re.search(r"enablement:\s*false\s*$", lowered, re.MULTILINE):
        errors.append("Pages workflow must not attempt to initialize repository Pages settings")
    if not re.search(r"path:\s*site\s*$", text, re.MULTILINE):
        errors.append("Pages artifact upload must publish site/")
    if "name: github-pages" not in text or "steps.deployment.outputs.page_url" not in text:
        errors.append("Pages deployment environment or published URL output is missing")

    references = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.MULTILINE)
    action_names: set[str] = set()
    for reference in references:
        if not is_pinned_official_action(reference):
            errors.append(f"workflow action is not an official full-SHA pin: {reference}")
            continue
        action_name = reference.split("@", 1)[0]
        action_names.add(action_name)
        expected_sha = REQUIRED_ACTION_REFS.get(action_name)
        if expected_sha and reference != f"{action_name}@{expected_sha}":
            errors.append(f"workflow action does not use the reviewed release pin: {action_name}")
    missing_actions = sorted(REQUIRED_ACTIONS - action_names)
    if missing_actions:
        errors.append("Pages workflow is missing actions: " + ", ".join(missing_actions))
    return errors


def validate_design_text(text: str) -> list[str]:
    errors: list[str] = []
    lowered = text.lower()
    for heading in ("visual principles", "palette", "typography", "composition", "interaction", "accessibility", "content authority"):
        if heading not in lowered:
            errors.append(f"DESIGN.md is missing authority section: {heading}")
    if len(text.split()) > 1400:
        errors.append("DESIGN.md must remain concise (1400 words maximum)")
    return errors


def validate_visual_language(text: str, filename: str) -> list[str]:
    """Reject the superseded visual vocabulary across the shipped site."""
    lowered = text.lower()
    matches = sorted(term for term in REJECTED_VISUAL_TERMS if term in lowered)
    if matches:
        return [f"site/{filename} contains rejected visual language: " + ", ".join(matches)]
    return []


def validate_repository(repo_root: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    root = Path(repo_root).resolve()
    site_dir = root / "site"
    for name in REQUIRED_SITE_FILES:
        if not (site_dir / name).is_file():
            errors.append(f"missing required site file: site/{name}")
    if errors:
        return errors

    texts: dict[str, str] = {}
    for name in REQUIRED_SITE_FILES:
        try:
            texts[name] = (site_dir / name).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"site/{name} is not readable UTF-8: {exc}")
    if errors:
        return errors

    for name, text in texts.items():
        if "\u2014" in text:
            errors.append(f"site/{name} contains an em dash")
        errors.extend(validate_visual_language(text, name))
    errors.extend(validate_html_text(texts["index.html"], site_dir))
    errors.extend(validate_css_text(texts["styles.css"]))
    errors.extend(validate_viewport_contracts(texts["index.html"], texts["styles.css"]))
    errors.extend(validate_javascript_text(texts["script.js"]))
    errors.extend(validate_design_text(texts["DESIGN.md"]))

    pages_workflow = root / ".github" / "workflows" / "pages.yml"
    if not pages_workflow.is_file():
        errors.append("missing .github/workflows/pages.yml")
    else:
        errors.extend(validate_pages_workflow_text(pages_workflow.read_text(encoding="utf-8")))

    ci_workflow = root / ".github" / "workflows" / "ci.yml"
    if not ci_workflow.is_file():
        errors.append("missing .github/workflows/ci.yml")
    elif "python scripts/validate_site.py" not in ci_workflow.read_text(encoding="utf-8"):
        errors.append("existing CI must run python scripts/validate_site.py")

    return errors


def main() -> int:
    errors = validate_repository(REPO_ROOT)
    if errors:
        print(f"site validation: {len(errors)} error(s)")
        for error in errors:
            print(f"  - {error}")
        return 1
    viewports = ", ".join(f"{width}x{height}" for width, height in VIEWPORTS)
    print(f"site validation: PASS (viewport contracts: {viewports})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
