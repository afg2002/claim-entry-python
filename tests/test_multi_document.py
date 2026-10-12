from __future__ import annotations

import unittest

from services.multi_document import merge_document_markdown


class MergeDocumentMarkdownTests(unittest.TestCase):
    def test_rebases_page_markers_and_preserves_source_page_map(self):
        merged, pages = merge_document_markdown([
            {
                "filename": "claim one.pdf",
                "markdown": "<!-- page: 1 -->\nFirst page\n<!-- page: 2 -->\nSecond page",
                "pages": [
                    {"page_number": 1, "raw_text": "First page"},
                    {"page_number": 2, "raw_text": "Second page"},
                ],
            },
            {
                "filename": "claim two.pdf",
                "markdown": "## Page 1\nThird page",
                "pages": [{"page_number": 1, "raw_text": "Third page"}],
            },
        ])

        self.assertIn("## Source document: claim one.pdf", merged)
        self.assertIn("## Source document: claim two.pdf", merged)
        self.assertIn("<!-- page: 1 -->", merged)
        self.assertIn("<!-- page: 2 -->", merged)
        self.assertIn("## Page 3", merged)
        self.assertIn("First page", merged)
        self.assertIn("Third page", merged)
        self.assertEqual(
            [
                (item["page_number"], item["source_file"], item["source_page"])
                for item in pages
            ],
            [
                (1, "claim one.pdf", 1),
                (2, "claim one.pdf", 2),
                (3, "claim two.pdf", 1),
            ],
        )

    def test_adds_a_source_page_marker_when_markdown_has_none(self):
        merged, pages = merge_document_markdown([
            {"filename": "plain.txt", "markdown": "Plain content"},
        ])

        self.assertIn("## Source document: plain.txt", merged)
        self.assertIn("<!-- page: 1 -->", merged)
        self.assertIn("Plain content", merged)
        self.assertEqual(pages[0]["source_file"], "plain.txt")
        self.assertEqual(pages[0]["page_number"], 1)


if __name__ == "__main__":
    unittest.main()