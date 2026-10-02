from __future__ import annotations

import unittest

from utils.formatting import format_amount


class FormatAmountTests(unittest.TestCase):
    def test_formats_numeric_and_us_grouped_values(self):
        self.assertEqual(format_amount(1250000), "1,250,000")
        self.assertEqual(format_amount("1,250,000"), "1,250,000")

    def test_formats_indonesian_grouping_and_decimal(self):
        self.assertEqual(format_amount("1.250.000"), "1,250,000")
        self.assertEqual(format_amount("1.250.000,50"), "1,250,000.50")

    def test_preserves_non_numeric_values_without_raising(self):
        self.assertEqual(format_amount("not available"), "not available")
        self.assertEqual(format_amount(None), "0")


if __name__ == "__main__":
    unittest.main()