"""Discovery and integrity checks for the bundled Hermes operator skill."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = REPO_ROOT / "skills" / "apple" / "hermes-imsg-plugin"
SKILL_MD = SKILL_ROOT / "SKILL.md"


def _frontmatter(text: str) -> str:
    match = re.match(r"\A---\n(?P<body>.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    assert match is not None, "SKILL.md must begin with YAML frontmatter"
    return match.group("body")


def test_bundled_skill_frontmatter_is_discoverable_and_valid():
    assert SKILL_MD.is_file(), "repository skill bundle is missing SKILL.md"
    frontmatter = _frontmatter(SKILL_MD.read_text())

    top_level_keys = re.findall(
        r"^([a-z][a-z0-9-]*):", frontmatter, re.MULTILINE
    )
    assert top_level_keys == [
        "name",
        "description",
        "version",
        "author",
        "license",
        "platforms",
        "metadata",
    ]
    assert re.search(r"^name: hermes-imsg-plugin$", frontmatter, re.MULTILINE)
    assert re.search(r"^description: Use when .+$", frontmatter, re.MULTILINE)
    assert re.search(r"^version: 0\.1\.0$", frontmatter, re.MULTILINE)
    assert re.search(r"^author: Hermes$", frontmatter, re.MULTILINE)
    assert re.search(r"^license: MIT$", frontmatter, re.MULTILINE)
    assert re.search(r"^platforms: \[macos\]$", frontmatter, re.MULTILINE)
    assert frontmatter.count("platforms:") == 1


def test_bundled_skill_reference_links_are_complete_and_resolve():
    assert SKILL_MD.is_file(), "repository skill bundle is missing SKILL.md"
    skill_text = SKILL_MD.read_text()
    linked_paths = {
        match
        for match in re.findall(r"\[[^\]]+\]\((references/[^)#]+\.md)\)", skill_text)
    }
    reference_paths = {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in (SKILL_ROOT / "references").glob("*.md")
    }

    assert linked_paths == reference_paths
    assert linked_paths, "SKILL.md must link its bundled references"
    for relative_path in linked_paths:
        assert (SKILL_ROOT / relative_path).is_file()
