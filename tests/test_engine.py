import tempfile
import unittest
from pathlib import Path

from star_preparation.engine import prepare
from star_preparation.export import write_run


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "profile_id": "test",
            "source_columns": {
                "source_id": "id", "component": "component", "severity": "priority",
                "arrival_date": "created", "closure_date": "resolved", "status": "status",
                "found_in_version": "version"
            },
            "normalization": {"blank_values": ["", "none"]},
            "inclusion_rules": {"statuses": ["Closed"], "version_policy": {"mode": "allow_list", "accepted_values": ["V1"]}}
        }

    def test_prepares_valid_row_and_logs_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            source.write_text("id,component,priority,created,resolved,status,version\n1,core,High,2026-01-01,2026-01-02,Closed,V1\n2,,Low,2026-01-02,,Open,V1\n", encoding="utf-8")
            result = prepare(source, self.profile)
            self.assertEqual(result["validation_report"]["rows_included"], 1)
            self.assertEqual(result["validation_report"]["rows_excluded"], 1)
            self.assertEqual(result["output_rows"][0]["arrival_date"], "2026-01-01")
            write_run(result, Path(directory) / "run")
            self.assertTrue((Path(directory) / "run" / "star_defects.csv").exists())

    def test_review_required_blocks_approval(self):
        profile = self.profile | {"inclusion_rules": {"statuses": ["Closed"], "version_policy": {"mode": "review_required"}}}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            source.write_text("id,component,priority,created,resolved,status,version\n1,x,High,2026-01-01,,Closed,V1\n", encoding="utf-8")
            result = prepare(source, profile)
            self.assertTrue(result["validation_report"]["approval_required"])
            self.assertEqual(result["decisions"][0]["decision"], "review_required")
            write_run(result, Path(directory) / "run")
            self.assertTrue((Path(directory) / "run" / "proposed_star_defects.csv").exists())
            self.assertFalse((Path(directory) / "run" / "star_defects.csv").exists())
