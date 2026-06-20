"""Guardrails for the Phase 1 defending-code optional skills."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "optional-skills" / "security"

SKILL_NAMES = [
    "defending-code-quickstart",
    "defending-code-threat-model",
    "defending-code-vuln-scan",
    "defending-code-vuln-triage",
    "defending-code-vuln-patch",
    "defending-code-customize-plan",
]

STATIC_CHILD_SKILLS = {
    "defending-code-quickstart",
    "defending-code-threat-model",
    "defending-code-vuln-scan",
    "defending-code-vuln-triage",
    "defending-code-vuln-patch",
    "defending-code-customize-plan",
}

PARALLEL_REVIEW_SKILLS = {
    "defending-code-threat-model",
    "defending-code-vuln-scan",
    "defending-code-vuln-triage",
}

SECTION_ORDER = [
    "## When to Use",
    "## Prerequisites",
    "## How to Run",
    "## Quick Reference",
    "## Procedure",
    "## Pitfalls",
    "## Verification",
]

CLAUDE_TOOL_NAMES = [
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Task",
    "AskUserQuestion",
    "Bash",
]

EXECUTION_SNIPPETS = [
    "vuln-pipeline patch",
    "vuln-pipeline run",
    "docker",
    "git apply",
    "pytest",
    "npm test",
]


def _skill_path(name: str) -> Path:
    return SKILL_ROOT / name / "SKILL.md"


@pytest.fixture(scope="module", params=SKILL_NAMES)
def skill_doc(request: pytest.FixtureRequest) -> tuple[str, str, dict]:
    name = str(request.param)
    text = _skill_path(name).read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{name} missing YAML frontmatter"
    frontmatter = yaml.safe_load(match.group(1))
    return name, text, frontmatter


def test_skill_files_exist() -> None:
    for name in SKILL_NAMES:
        assert _skill_path(name).is_file()


def test_frontmatter_name_matches_directory(skill_doc: tuple[str, str, dict]) -> None:
    name, _text, frontmatter = skill_doc
    assert frontmatter["name"] == name


def test_descriptions_are_hardline(skill_doc: tuple[str, str, dict]) -> None:
    name, _text, frontmatter = skill_doc
    description = frontmatter["description"]
    assert len(description) <= 60, f"{name} description too long"
    assert description.endswith(".")


def test_author_credits_upstream_first(skill_doc: tuple[str, str, dict]) -> None:
    _name, _text, frontmatter = skill_doc
    assert str(frontmatter["author"]).startswith("Anthropic")
    assert frontmatter["license"] == "Apache-2.0"


def test_modern_section_order(skill_doc: tuple[str, str, dict]) -> None:
    name, text, _frontmatter = skill_doc
    positions = []
    for section in SECTION_ORDER:
        pos = text.find(section)
        assert pos >= 0, f"{name} missing {section}"
        positions.append(pos)
    assert positions == sorted(positions), f"{name} section order is wrong"


def test_no_collision_prone_skill_names() -> None:
    for bad_name in {"patch", "triage", "threat-model", "vuln-scan", "quickstart"}:
        assert not (SKILL_ROOT / bad_name / "SKILL.md").exists()


def test_hermes_tool_names_not_claude_tool_names(skill_doc: tuple[str, str, dict]) -> None:
    name, text, _frontmatter = skill_doc
    body = text.split("---\n", 2)[-1]
    for tool_name in CLAUDE_TOOL_NAMES:
        assert re.search(rf"\b{re.escape(tool_name)}\b", body) is None, (
            f"{name} uses Claude tool name {tool_name!r}"
        )


def test_static_skills_use_repo_read_delegate_task(
    skill_doc: tuple[str, str, dict],
) -> None:
    name, text, _frontmatter = skill_doc
    if name not in STATIC_CHILD_SKILLS:
        return
    assert "delegate_task" in text
    assert 'toolsets=["repo-read"]' in text or '"toolsets": ["repo-read"]' in text
    assert 'toolsets=["file"]' not in text
    assert 'toolsets=["search"]' not in text


def test_parallel_review_skills_show_batch_mode(
    skill_doc: tuple[str, str, dict],
) -> None:
    name, text, _frontmatter = skill_doc
    if name not in PARALLEL_REVIEW_SKILLS:
        return
    assert "tasks=[" in text
    assert '"toolsets": ["repo-read"]' in text


def test_static_skills_do_not_include_execution_snippets(
    skill_doc: tuple[str, str, dict],
) -> None:
    name, text, _frontmatter = skill_doc
    for snippet in EXECUTION_SNIPPETS:
        assert snippet not in text, f"{name} includes execution snippet {snippet!r}"


def test_patch_skill_only_writes_patch_artifacts() -> None:
    text = _skill_path("defending-code-vuln-patch").read_text(encoding="utf-8")
    assert "PATCHES/" in text
    assert "No target source file is modified." in text
    assert "Do not edit target source files from this skill." in text
