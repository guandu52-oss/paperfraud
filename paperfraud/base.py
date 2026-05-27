"""Core dataclasses for paper fraud detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class SourceLocation:
    """Pinpoints where evidence was found in the paper."""
    page: int
    figure_number: str | None = None
    paragraph: str | None = None
    excerpt: str | None = None
    screenshot_path: str | None = None


CheckLevel = Literal["red", "orange", "yellow", "green", "error"]


@dataclass
class CheckResult:
    """Unified output from any detection module."""
    check_id: str
    check_name: str
    level: CheckLevel
    verdict: str
    evidence: list[str] = field(default_factory=list)
    source_locations: list[SourceLocation] = field(default_factory=list)
    confidence: float = 1.0
    needs_human: bool = False
    human_instruction: str = ""

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "check_name": self.check_name,
            "level": self.level,
            "verdict": self.verdict,
            "evidence": self.evidence,
            "source_locations": [
                {
                    "page": sl.page,
                    "figure_number": sl.figure_number,
                    "paragraph": sl.paragraph,
                    "excerpt": sl.excerpt,
                }
                for sl in self.source_locations
            ],
            "confidence": self.confidence,
            "needs_human": self.needs_human,
            "human_instruction": self.human_instruction,
        }


@dataclass
class ParsedPaper:
    """Structured output from PDF parser.

    IMPORTANT: After parsing, all fields are read-only. Check functions
    MUST NOT mutate ParsedPaper fields — this ensures ThreadPoolExecutor
    safety without locks. Python strings are immutable; image_paths is
    only appended during parsing and only read during checks.
    """
    file_path: Path | None = None
    doi: str | None = None
    title: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    year: int | None = None

    # Sectioned text
    abstract: str = ""
    introduction: str = ""
    methods: str = ""
    results: str = ""
    discussion: str = ""
    full_text: str = ""

    # Extracted data
    tables: list[dict] = field(default_factory=list)
    image_paths: list[Path] = field(default_factory=list)
    # Text block coordinates per image stem, in PDF points relative to image origin.
    # e.g. {"page2_1": [{"x0": 10, "y0": 20, "x1": 100, "y1": 30}, ...]}
    image_text_boxes: dict[str, list[dict]] = field(default_factory=dict)

    # External data file (CSV) for Benford / bulk numeric checks
    data_file: str = ""

    # Internal: temp directory for extracted images (cleaned up after checks)
    _tmp_dir: str | None = None

    # Persistent output directory (set by --output-dir; images + report saved here)
    _output_dir: str | None = None

    # Metadata
    metadata: dict = field(default_factory=dict)
