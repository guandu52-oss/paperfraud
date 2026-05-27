"""paperfraud-detect — Automated academic paper fraud detection.

Multi-step agent architecture: rule engine + statistical analysis + LLM review.

Quick start:
    from paperfraud.parser.engine import parse_paper
    from paperfraud.config import Config
    from pathlib import Path

    paper = parse_paper(Path("paper.pdf"), Config())
    print(paper.title, len(paper.full_text))
"""

__version__ = "0.2.0"
