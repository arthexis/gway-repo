"""Verify that the single CI check cannot silently omit critical suites."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
COMPONENTS = {
    "quality.yml",
    "package.yml",
    "python-compatibility.yml",
    "clean-install.yml",
    "provider-ansible.yml",
}


def test_one_ci_entrypoint_and_complete_gate():
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert ci.startswith("name: CI\n")
    assert "\n  pull_request:\n" in ci
    assert "\n    name: CI\n" in ci
    assert "\n    if: always()\n" in ci
    assert "if job.get('result') != 'success'" in ci
    for filename in COMPONENTS:
        assert f"uses: ./.github/workflows/{filename}" in ci


def test_components_only_run_through_ci():
    for filename in COMPONENTS:
        workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
        assert "\non:\n  workflow_call:\n" in workflow
        assert "\n  pull_request:" not in workflow
        assert "\n  push:" not in workflow
