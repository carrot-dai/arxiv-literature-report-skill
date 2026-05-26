from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import datetime as dt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arxiv_literature_report import core  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "arxiv_literature_report", *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
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
            json_files = list(Path(tmp).glob("**/arxiv_literature_weekly_summary_*.json"))
            self.assertEqual(len(json_files), 1)
            payload = json.loads(json_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["language"], "zh")
            self.assertEqual(payload["report_kind"], "周报")
            self.assertEqual(payload["report_scope"], "weekly_summary")

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
            json_files = list(Path(tmp).glob("**/arxiv_literature_weekly_summary_*.json"))
            self.assertEqual(len(json_files), 1)
            payload = json.loads(json_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["field_name"], "WSe2 superconductivity")
            self.assertEqual(payload["language"], "bilingual")
            self.assertTrue(any("WSe2" in query["query"] for query in payload["queries"]))

    def test_fixed_weekly_report_uses_monday_sunday_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--empty-fixture",
                "--report-kind",
                "weekly",
                "--as-of",
                "2026-05-31",
                "--output-dir",
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report_dir = Path(tmp) / "2026" / "05" / "周报" / "2026-05-25_to_2026-05-31"
            json_path = report_dir / "arxiv_literature_weekly_summary_2026-05-25_to_2026-05-31.json"
            self.assertTrue(json_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["week_start"], "2026-05-25")
            self.assertEqual(payload["week_end"], "2026-05-31")
            self.assertEqual(payload["segment_start"], "2026-05-25")
            self.assertEqual(payload["segment_end"], "2026-05-31")
            self.assertTrue(payload["window_end_exclusive"])

    def test_cross_month_week_writes_segments_and_sunday_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--empty-fixture",
                "--report-kind",
                "weekly",
                "--as-of",
                "2026-07-05",
                "--output-dir",
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            root = Path(tmp)
            june_segment = (
                root
                / "2026"
                / "06"
                / "周报"
                / "2026-06-29_to_2026-07-05"
                / "arxiv_literature_weekly_segment_2026-06-29_to_2026-06-30.json"
            )
            july_segment = (
                root
                / "2026"
                / "07"
                / "周报"
                / "2026-06-29_to_2026-07-05"
                / "arxiv_literature_weekly_segment_2026-07-01_to_2026-07-05.json"
            )
            july_summary = (
                root
                / "2026"
                / "07"
                / "周报"
                / "2026-06-29_to_2026-07-05"
                / "arxiv_literature_weekly_summary_2026-06-29_to_2026-07-05.json"
            )
            june_summary = (
                root
                / "2026"
                / "06"
                / "周报"
                / "2026-06-29_to_2026-07-05"
                / "arxiv_literature_weekly_summary_2026-06-29_to_2026-07-05.json"
            )
            self.assertTrue(june_segment.exists())
            self.assertTrue(july_segment.exists())
            self.assertTrue(july_summary.exists())
            self.assertFalse(june_summary.exists())
            june_payload = json.loads(june_segment.read_text(encoding="utf-8"))
            self.assertEqual(june_payload["report_scope"], "weekly_segment")
            self.assertEqual(june_payload["segment_start"], "2026-06-29")
            self.assertEqual(june_payload["segment_end"], "2026-06-30")

    def test_weekly_report_preserves_seen_state_and_old_weekly_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = Path(tmp) / "2026" / "05" / "周报"
            old_dir.mkdir(parents=True)
            old_file = old_dir / "arxiv_literature_weekly_report_2026-05-30.html"
            old_file.write_text("legacy", encoding="utf-8")
            result = run_cli(
                "--empty-fixture",
                "--report-kind",
                "weekly",
                "--as-of",
                "2026-05-31",
                "--output-dir",
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(old_file.exists())
            self.assertFalse(list(Path(tmp).glob("**/arxiv_literature_seen_ids.json")))

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
        card_html = core.render_paper_card(record, 1, "zh")
        self.assertIn("中文导读", card_html)
        self.assertIn("摘要中文译文", card_html)
        self.assertIn("英文原摘要（核对）", card_html)
        self.assertNotIn("nature-reader", card_html)
        self.assertNotIn("润色英文导读", card_html)
        self.assertIn("重点提炼", core.render_paper_card(record, 1, "zh"))
        self.assertIn("问题：", record["key_takeaways_cn"])
        self.assertIn("结论线索：", record["key_takeaways_cn"])
        self.assertIn("addresses", record["polished_abstract_en"])
        self.assertIn("abstract_translation_cn", record)
        self.assertNotIn("Here we", record["abstract_translation_cn"])

        html = core.render_html(
            [record],
            [],
            dt.datetime(2026, 5, 25, 21, tzinfo=core.CHINA_TZ),
            5,
            language="zh",
        )
        self.assertIn("日期分布", html)
        self.assertIn("更新日期：2026-05-24", html)
        payload = core.serializable_report(
            [record],
            [],
            dt.datetime(2026, 5, 25, 21, tzinfo=core.CHINA_TZ),
            5,
        )
        self.assertEqual(payload["date_counts"], {"2026-05-24": 1})

    def test_daily_history_is_used_for_seen_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            month_dir = Path(tmp) / "2026" / "05"
            daily_dir = month_dir / "日报"
            daily_dir.mkdir(parents=True)
            history = {
                "report_date": "2026-05-25",
                "records": [
                    {
                        "arxiv_id": "2605.00001v1",
                        "base_id": "2605.00001",
                        "title": "Already reported",
                    }
                ],
            }
            (daily_dir / "arxiv_literature_daily_report_2026-05-25.json").write_text(
                json.dumps(history),
                encoding="utf-8",
            )
            state = core.merge_historical_daily_reports(
                {"version": 1, "seen": {}},
                [month_dir],
                "2026-05-26",
            )
            records = [
                {"arxiv_id": "2605.00001v2", "base_id": "2605.00001"},
                {"arxiv_id": "2605.00002v1", "base_id": "2605.00002"},
            ]
            fresh, skipped = core.filter_seen_records(records, state)

            self.assertEqual([item["base_id"] for item in fresh], ["2605.00002"])
            self.assertEqual([item["base_id"] for item in skipped], ["2605.00001"])

    def test_daily_report_hides_seen_publication_updates_by_default(self) -> None:
        record = {
            "arxiv_id": "2605.00001v2",
            "base_id": "2605.00001",
            "abs_url": "https://arxiv.org/abs/2605.00001",
            "pdf_url": "https://arxiv.org/pdf/2605.00001",
            "title": "Already reported paper with DOI",
            "summary": "A previously reported paper now has a DOI.",
            "authors": ["A. Researcher"],
            "updated": "2026-05-26T00:00:00Z",
            "published": "2026-05-25T00:00:00Z",
            "updated_local": "2026-05-26T08:00:00+08:00",
            "published_local": "2026-05-25T08:00:00+08:00",
            "primary_category": "physics.optics",
            "categories": ["physics.optics"],
            "doi": "10.0000/example",
            "journal_ref": "",
            "comment": "",
            "topics": [core.TOPIC_PLASMONICS],
            "matched_queries": ["fixture"],
            "score": 1,
        }
        original_collect = core.collect_records
        try:
            core.collect_records = lambda args, as_of: ([dict(record)], [])  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                seen_path = Path(tmp) / "2026" / "05" / "arxiv_literature_seen_ids.json"
                seen_path.parent.mkdir(parents=True)
                seen_path.write_text(
                    json.dumps({"version": 1, "seen": {"2605.00001": {"first_reported": "2026-05-25"}}}),
                    encoding="utf-8",
                )
                result = core.main(
                    [
                        "--report-kind",
                        "daily",
                        "--as-of",
                        "2026-05-26T09:00:00+08:00",
                        "--output-dir",
                        tmp,
                    ]
                )
                self.assertEqual(result, 0)
                payload_path = (
                    Path(tmp)
                    / "2026"
                    / "05"
                    / "日报"
                    / "arxiv_literature_daily_report_2026-05-26.json"
                )
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["total_records"], 0)
                self.assertEqual(payload["skipped_seen_records"], 1)
                self.assertEqual(payload["publication_updates"], [])

            with tempfile.TemporaryDirectory() as tmp:
                seen_path = Path(tmp) / "2026" / "05" / "arxiv_literature_seen_ids.json"
                seen_path.parent.mkdir(parents=True)
                seen_path.write_text(
                    json.dumps({"version": 1, "seen": {"2605.00001": {"first_reported": "2026-05-25"}}}),
                    encoding="utf-8",
                )
                result = core.main(
                    [
                        "--report-kind",
                        "daily",
                        "--as-of",
                        "2026-05-26T09:00:00+08:00",
                        "--output-dir",
                        tmp,
                        "--include-publication-updates",
                    ]
                )
                self.assertEqual(result, 0)
                payload_path = (
                    Path(tmp)
                    / "2026"
                    / "05"
                    / "日报"
                    / "arxiv_literature_daily_report_2026-05-26.json"
                )
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["total_records"], 0)
                self.assertEqual(len(payload["publication_updates"]), 1)
        finally:
            core.collect_records = original_collect

    def test_oai_records_are_parsed_and_scoped_for_rate_limit_fallback(self) -> None:
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
  xmlns:arXiv="http://arxiv.org/OAI/arXiv/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:2605.12345</identifier>
        <datestamp>2026-05-24</datestamp>
      </header>
      <metadata>
        <arXiv:arXiv>
          <arXiv:id>2605.12345v1</arXiv:id>
          <arXiv:created>2026-05-24</arXiv:created>
          <arXiv:updated>2026-05-24</arXiv:updated>
          <arXiv:authors>
            <arXiv:author>
              <arXiv:forenames>A.</arXiv:forenames>
              <arXiv:keyname>Researcher</arXiv:keyname>
            </arXiv:author>
          </arXiv:authors>
          <arXiv:title>Exciton polaritons in a WS2 photonic crystal cavity</arXiv:title>
          <arXiv:categories>physics.optics cond-mat.mtrl-sci</arXiv:categories>
          <arXiv:abstract>We demonstrate strong coupling and polariton emission in monolayer WS2.</arXiv:abstract>
        </arXiv:arXiv>
      </metadata>
    </record>
  </ListRecords>
</OAI-PMH>
"""
        records, token = core.parse_oai_entries(
            xml_text,
            dt.datetime(2026, 5, 20, tzinfo=core.UTC),
            dt.datetime(2026, 5, 25, tzinfo=core.UTC),
        )

        self.assertIsNone(token)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "arxiv-oai-fallback")
        scoped_topics = core.oai_scope_topics(records[0])
        self.assertIn(core.TOPIC_EXCITON, scoped_topics)
        self.assertIn(core.TOPIC_TMD, scoped_topics)


if __name__ == "__main__":
    unittest.main()
