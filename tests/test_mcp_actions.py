"""Hermetic parity tests for federated GitHub Actions and repository tools."""

import json
import unittest
from unittest.mock import patch

from gway_repo import mcp_actions, mcp_read, mcp_stdio, mcp_workflows

REPO = "arthexis/gway-remote"


def run(run_id, status="completed", conclusion="success"):
    return {
        "id": run_id,
        "name": "CI",
        "workflow_id": 11,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "event": "pull_request",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-11-20T00:00:00Z",
        "updated_at": "2026-11-20T00:00:01Z",
        "html_url": "https://github.com/example",
    }


class GithubMcpMigrationTests(unittest.TestCase):
    def test_complete_tool_catalog_with_explicit_write_scope(self):
        tools = {t["name"]: t for t in mcp_stdio.TOOLS}
        self.assertEqual(len(tools), 16)
        self.assertEqual(
            {name for name, tool in tools.items()
             if tool["annotations"]["readOnlyHint"] is False},
            {"github_workflow_dispatch", "github_workflow_cancel"},
        )
        self.assertEqual(
            set(mcp_stdio._GITHUB_HANDLERS),
            {name for name in tools if name.startswith("github_")},
        )

    def test_workflow_listing_pagination(self):
        with patch.object(
            mcp_actions,
            "_request",
            return_value={
                "total_count": 45,
                "workflows": [{
                    "id": 12,
                    "name": "CI",
                    "path": ".github/workflows/ci.yml",
                    "state": "active",
                }],
            },
        ) as request:
            result = mcp_actions.workflow_list({
                "repository": REPO, "page": 2, "limit": 20
            })
        request.assert_called_once_with(
            REPO, "workflows", {"per_page": 20, "page": 2}
        )
        self.assertTrue(result["pagination"]["has_more"])

    def test_runs_page_filter_and_legacy_default(self):
        with patch.object(mcp_workflows, "_request", return_value={
            "total_count": 41, "workflow_runs": [run(4)]
        }):
            page = mcp_workflows.runs({
                "repository": REPO, "page": 2, "limit": 20,
                "workflow_id": 11
            })
            default = mcp_workflows.runs({"repository": REPO})
        self.assertEqual(page["runs"][0]["id"], 4)
        self.assertTrue(page["pagination"]["has_more"])
        self.assertEqual(set(default), {"runs", "source"})

    def test_health_does_not_claim_global_coverage(self):
        payload = {
            "total_count": 45,
            "workflow_runs": [
                run(1, conclusion="failure"),
                run(2, status="in_progress", conclusion=None),
            ],
        }
        with patch.object(mcp_workflows, "_request", return_value=payload):
            summary = mcp_actions.workflow_health({"repository": REPO})
        self.assertEqual(summary["state"], "failure_observed")
        self.assertEqual(summary["scope"], "recent_runs_only")
        self.assertTrue(summary["older_runs_unchecked"])

    def test_errors_withhold_logs(self):
        with patch.object(mcp_workflows, "status", return_value={
            "jobs": [{
                "id": 8,
                "conclusion": "failure",
                "name": "failed-job",
            }]
        }), patch.object(
            mcp_workflows,
            "_request",
            return_value={
                "id": 8,
                "name": "failed-job",
                "conclusion": "failure",
                "steps": [{"name": "Build", "conclusion": "failure"}],
            },
        ):
            result = mcp_workflows.errors({
                "repository": REPO, "run_id": 42
            })
        self.assertFalse(result["logs_available"])
        self.assertEqual(
            result["failures"][0]["log_excerpt"],
            "withheld_untrusted_build_output",
        )

    def test_branch_rules_array_and_no_deploy(self):
        with patch.object(mcp_read, "_get", return_value=[]):
            result = mcp_read.branch_effective_rules({
                "repository": REPO, "branch": "main"
            })
        self.assertEqual(result["schema"], "github/rules/v1")
        self.assertTrue(result["complete"])
        self.assertNotIn("node_deploy", mcp_stdio._GITHUB_HANDLERS)

    def test_commit_checks_complete_empty(self):
        sha = "a" * 40

        def response(repository, path, params=None):
            self.assertEqual(repository, REPO)
            if path.endswith("/check-suites"):
                return {"total_count": 0, "check_suites": []}
            if path.endswith("/check-runs"):
                return {"total_count": 0, "check_runs": []}
            if path.endswith("/statuses"):
                return []
            raise AssertionError(path)

        with patch.object(mcp_read, "_get", side_effect=response):
            result = mcp_read.commit_checks({
                "repository": REPO, "sha": sha
            })
        self.assertTrue(result["complete"])
        self.assertEqual(result["check_runs"], [])

    def test_dispatch_main_only_and_acceptance_not_deployment(self):
        with patch.object(mcp_actions, "_post") as submit:
            result = mcp_stdio.respond({
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "github_workflow_dispatch",
                    "arguments": {
                        "repository": REPO,
                        "workflow_id": 12,
                        "ref": "main",
                    },
                },
            })
            self.assertIn("result", result)
            data = json.loads(result["result"]["content"][0]["text"])
            self.assertTrue(data["accepted"])
            self.assertFalse(data["execution_verified"])
            submit.assert_called_once()
            submit.reset_mock()
            blocked = mcp_stdio.respond({
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "github_workflow_dispatch",
                    "arguments": {
                        "repository": REPO,
                        "workflow_id": 12,
                        "ref": "unreviewed-branch",
                    },
                },
            })
            self.assertIn("error", blocked)
            submit.assert_not_called()

    def test_cancel_report_is_only_accepted(self):
        with patch.object(mcp_actions, "_post") as submit:
            result = mcp_actions.workflow_cancel({
                "repository": REPO, "run_id": 72
            })
        self.assertTrue(result["accepted"])
        self.assertFalse(result["cancelled_verified"])
        submit.assert_called_once_with(
            REPO, "runs/72/cancel", {}, 202
        )

    def test_cross_owner_rejected_before_network(self):
        with patch.object(mcp_actions, "_post") as submit:
            with self.assertRaisesRegex(
                ValueError, "repository_not_allowed"
            ):
                mcp_actions.workflow_cancel({
                    "repository": "outside/repo", "run_id": 1
                })
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
