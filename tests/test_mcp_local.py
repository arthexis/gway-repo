"""Hermetic local MCP boundaries: fixed repository roots, index-only source, limits."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gway_repo import mcp_local, mcp_stdio


class LocalIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        commands = [
            ["git", "init", "-q", str(self.root)],
            ["git", "-C", str(self.root), "config", "user.name", "Test"],
            ["git", "-C", str(self.root), "config", "user.email", "t@example.invalid"],
            [
                "git",
                "-C",
                str(self.root),
                "remote",
                "add",
                "origin",
                "https://github.com/arthexis/gway-remote.git",
            ],
        ]
        for args in commands:
            subprocess.run(args, check=True, capture_output=True)
        src = self.root / "src"
        src.mkdir()
        (src / "example.py").write_text(
            "def greeting():\n    return 'tracked'\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "src/example.py"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "tracked"],
            check=True,
            capture_output=True,
        )
        self.workspace = patch.object(mcp_local, "_root_for", return_value=self.root)
        self.workspace.start()
        self.addCleanup(self.workspace.stop)

    @staticmethod
    def rpc(name, arguments):
        return mcp_stdio.respond(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def test_catalog_is_read_only_and_schema_constrained(self):
        by_name = {x["name"]: x for x in mcp_stdio.TOOLS}
        for name in ("map", "files", "symbols", "relations", "impact", "code"):
            declaration = by_name[name]
            self.assertTrue(declaration["annotations"]["readOnlyHint"])
            schema = declaration["inputSchema"]
            self.assertEqual(schema["required"], ["repository"])
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("path", schema["properties"])
            self.assertEqual(len(schema["properties"]["repository"]["enum"]), 3)

    def test_index_source_and_workspace_provenance(self):
        args = {"repository": "arthexis/gway-remote"}
        response = self.rpc("code", {**args, "file": "src/example.py"})
        self.assertNotIn("error", response)
        body = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("tracked", json.dumps(body))
        self.assertEqual(body["workspace"]["repository"], args["repository"])
        self.assertEqual(len(body["workspace"]["head"]), 40)
        self.assertNotIn(str(self.root), json.dumps(body))
        # Worktree modifications must never supersede the indexed blob.
        (self.root / "src/example.py").write_text(
            "secret worktree edit", encoding="utf-8"
        )
        again = self.rpc("code", {**args, "file": "src/example.py"})
        self.assertNotIn("error", again)
        self.assertNotIn("secret worktree edit", json.dumps(again))

    def test_all_six_local_operations_are_dispatched(self):
        repo = {"repository": "arthexis/gway-remote"}
        cases = (
            ("map", {}),
            ("files", {}),
            ("symbols", {"file": "src/example.py"}),
            ("relations", {"file": "src/example.py"}),
            ("impact", {"file": "src/example.py"}),
            ("code", {"symbol": "greeting"}),
        )
        for name, args in cases:
            with self.subTest(name=name):
                response = self.rpc(name, {**repo, **args})
                self.assertNotIn("error", response, str(response))
                payload = json.loads(response["result"]["content"][0]["text"])
                self.assertEqual(payload["workspace"]["repository"], repo["repository"])

    def test_reject_bad_repository_paths_and_oversized_limits(self):
        invalid = (
            ("map", {"repository": "arthexis/unknown"}),
            ("map", {"repository": "outside/secret"}),
            ("map", {"repository": "arthexis/gway-remote", "path": "/etc"}),
            ("map", {"repository": "arthexis/gway-remote", "file_limit": True}),
            ("map", {"repository": "arthexis/gway-remote", "file_limit": 100000}),
            ("code", {"repository": "arthexis/gway-remote", "file": "../passwd"}),
            ("code", {"repository": "arthexis/gway-remote", "file": "/etc/shadow"}),
            (
                "code",
                {"repository": "arthexis/gway-remote", "file": "src/./example.py"},
            ),
            ("code", {"repository": "arthexis/gway-remote", "symbol": "a; rm -rf /"}),
            (
                "code",
                {
                    "repository": "arthexis/gway-remote",
                    "max_chars": 40000,
                    "file": "src/example.py",
                },
            ),
            ("impact", {"repository": "arthexis/gway-remote", "pr": 1, "issue": 2}),
        )
        for name, args in invalid:
            with self.subTest(name=name, args=args):
                response = self.rpc(name, args)
                self.assertIn("error", response)
                self.assertNotIn(str(self.root), json.dumps(response))

    def test_reject_symlink_workspace_or_changed_identity(self):
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(mcp_local, "_root_for", return_value=alias):
            self.assertIn(
                "error", self.rpc("files", {"repository": "arthexis/gway-remote"})
            )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "remote",
                "set-url",
                "origin",
                "https://github.com/outsider/secret.git",
            ],
            check=True,
            capture_output=True,
        )
        self.assertIn("error", self.rpc("map", {"repository": "arthexis/gway-remote"}))

    def test_backend_exceptions_are_not_leaked(self):
        with patch.object(
            mcp_local,
            "_workspace",
            side_effect=RuntimeError("private workspace path and credential"),
        ):
            result = self.rpc("map", {"repository": "arthexis/gway-remote"})
        self.assertIn("error", result)
        self.assertNotIn("private workspace", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
