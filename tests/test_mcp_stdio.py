"""Hermetic MCP stdio and existing GitHub identity contracts."""

import io
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from gway_repo import mcp_stdio
from gway_repo.auth import github_token


class McpStdioTests(unittest.TestCase):
    def test_initialize_and_catalog(self):
        result = mcp_stdio.respond(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        self.assertEqual(result["result"]["protocolVersion"], "2025-03-26")
        catalog = mcp_stdio.respond(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )["result"]["tools"]
        self.assertEqual(len(catalog), 7)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in catalog))
        self.assertEqual(len({t["name"] for t in catalog}), len(catalog))

    def test_bounded_pr(self):
        with patch.object(
            mcp_stdio, "pull_request_state", return_value={"number": 5}
        ) as call:
            response = mcp_stdio.respond(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "pr",
                        "arguments": {"repository": "arthexis/gway-repo", "number": 5},
                    },
                }
            )
        call.assert_called_once_with(5, repository="arthexis/gway-repo")
        self.assertEqual(
            json.loads(response["result"]["content"][0]["text"]), {"number": 5}
        )

    def test_context_cannot_inspect_files(self):
        with patch.object(mcp_stdio, "context_state", return_value={}) as call:
            mcp_stdio.respond(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "context",
                        "arguments": {"repository": "arthexis/gway-repo", "number": 5},
                    },
                }
            )
        call.assert_called_once_with(
            pr=5, repository="arthexis/gway-repo", include_impact=False
        )

    def test_unauthorized_and_unknown_calls_rejected(self):
        for name, arguments in (
            ("node_deploy", {"repository": "arthexis/gway-repo", "number": 1}),
            ("pr", {"repository": "outside/secret", "number": 1}),
            ("pr", {"repository": "arthexis/gway-repo", "number": True}),
            ("pr", {"repository": "arthexis/gway-repo", "number": 1, "path": "/etc"}),
            ("code", {"repository": "arthexis/gway-repo", "number": 1}),
        ):
            with self.subTest(name=name, arguments=arguments):
                result = mcp_stdio.respond(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }
                )
                self.assertIn("error", result)
        self.assertIsNone(
            mcp_stdio.respond({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_upstream_errors_never_leak(self):
        with patch.object(
            mcp_stdio,
            "pull_request_state",
            side_effect=RuntimeError("secret-looking traceback"),
        ):
            result = mcp_stdio.respond(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "pr",
                        "arguments": {"repository": "arthexis/gway-repo", "number": 1},
                    },
                }
            )
        self.assertNotIn("secret-looking", json.dumps(result))

    def test_stdio_exchange(self):
        output = io.StringIO()
        mcp_stdio.serve(
            io.StringIO('{"jsonrpc":"2.0","id":3,"method":"tools/list"}\n'), output
        )
        self.assertEqual(json.loads(output.getvalue())["id"], 3)


class ExistingGithubIdentityTests(unittest.TestCase):
    @patch("gway_repo.auth.subprocess.run")
    def test_reuse_gh_login(self, run):
        run.return_value = Mock(returncode=0, stdout="existing-token\n")
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("gway_repo.auth.Path.is_file", return_value=True),
        ):
            self.assertEqual(github_token(), "existing-token")
        self.assertEqual(run.call_args.args[0][-3:], ["token", "-h", "github.com"])

    @patch("gway_repo.auth.subprocess.run")
    def test_environment_preferred(self, run):
        with patch.dict("os.environ", {"GH_TOKEN": "existing-token"}):
            self.assertEqual(github_token(), "existing-token")
        run.assert_not_called()

    @patch("gway_repo.auth.subprocess.run")
    def test_login_timeout_not_leaked(self, run):
        run.side_effect = subprocess.TimeoutExpired("gh", 3)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("gway_repo.auth.Path.is_file", return_value=True),
        ):
            self.assertIsNone(github_token())


if __name__ == "__main__":
    unittest.main()
