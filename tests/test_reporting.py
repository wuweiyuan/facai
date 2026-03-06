from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.reporting import append_recommendation_output_log


class TestReporting(TestCase):
    def test_append_recommendation_output_log_groups_by_signal_date(self):
        with TemporaryDirectory() as tmp:
            template = f"{tmp}/{{signal_date}}.log"
            saved = append_recommendation_output_log("line-a\n", date(2026, 3, 3), template)
            append_recommendation_output_log("line-b\n", date(2026, 3, 3), template)
            self.assertEqual(saved.name, "20260303.log")
            content = Path(saved).read_text(encoding="utf-8")
            self.assertEqual(content, "line-a\nline-b\n")

