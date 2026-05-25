from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arxiv_literature_report import core  # noqa: E402


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

    def test_plasmonic_records_get_reader_style_summary_fields(self) -> None:
        record = {
            "arxiv_id": "2605.00001v1",
            "base_id": "2605.00001",
            "abs_url": "https://arxiv.org/abs/2605.00001",
            "pdf_url": "https://arxiv.org/pdf/2605.00001",
            "title": "Plasmonic nanocavity control of exciton polaritons",
            "summary": (
                "Plasmonic nanocavities can confine optical fields below the diffraction limit. "
                "Here we demonstrate strong coupling between excitons and localized surface plasmons. "
                "The results provide a route toward integrated light-matter devices."
            ),
            "authors": ["A. Researcher"],
            "updated_local": "2026-05-24T21:00:00+08:00",
            "primary_category": "physics.optics",
            "categories": ["physics.optics"],
            "doi": "",
            "comment": "",
            "topics": [],
            "matched_queries": [],
        }
        record["topics"] = core.infer_topics(record)
        record.update(core.build_cn_fields(record))

        self.assertIn(core.TOPIC_PLASMONICS, record["topics"])
        self.assertIn(core.TOPIC_PHOTONIC_CRYSTAL, record["topics"])
        self.assertEqual(record["full_abstract_en"], record["summary"])
        self.assertIn("润色英文导读", core.render_paper_card(record, 1, "zh"))
        self.assertIn("重点提炼", core.render_paper_card(record, 1, "zh"))


if __name__ == "__main__":
    unittest.main()
