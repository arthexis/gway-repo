"""Hermetic immutable provider staging without network or privileged writes."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImmutableProviderDeploymentTests(unittest.TestCase):
    def test_exact_sha_stage_and_repeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            checkout = workspace / "checkout"
            checkout.mkdir()
            shutil.copytree(
                ROOT / "src",
                checkout / "src",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            shutil.copy2(ROOT / "deploy.sh", checkout / "deploy.sh")
            subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
            subprocess.run(
                ["git", "config", "user.email", "ci@example.invalid"],
                cwd=checkout,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "CI"],
                cwd=checkout,
                check=True,
            )
            subprocess.run(["git", "add", "."], cwd=checkout, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "fixture"], cwd=checkout, check=True
            )
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=checkout,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            home = workspace / "home"
            home.mkdir()
            env = {**os.environ, "HOME": str(home)}
            command = ["bash", str(checkout / "deploy.sh"), "--expected-sha", sha]
            first = subprocess.run(
                command, env=env, check=True, text=True, capture_output=True
            )
            self.assertEqual(json.loads(first.stdout)["state"], "staged")
            artifact = (
                home / ".local/share/gway-repo/releases" / sha / "bin/gway-repo-mcp"
            )
            self.assertTrue(artifact.is_file())
            self.assertFalse(artifact.is_symlink())
            second = subprocess.run(
                command, env=env, check=True, text=True, capture_output=True
            )
            self.assertEqual(json.loads(second.stdout)["state"], "unchanged")
            invalid = subprocess.run(
                command[:-1] + ["0" * 40],
                env=env,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(invalid.returncode, 0)


if __name__ == "__main__":
    unittest.main()
