"""Image path resolution for the Streamlit dashboard.

Discovers forensic images from a report directory, with fallbacks:
  1. JSON `images` field (structured paths, relative to report dir)
  2. Directory scan of known subdirectories (lut_output, etc.)
  3. Parsing evidence strings for `→ filename.png` references
"""

from __future__ import annotations

import re
from pathlib import Path


# Known output subdirectories under the report root
KNOWN_SUBDIRS = {
    "images.lut": "lut_output",
    "images.clone_detect": "clone_output",
    "images.ela": "ela_output",
}


def get_output_subdirs(base_dir: Path) -> dict[str, Path]:
    """Return {check_key: path} for all existing output subdirectories."""
    result: dict[str, Path] = {}
    for check_id, subdir_name in KNOWN_SUBDIRS.items():
        subdir = base_dir / subdir_name
        if subdir.is_dir():
            result[check_id] = subdir
    return result


def find_images_for_check(
    report_dir: Path,
    check_id: str,
    report: dict | None = None,
) -> list[Path]:
    """Find all forensic images for a given check.

    Resolution order:
      1. report['images'] JSON field (structured, relative paths)
      2. Scan KNOWN_SUBDIRS under report_dir
      3. Parse evidence strings from the check's evidence list
    """
    # 1. Structured images field in JSON
    if report and "images" in report:
        subdir_name = KNOWN_SUBDIRS.get(check_id)
        if subdir_name:
            image_list = report["images"].get(subdir_name, [])
            if image_list:
                return [report_dir / p for p in image_list if (report_dir / p).exists()]

    # 2. Directory scan
    subdir = get_output_subdirs(report_dir).get(check_id)
    if subdir and subdir.is_dir():
        return sorted(subdir.glob("*.png"))

    # 3. Parse evidence strings (for legacy reports without images field)
    if report:
        for check in report.get("checks", []):
            if check.get("check_id") == check_id:
                evidence = check.get("evidence", [])
                refs = parse_image_refs(evidence)
                result = []
                for name in refs.get("outputs", []):
                    # Search in known subdirectories
                    for subdir_name in KNOWN_SUBDIRS.values():
                        candidate = report_dir / subdir_name / name
                        if candidate.exists():
                            result.append(candidate)
                            break
                return sorted(result)

    return []


def parse_image_refs(evidence: list[str]) -> dict[str, list[str]]:
    """Extract image filename references from evidence strings.

    Returns {"outputs": [name1, name2, ...], "sources": [name1, name2, ...]}

    Patterns:
      ELA:    "[img.png] ... → out_name.png"
      Clone:  "[img.png] 发现 N 对克隆区域 → out_name.png"
      FeatMatch: "[a.png ↔ b.png] ... → out_name.png"
    """
    outputs: list[str] = []
    sources: list[str] = []

    for line in evidence:
        # Extract "→ filename.png" at end of line
        arrow_match = re.search(r"→\s*(\S+\.png)", line)
        if arrow_match:
            outputs.append(arrow_match.group(1))

        # Extract [source.png] references
        bracket_refs = re.findall(r"\[([^\]]+\.(?:png|jpg|jpeg|tiff?))\]", line)
        sources.extend(bracket_refs)

    return {"outputs": outputs, "sources": sources}


def load_image_for_web(img_path: Path, max_width: int = 1920):
    """Load and optionally downsize an image for web display.

    Returns a PIL Image. Downsizes if width exceeds max_width to avoid
    overwhelming the browser with 4000x2000 ORB match previews.
    """
    from PIL import Image

    img = Image.open(img_path)
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    return img
