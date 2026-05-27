"""Paper parsing engine — dispatches between PDF (PyMuPDF / GROBID) and DOCX (python-docx)."""

from pathlib import Path

from paperfraud.base import ParsedPaper
from paperfraud.config import Config
from paperfraud.parser.pymupdf_parser import PyMuPDFParser


def parse_paper(file_path: Path, config: Config) -> ParsedPaper:
    """Parse a paper (PDF or DOCX) into structured sections, tables, and images.

    Dispatches to PyMuPDF for .pdf, python-docx for .docx.
    If config.grobid_url is set, delegates PDF parsing to GROBID.
    """
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        if config.grobid_url:
            from paperfraud.parser.grobid_client import GrobidClient
            client = GrobidClient(config.grobid_url)
            return client.parse(file_path)
        parser = PyMuPDFParser()
        return parser.parse(file_path, skip_images=config.skip_images, max_pages=config.max_pages)

    if suffix == ".docx":
        try:
            from paperfraud.parser.docx_parser import DocxParser
        except ImportError:
            raise ImportError(
                "Word (.docx) support requires python-docx. "
                "Install with: pip install 'paperfraud-detect[docx]'"
            )
        parser = DocxParser()
        return parser.parse(file_path, skip_images=config.skip_images)

    raise ValueError(f"不支持的文件格式: {suffix}。支持的格式: .pdf, .docx")


# Backward-compatible alias
parse_pdf = parse_paper
