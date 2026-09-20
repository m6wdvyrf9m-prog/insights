from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from insights_app.pdf_parser import parse_insights_pdf

from .helpers import make_sample_pdf


class PDFParserTests(unittest.TestCase):
    def test_extracts_required_fields_from_sample_like_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = make_sample_pdf(Path(tmp) / "sample.pdf")
            parsed = parse_insights_pdf(pdf_path)

        self.assertEqual(parsed["full_name"], "Alex Sample")
        self.assertEqual(parsed["wheel"]["conscious_text"], "24: Directing Motivator (Classic)")
        self.assertEqual(parsed["colour_dynamics"]["Blue"]["score"], 0.20)
        self.assertEqual(parsed["colour_dynamics"]["Green"]["score"], 2.80)
        self.assertEqual(parsed["colour_dynamics"]["Yellow"]["score"], 4.60)
        self.assertEqual(parsed["colour_dynamics"]["Red"]["score"], 5.73)
        self.assertEqual(parsed["colour_dynamics"]["Blue"]["percentage"], 3)
        self.assertEqual(parsed["colour_dynamics"]["Green"]["percentage"], 47)
        self.assertEqual(parsed["colour_dynamics"]["Yellow"]["percentage"], 77)
        self.assertEqual(parsed["colour_dynamics"]["Red"]["percentage"], 95)
        self.assertEqual(len(parsed["sections"]["strengths"]), 5)
        self.assertEqual(len(parsed["sections"]["development_areas"]), 5)
        self.assertTrue(parsed["sections"]["possible_blind_spots_summary"])
        self.assertTrue(parsed["sections"]["opposite_type_summary"])

    @unittest.skipUnless(os.environ.get("INSIGHTS_SAMPLE_PDF"), "Set INSIGHTS_SAMPLE_PDF to validate a real profile PDF.")
    def test_real_sample_pdf_when_provided(self):
        parsed = parse_insights_pdf(os.environ["INSIGHTS_SAMPLE_PDF"])
        self.assertNotEqual(parsed["full_name"], "Unknown participant")
        self.assertTrue(all(parsed["colour_dynamics"][colour]["score"] is not None for colour in ("Blue", "Green", "Yellow", "Red")))
        self.assertIsNotNone(parsed["wheel"]["conscious_position"])


if __name__ == "__main__":
    unittest.main()
