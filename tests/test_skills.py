"""Check that the shipped Commonplace Skills are discoverable and use real interfaces."""

from __future__ import annotations

import shutil
from pathlib import Path

from commonplace.cli import _parser
from commonplace.mcp_server import _tool_name
from commonplace.models import OPERATIONS

ROOT = Path(__file__).parents[1]
SKILLS = ("commonplace-worker", "commonplace-supervisor")


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    result: dict[str, str] = {}
    for line in lines[1:end]:
        key, value = line.split(":", 1)
        result[key] = value.strip()
    return result


def test_both_skills_discover_from_a_clean_consuming_repository(tmp_path):
    consuming_repo = tmp_path / "research-repo"
    skills_dir = consuming_repo / ".agents" / "skills"
    skills_dir.mkdir(parents=True)

    for skill_name in SKILLS:
        shutil.copytree(ROOT / ".agents" / "skills" / skill_name, skills_dir / skill_name)

    discovered = sorted(path.name for path in skills_dir.iterdir() if (path / "SKILL.md").is_file())
    assert discovered == sorted(SKILLS)
    for skill_name in SKILLS:
        metadata = _frontmatter(skills_dir / skill_name / "SKILL.md")
        assert metadata["name"] == skill_name
        assert metadata["description"]


def test_skill_examples_name_real_mcp_operations_and_cli_commands():
    worker = (ROOT / ".agents/skills/commonplace-worker/SKILL.md").read_text()
    supervisor = (ROOT / ".agents/skills/commonplace-supervisor/SKILL.md").read_text()

    for tool_name in (
        "commonplace_context",
        "commonplace_add_evidence",
        "commonplace_lease_next",
        "commonplace_renew_lease",
        "commonplace_complete_request",
        "commonplace_release_lease",
    ):
        operation = (
            "lease_next_request"
            if tool_name == "commonplace_lease_next"
            else tool_name.removeprefix("commonplace_")
        )
        assert operation in OPERATIONS
        assert _tool_name(operation) == tool_name
        assert tool_name in worker

    for tool_name in (
        "commonplace_project_snapshot",
        "commonplace_recent_activity",
        "commonplace_search",
        "commonplace_get_claim",
        "commonplace_list_leases",
        "commonplace_set_claim_status",
        "commonplace_supersede_claim",
        "commonplace_cancel_request",
        "commonplace_set_project_archived",
    ):
        operation = tool_name.removeprefix("commonplace_")
        assert operation in OPERATIONS
        assert _tool_name(operation) == tool_name
        assert tool_name in supervisor

    parser = _parser()
    for args in (
        ["context", "--json"],
        ["search", "address", "--json"],
        ["evidence", "add", "17", "--supports", "--method", "watchpoint", "summary"],
        ["request", "next", "--json"],
        ["request", "renew", "--json"],
        ["request", "complete", "--json"],
        ["request", "release", "--json"],
        ["snapshot", "--json"],
        ["recent", "--since", "0", "--json"],
        ["claim", "status", "--json"],
        ["claim", "supersede", "--json"],
        ["request", "cancel", "--json"],
        ["project", "archive", "--json"],
        ["project", "unarchive", "--json"],
    ):
        parser.parse_args(args)


def test_skills_keep_recovery_and_untrusted_data_guidance():
    for skill_name in SKILLS:
        content = (ROOT / ".agents" / "skills" / skill_name / "SKILL.md").read_text().lower()
        assert "outcome_unknown" in content
        assert "generation" in content
        assert "origin_host" in content
        assert "private" in content
        assert "untrusted" in content
