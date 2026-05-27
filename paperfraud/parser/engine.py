"""PDF parsing engine — dispatches between PyMuPDF (default) and GROBID (optional)."""

from pathlib import Path

from paperfraud.base import ParsedPaper
from paperfraud.config import Config
from paperfraud.parser.pymupdf_parser import PyMuPDFParser


def parse_pdf(file_path: Path, config: Config) -> ParsedPaper:
    """Parse a PDF into structured sections, tables, and images.

    Uses PyMuPDF by default. If config.grobid_url is set, delegates to GROBID.
    """
    if config.grobid_url:
        from paperfraud.parser.grobid_client import GrobidClient

        client = GrobidClient(config.grobid_url)
        return client.parse(file_path)

    parser = PyMuPDFParser()
    return parser.parse(file_path, skip_images=config.skip_images, max_pages=config.max_pages)
