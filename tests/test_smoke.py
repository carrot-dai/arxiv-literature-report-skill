from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "arxiv_literature_report", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class SmokeTests(unittest.TestCase):
    def test_empty_weekly_report_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--empty-fixture",
                "--report-kind",
                "weekly",
                "--language",
                "zh",
                "--output-dir",
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            json_files = list(Path(tmp).glob("**/arxiv_literature_weekly_report_*.json"))
            self.assertEqual(len(json_files), 1)
            payload = json.loads(json_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["language"], "zh")
            self.assertEqual(payload["report_kind"], "周报")

    def test_config_profile_applies_custom_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--config",
                "examples/config.example.json",
                "--profile",
                "wse2_superconductivity",
                "--empty-fixture",
                "--output-dir",
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            json_files = list(Path(tmp).glob("**/arxiv_literature_weekly_report_*.json"))
            self.assertEqual(len(json_files), 1)
            payload = json.loads(json_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["field_name"], "WSe2 superconductivity")
            self.assertEqual(payload["language"], "bilingual")
            self.assertTrue(any("WSe2" in query["query"] for query in payload["queries"]))

    def test_tracking_profile_writes_group_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--config",
                "examples/config.example.json",
                "--profile",
                "mak_shan_tracking",
                "--track-group",
                "Mak-Shan通讯团队",
                "--empty-fixture",
                "--output-dir",
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            group_dir = Path(tmp) / "group_tracking" / "Mak-Shan通讯团队"
            self.assertTrue((group_dir / "tracked_topics.json").exists())
            self.assertTrue((group_dir / "timeline.md").exists())


if __name__ == "__main__":
    unittest.main()

