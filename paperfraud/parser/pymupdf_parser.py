"""PyMuPDF-based PDF parser with heuristic section segmentation.

Extracts: full text, sectioned text (Abstract/Methods/Results/Discussion),
embedded images, and basic table data.
"""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from paperfraud.base import ParsedPaper


class PyMuPDFParser:
    """Parse a PDF using PyMuPDF + regex heuristics.

    Accuracy ~60% for section segmentation, adequate for extracting
    statistical values, P-values, and numerical tables.
    """

    SECTION_PATTERNS: list[tuple[str, str]] = [
        ("abstract", r"(?i)\babstract\b"),
        ("introduction", r"(?i)\bintroduction\b"),
        ("methods", r"(?i)\b(materials?\s*(?:and|&)\s*methods?|methods?|experimental\s+procedures?)\b"),
        ("results", r"(?i)\bresults?\b"),
        ("discussion", r"(?i)\bdiscussion\b"),
        ("references", r"(?i)\b(references?|bibliography)\b"),
    ]

    def parse(self, file_path: Path, skip_images: bool = True, max_pages: int = 0) -> ParsedPaper:
        doc = fitz.open(str(file_path))
        total_pages = doc.page_count
        effective_pages = min(total_pages, max_pages) if max_pages > 0 else total_pages
        paper = ParsedPaper(file_path=file_path)

        full_text_parts: list[str] = []
        image_dir: Path | None = None

        if not skip_images:
            image_dir = Path(tempfile.mkdtemp(prefix="paperfraud_"))
            paper._tmp_dir = str(image_dir)
            atexit.register(shutil.rmtree, str(image_dir), ignore_errors=True)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            task = progress.add_task(
                f"[cyan]解析 PDF ({effective_pages}/{total_pages} 页)...",
                total=effective_pages,
            )

            image_count = 0
            for page_num in range(effective_pages):
                page = doc[page_num]
                text = page.get_text("text")
                full_text_parts.append(text)

                if image_dir is not None:
                    # 1. Collect embedded raster image rects.
                    #    Filter:
                    #      - Tiny < 50pt (logos, icons, page decorations)
                    #      - Wide+short strips with aspect > 5:1 and height < 150pt
                    #        (chapter titles, decorative text rendered as bitmap)
                    img_rects = []
                    for img in page.get_images(full=True):
                        xref = img[0]
                        for r in page.get_image_rects(xref):
                            if r.width < 50 or r.height < 50:
                                continue
                            # Skip text-as-image strips (e.g. chapter headings
                            # with special fonts rendered as bitmaps).  Real
                            # figures are rarely wider than 5:1 at < 150pt tall.
                            if r.width / max(r.height, 1) > 5 and r.height < 150:
                                continue
                            img_rects.append(r)

                    # 2. Collect vector art bounding box from filled drawings.
                    #    Many figures are pure vector (charts, diagrams) with no
                    #    embedded raster images. We union ALL filled paths into one
                    #    "vector figure" rect and add it to the clustering pool.
                    drawings = page.get_drawings()
                    filled = [fitz.Rect(d["rect"]) for d in drawings
                              if d.get("fill")]
                    if filled:
                        vector_rect = filled[0]
                        for r in filled[1:]:
                            vector_rect |= r
                        if vector_rect.width >= 100 and vector_rect.height >= 100:
                            img_rects.append(vector_rect)

                    if not img_rects:
                        continue

                    # 3. Spatial clustering — merge only nearby rects (connected-component
                    #    on an overlap graph with 50pt tolerance). This prevents logos and
                    #    distant figures from being merged into one giant bounding box.
                    TOLERANCE = 50
                    changed = True
                    while changed:
                        changed = False
                        merged_rects = []
                        while img_rects:
                            r = img_rects.pop(0)
                            r_expand = fitz.Rect(
                                r.x0 - TOLERANCE, r.y0 - TOLERANCE,
                                r.x1 + TOLERANCE, r.y1 + TOLERANCE,
                            )
                            match_idx = -1
                            for i, existing_r in enumerate(merged_rects):
                                e_expand = fitz.Rect(
                                    existing_r.x0 - TOLERANCE, existing_r.y0 - TOLERANCE,
                                    existing_r.x1 + TOLERANCE, existing_r.y1 + TOLERANCE,
                                )
                                if r_expand.intersects(e_expand):
                                    match_idx = i
                                    break
                            if match_idx != -1:
                                merged_rects[match_idx] |= r
                                changed = True
                            else:
                                merged_rects.append(r)
                        img_rects = merged_rects

                    # 4. Render each cluster as an independent figure image
                    page_dict = page.get_text("dict")
                    for i, cluster_rect in enumerate(img_rects):
                        margin = 30
                        cluster_rect.x0 = max(0, cluster_rect.x0 - margin)
                        cluster_rect.y0 = max(0, cluster_rect.y0 - margin)
                        cluster_rect.x1 = min(page.rect.width, cluster_rect.x1 + margin)
                        cluster_rect.y1 = min(page.rect.height, cluster_rect.y1 + margin)

                        # If the cluster covers most of the page (>50% area),
                        # render the full page to capture text labels at edges.
                        page_area = page.rect.width * page.rect.height
                        if (cluster_rect.width * cluster_rect.height) > page_area * 0.5:
                            cluster_rect = page.rect

                        # 5. Name strictly by page number: page{N}_{M}.png
                        final_fig_name = f"page{page_num + 1}_{i + 1}"
                        fig_path = image_dir / f"{final_fig_name}.png"

                        counter = 1
                        while fig_path.exists():
                            fig_path = image_dir / f"page{page_num + 1}_{i + 1}_{counter}.png"
                            counter += 1

                        # 6. Render at 300 DPI and save
                        fig_pix = page.get_pixmap(dpi=300, clip=cluster_rect)
                        fig_pix.save(str(fig_path))

                        # 7. Extract text block coordinates within this cluster
                        #    (relative to cluster origin, in PDF points) for
                        #    downstream text masking during ORB/ELA/LUT checks.
                        text_boxes: list[dict] = []
                        for block in page_dict.get("blocks", []):
                            if block.get("type") != 0:
                                continue
                            b = block["bbox"]
                            block_rect = fitz.Rect(b[0], b[1], b[2], b[3])
                            if not cluster_rect.intersects(block_rect):
                                continue
                            # Clip to cluster rect and make coordinates relative
                            clipped = block_rect & cluster_rect
                            text_boxes.append({
                                "x0": clipped.x0 - cluster_rect.x0,
                                "y0": clipped.y0 - cluster_rect.y0,
                                "x1": clipped.x1 - cluster_rect.x0,
                                "y1": clipped.y1 - cluster_rect.y0,
                            })
                        paper.image_text_boxes[final_fig_name] = text_boxes

                        paper.image_paths.append(fig_path)
                        image_count += 1

                progress.update(task, advance=1,
                    description=f"[cyan]解析 PDF 第 {page_num + 1}/{effective_pages} 页..." +
                    (f" (图片: {image_count})" if image_dir is not None else ""))

        paper.full_text = "\n".join(full_text_parts)
        doc.close()

        self._segment_sections(paper)
        self._extract_metadata(paper, file_path)
        return paper

    def _segment_sections(self, paper: ParsedPaper) -> None:
        """Heuristically split full_text into IMRaD sections."""
        text = paper.full_text

        boundaries: list[tuple[int, str]] = []
        for section_name, pattern in self.SECTION_PATTERNS:
            match = re.search(pattern, text)
            if match:
                boundaries.append((match.start(), section_name))

        boundaries.sort()

        for i, (start, name) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            section_text = text[start:end].strip()
            setattr(paper, name, section_text)

        if not paper.abstract and paper.introduction:
            intro_start = text.find(paper.introduction[:50])
            if intro_start > 100:
                paper.abstract = text[:intro_start].strip()[-2000:]

        # Fallback: papers without "Abstract" or "Introduction" headings
        # (e.g. Nature journals). Take first substantial paragraph after metadata.
        if not paper.abstract:
            abstract_end = boundaries[0][0] if boundaries else len(text)
            # Find the first long paragraph (100+ chars contiguous, no double-newline)
            body_start = None
            paragraphs = text[:abstract_end].split("\n\n")
            for para in paragraphs:
                stripped = para.strip()
                if len(stripped) > 150:
                    body_start = text.find(stripped)
                    break
            if body_start is None:
                body_start = min(400, abstract_end // 2)
            abstract_text = text[body_start:abstract_end].strip()
            if len(abstract_text) > 200:
                paper.abstract = abstract_text[:3000]

    def _extract_metadata(self, paper: ParsedPaper, file_path: Path) -> None:
        try:
            doc = fitz.open(str(file_path))
            meta = doc.metadata
            if meta:
                paper.title = meta.get("title", "")
                if meta.get("author"):
                    paper.authors = [a.strip() for a in meta["author"].split(";")]
            doc.close()
        except Exception:
            pass
