from __future__ import annotations

import unittest

from docling.backend.image_backend import ImageDocumentBackend
from docling.datamodel.pipeline_options import VlmPipelineOptions
from docling.document_converter import PdfFormatOption
from docling.pipeline.vlm_pipeline import VlmPipeline

from parsers.docling_parser import _vlm_image_format_option


class DoclingImageVlmInputTests(unittest.TestCase):
    def test_vlm_image_format_uses_supported_native_image_backend(self):
        options = VlmPipelineOptions()

        image_format = _vlm_image_format_option(options)

        self.assertIsInstance(image_format, PdfFormatOption)
        self.assertIs(image_format.pipeline_cls, VlmPipeline)
        self.assertIs(image_format.backend, ImageDocumentBackend)
        self.assertIs(image_format.pipeline_options, options)


if __name__ == "__main__":
    unittest.main()