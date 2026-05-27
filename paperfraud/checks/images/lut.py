"""LUT (Look-Up Table) pseudo-color mapping for image forensics.

Applies Fire/Royal/Hot LUT color maps to extracted images, which can
reveal splicing boundaries invisible in grayscale. Spliced regions often
appear as rectangular "stamp" blocks with distinct color boundaries.

Output images are saved alongside the originals for human review.
"""

from __future__ import annotations

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from paperfraud.base import CheckResult


# Color maps available for forensic analysis
LUT_MAPS = {
    "fire": cv2.COLORMAP_HOT,         # Fire/Iron LUT
    "royal": cv2.COLORMAP_TWILIGHT,   # Royal/Dusk-like
    "viridis": cv2.COLORMAP_VIRIDIS,   # Perceptually uniform
    "inferno": cv2.COLORMAP_INFERNO,
    "hsv": cv2.COLORMAP_HSV,
}


def apply_lut(image_path: Path, output_dir: Path, luts: list[str] | None = None) -> list[Path]:
    """Apply LUT color maps to an image and save results.

    Args:
        image_path: Path to source image
        output_dir: Directory to save LUT images
        luts: List of LUT names to apply (default: fire, royal)

    Returns list of output file paths.
    """
    if luts is None:
        luts = ["fire", "royal", "viridis"]

    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []

    output_paths = []
    base_name = image_path.stem

    for lut_name in luts:
        if lut_name not in LUT_MAPS:
            continue

        # Apply LUT — convert grayscale to color via color map
        colored = cv2.applyColorMap(img, LUT_MAPS[lut_name])

        output_path = output_dir / f"{base_name}_{lut_name}.png"
        cv2.imwrite(str(output_path), colored)
        output_paths.append(output_path)

    return output_paths


def run_lut(paper) -> list[CheckResult]:
    """Apply LUT pseudo-color to all extracted images.

    This is a semi-automated check — the tool generates color-mapped images
    for human review. It does NOT automatically flag splicing (that's
    Phase 2 Clone Detection).
    """
    if not paper.image_paths:
        return [
            CheckResult(
                check_id="images.lut",
                check_name="LUT 伪彩映射",
                level="error",
                verdict="未从 PDF 提取到图片，无法执行 LUT 映射",
                needs_human=False,
            )
        ]

    # Use persistent output_dir first, then temp dir, then default
    if paper._output_dir:
        output_dir = Path(paper._output_dir) / "lut_output"
    elif paper._tmp_dir:
        output_dir = Path(paper._tmp_dir) / "lut_output"
    else:
        output_dir = Path("paperfraud_images")
    output_dir.mkdir(parents=True, exist_ok=True)

    total_processed = 0
    for img_path in paper.image_paths:
        outputs = apply_lut(img_path, output_dir)
        total_processed += len(outputs)

    return [
        CheckResult(
            check_id="images.lut",
            check_name="LUT 伪彩映射",
            level="green",
            verdict=f"已对 {len(paper.image_paths)} 张图片批量应用 LUT 伪彩映射，"
            f"输出 {total_processed} 张对比图到 {output_dir}/",
            evidence=[f"输出目录: {output_dir.resolve()}"],
            confidence=0.5,
            needs_human=True,
            human_instruction=f"打开 {output_dir}/ 目录，逐张审阅 *_fire.png 和 *_royal.png 文件。"
            "拼接区域会出现'贴邮票'矩形纯色框。注意：PDF 8×8 JPEG 压缩伪影也会产生方块，"
            "需对比原图确认。重点看 Western Blot 条带和显微照片。",
        )
    ]
